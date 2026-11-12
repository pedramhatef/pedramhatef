"""Download CliqueAI validator logs from the public Weights & Biases project (no API key needed).

Every validator that logs writes one history row per problem: the graph plus every queried miner's answer and reward
(schema: CliqueAI docs/wandb_logging.md). We request only the keys we need (not the 1 MB adjacency_list).

    python -m analysis.wandb_logs --steps 4000 --out data/
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import json
import os
import time
import urllib.request

ENTITY, PROJECT = "toptensor-ai", "CliqueAI"
API = "https://api.wandb.ai/graphql"
KEYS = ["_step", "_timestamp", "uuid", "number_of_nodes", "time_limit", "difficulty", "encoded_matrix", "miner_uids",
        "miner_hotkeys", "miner_coldkeys", "miner_ans", "miner_rewards", "miner_diversity", "miner_optimality"]


def gql(query: str, variables: dict | None = None, timeout: float = 120.0) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json", "User-Agent": "cliqueminer-analysis"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if out.get("errors"):
        raise RuntimeError(str(out["errors"])[:300])
    return out["data"]


def list_runs() -> list[dict]:
    q = ('query { project(name: "%s", entityName: "%s") { runs(first: 200, order: "-heartbeat_at") '
         '{ edges { node { name state createdAt heartbeatAt historyLineCount } } } } }' % (PROJECT, ENTITY))
    return [e["node"] for e in gql(q)["project"]["runs"]["edges"]]


def _fetch_window(run: str, lo: int, hi: int, retries: int = 6) -> list[dict]:
    spec = json.dumps({"keys": KEYS, "samples": hi - lo + 5, "minStep": lo, "maxStep": hi})
    q = ('query($spec: [JSONString!]!) { project(name: "%s", entityName: "%s") { run(name: "%s") '
         '{ sampledHistory(specs: $spec) } } }' % (PROJECT, ENTITY, run))
    err: Exception | None = None
    for attempt in range(retries):
        try:
            return gql(q, {"spec": [spec]})["project"]["run"]["sampledHistory"][0]
        except Exception as e:           # noqa: BLE001 - network hiccups: back off and retry
            err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"window {lo}-{hi} of {run} failed: {err}")


def fetch_run(run: str, last_step: int, n_steps: int, out_path: str, window: int = 40, workers: int = 4) -> int:
    start = max(0, last_step - n_steps + 1)
    wins = [(lo, min(lo + window, last_step + 1)) for lo in range(start, last_step + 1, window)]
    rows: dict[int, dict] = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        for got in ex.map(lambda w: _fetch_window(run, *w), wins):
            for row in got:
                rows[row["_step"]] = row
    with gzip.open(out_path, "wt") as f:
        for step in sorted(rows):
            f.write(json.dumps(rows[step]) + "\n")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", type=int, default=4000, help="most recent problems to fetch per validator (~1 per 75 s)")
    ap.add_argument("--out", default="data")
    ap.add_argument("--runs", nargs="*", help="run names; default: every run whose state is 'running'")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    runs = list_runs()
    chosen = [r for r in runs if (r["name"] in a.runs if a.runs else r["state"] == "running")]
    if not chosen:
        raise SystemExit("no matching runs; available: " + ", ".join(f"{r['name']}({r['state']})" for r in runs[:10]))
    for r in chosen:
        path = os.path.join(a.out, f"{r['name']}.jsonl.gz")
        n = fetch_run(r["name"], r["historyLineCount"] - 1, a.steps, path)
        print(f"{r['name']}: {n} problems -> {path}")


if __name__ == "__main__":
    main()
