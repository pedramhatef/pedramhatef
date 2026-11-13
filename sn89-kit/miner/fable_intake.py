#!/usr/bin/env python3
"""fable_intake.py — gatekeeper between your model and the SN89 miner's local intake.

Your engine (Fable, Python or TypeScript) calls this instead of POSTing to the
miner directly. It refuses a call unless it passes YOUR gates, then POSTs to the
miner's `serve` endpoint (127.0.0.1:8089) and appends every attempt and receipt
to a JSONL ledger, which is your evidence if a call is ever misgraded or forfeited.

Gates, checked in this order (all local; the miner re-checks quota/gap itself):
  1. pair on your allowlist, direction LONG/SHORT
  2. calibrated probability >= --min-prob (P(TP first | decisive) for SN89's band)
  3. at most 3 accepted calls per UTC day (SN89 voids the 4th)
  4. >= 61 minutes since your last accepted call (SN89 voids < ~3570 s)
  5. no open call on the same pair+direction inside its grade window
     (crypto/equities/indices/WTI 8 h, forex/metals 12 h)

Only {trade_pair, direction, comment} is sent: TP, SL and horizon are fixed by
the subnet's board. The comment is revealed PUBLICLY 15 minutes after commit, so
the default is empty. The owner can read every call immediately (their key wrap
is in every blob): only send calls you are willing to publish.

Usage:
  python fable_intake.py submit --pair BTCUSD --direction LONG --prob 0.68
  python fable_intake.py submit --pair ETHUSD --direction SHORT --prob 0.66 --dry-run
  python fable_intake.py status            # today's quota, gap, open calls
From TypeScript: spawn the same command and read the JSON on stdout.
Exit codes: 0 committed (or dry-run pass), 2 refused by a gate, 3 miner/HTTP error.

Stdlib only; Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8089/submit"
DEFAULT_ENV = os.path.expanduser("~/.sn89.env")
DEFAULT_LEDGER = os.path.expanduser("~/.sn89/fable_ledger.jsonl")
DEFAULT_PAIRS = "BTCUSD,ETHUSD,SOLUSD,XRPUSD"
MAX_PER_UTC_DAY = 3
MIN_GAP_S = 61 * 60
HORIZON_H = {"metals": 12, "forex": 12}  # everything else on the LF board: 8 h
FX_METALS = {"XAUUSD": "metals", "XAGUSD": "metals",
             "AUDUSD": "forex", "EURUSD": "forex", "USDCAD": "forex"}


def load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def read_ledger(path: str) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return rows


def append_ledger(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def accepted(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("result") == "committed"]


def horizon_s(pair: str) -> int:
    return HORIZON_H.get(FX_METALS.get(pair, ""), 8) * 3600


def check_gates(pair: str, direction: str, prob: float | None, *, min_prob: float,
                allow: set[str], rows: list[dict], now: float) -> str | None:
    """Return a refusal reason, or None if the call may go out."""
    if pair not in allow:
        return f"pair {pair} not in allowlist {sorted(allow)}"
    if direction not in ("LONG", "SHORT"):
        return f"direction must be LONG or SHORT, got {direction!r}"
    if prob is None or prob < min_prob:
        return f"prob {prob} below gate {min_prob}"
    acc = accepted(rows)
    day = int(now // 86400)
    today = [r for r in acc if int(r["t"] // 86400) == day]
    if len(today) >= MAX_PER_UTC_DAY:
        return f"daily cap reached ({MAX_PER_UTC_DAY}/UTC day); resets 00:00 UTC"
    if acc:
        since = now - max(r["t"] for r in acc)
        if since < MIN_GAP_S:
            return f"gap {since/60:.0f} min < {MIN_GAP_S//60} min since last accepted call"
    for r in acc:
        if r["pair"] == pair and r["direction"] == direction and now - r["t"] < horizon_s(pair):
            return f"already holding an open {pair} {direction} call (committed {(now - r['t'])/3600:.1f} h ago)"
    return None


def post(url: str, token: str, body: dict, timeout: float = 120.0) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            return e.code, {"error": str(e)}


def cmd_submit(a) -> int:
    env = load_env(a.env)
    token = os.getenv("SN89_INTAKE_TOKEN") or env.get("SN89_INTAKE_TOKEN", "")
    pair, direction = a.pair.upper(), a.direction.upper()
    allow = {p.strip().upper() for p in a.pairs.split(",") if p.strip()}
    now = time.time()
    rows = read_ledger(a.ledger)
    base = {"t": now, "pair": pair, "direction": direction, "prob": a.prob,
            "model": a.model}

    why = check_gates(pair, direction, a.prob, min_prob=a.min_prob, allow=allow,
                      rows=rows, now=now)
    if why:
        append_ledger(a.ledger, {**base, "result": "refused", "reason": why})
        print(json.dumps({"ok": False, "refused": why}))
        return 2
    if a.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "would_send":
                          {"trade_pair": pair, "direction": direction, "comment": a.comment}}))
        return 0

    code, res = post(a.url, token, {"trade_pair": pair, "direction": direction,
                                    "comment": a.comment})
    if code == 200 and res.get("ok"):
        append_ledger(a.ledger, {**base, "result": "committed", "http": code, "receipt": res})
        print(json.dumps({"ok": True, "receipt": res}))
        return 0
    append_ledger(a.ledger, {**base, "result": "error", "http": code, "response": res})
    print(json.dumps({"ok": False, "http": code, "response": res}))
    if code == 401:
        print("401: SN89_INTAKE_TOKEN here differs from the one serve was started with "
              f"(source {a.env} in both shells).", file=sys.stderr)
    return 3


def cmd_status(a) -> int:
    now = time.time()
    acc = accepted(read_ledger(a.ledger))
    day = int(now // 86400)
    today = [r for r in acc if int(r["t"] // 86400) == day]
    last = max((r["t"] for r in acc), default=None)
    open_calls = [f'{r["pair"]} {r["direction"]} ({(now - r["t"])/3600:.1f} h)'
                  for r in acc if now - r["t"] < horizon_s(r["pair"])]
    out = {
        "accepted_today": len(today),
        "remaining_today": max(0, MAX_PER_UTC_DAY - len(today)),
        "next_allowed_in_min": (max(0, (MIN_GAP_S - (now - last)) / 60) if last else 0),
        "open_calls": open_calls,
        "lifetime_committed": len(acc),
    }
    print(json.dumps(out, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--url", default=os.getenv("SN89_INTAKE_URL", DEFAULT_URL))
    p.add_argument("--env", default=DEFAULT_ENV)
    p.add_argument("--ledger", default=os.getenv("SN89_FABLE_LEDGER", DEFAULT_LEDGER))
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit", help="gate and send one call")
    s.add_argument("--pair", required=True)
    s.add_argument("--direction", required=True)
    s.add_argument("--prob", type=float, required=True,
                   help="calibrated P(TP first | decisive) on SN89's band")
    s.add_argument("--min-prob", type=float, default=float(os.getenv("FABLE_MIN_PROB", "0.65")))
    s.add_argument("--pairs", default=os.getenv("FABLE_PAIRS", DEFAULT_PAIRS),
                   help="comma-separated allowlist")
    s.add_argument("--comment", default="", help="revealed publicly; keep empty")
    s.add_argument("--model", default="fable", help="recorded locally only")
    s.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="today's quota, gap and open calls")
    a = p.parse_args(argv)
    return cmd_submit(a) if a.cmd == "submit" else cmd_status(a)


if __name__ == "__main__":
    sys.exit(main())
