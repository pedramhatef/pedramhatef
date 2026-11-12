"""Turn raw W&B rows into validated problems, and re-implement the validator's scoring exactly.

`score()` mirrors CliqueAI/scoring/clique_scoring.py (v0.0.17); `Problem.logged_rewards` lets you confirm it reproduces
what the validator logged (see tests/test_scoring.py and `python -m analysis.dataset --check data/*.jsonl.gz`).
"""
from __future__ import annotations

import collections
import glob
import gzip
import json
import multiprocessing as mp
import os
import pickle
import sys
from dataclasses import dataclass, field

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cliqueminer import codec  # noqa: E402


@dataclass
class Problem:
    validator: str                    # W&B run name (starts with the validator hotkey)
    step: int
    ts: float
    uuid: str
    n: int
    time_limit: float
    d: float                          # difficulty
    density: float
    uids: list
    hotkeys: list
    coldkeys: list
    answers: list                     # sorted tuples, () when the miner sent nothing usable
    ok: list                          # valid AND maximal, as the validator checks it
    logged_rewards: list
    encoded: str = field(repr=False, default="")

    @property
    def best(self) -> int:
        return max((len(a) for a, o in zip(self.answers, self.ok) if o), default=0)

    @property
    def frac_at_best(self) -> float:
        b = self.best
        return sum(1 for a, o in zip(self.answers, self.ok) if o and len(a) == b) / max(1, len(self.answers))

    def adjacency(self) -> np.ndarray:
        return codec.decode_matrix(self.encoded)


def score(answers, ok, d: float):
    """(omega_n, delta_n, reward) arrays for a pool of answers. ok[i] = answer i is a valid maximal clique."""
    ok = np.asarray(ok, dtype=bool)
    size = np.array([len(a) for a in answers]) * ok
    zeros = np.zeros(len(answers))
    if len(size) == 0 or size.max() <= 0:
        return zeros, zeros, zeros
    rel = size / size.max()
    pr = np.array([(size > s).sum() / len(size) for s in size])
    omega = np.where(ok, np.exp(-pr / np.where(rel > 0, rel, 1.0)), 0.0)
    omega = omega / omega.max()
    counts = collections.Counter(answers)
    delta = ok * np.array([1.0 / counts[a] for a in answers])
    if delta.max() > 0:
        delta = delta / delta.max()
    return omega, delta, omega * (1.0 + d) + delta


def reward_if_added(p: Problem, answer, answer_ok: bool = True):
    """(reward, omega_n, delta_n) a NEW miner would have received on problem p with `answer`, others held fixed."""
    om, dl, rw = score(list(p.answers) + [tuple(sorted(answer))], list(p.ok) + [bool(answer_ok)], p.d)
    return float(rw[-1]), float(om[-1]), float(dl[-1])


def _prepare_row(args):
    validator, line = args
    x = json.loads(line)
    try:
        adj = codec.decode_matrix(x["encoded_matrix"])
    except (ValueError, KeyError, TypeError):
        return None
    n = adj.shape[0]
    answers, ok = [], []
    for a in x.get("miner_ans") or []:
        good = isinstance(a, list) and codec.is_maximal_clique(adj, a)
        answers.append(tuple(sorted(a)) if isinstance(a, list) and all(isinstance(v, int) for v in a) else ())
        ok.append(bool(good))
    m = len(answers)
    if m == 0 or len(x.get("miner_uids") or []) != m:
        return None
    return Problem(validator=validator, step=int(x["_step"]), ts=float(x.get("_timestamp") or 0), uuid=str(x.get("uuid", "")), n=n,
                   time_limit=float(x.get("time_limit") or 0), d=float(x.get("difficulty") or 0),
                   density=float(adj.sum() / max(1, n * (n - 1))), uids=list(x["miner_uids"]), hotkeys=list(x["miner_hotkeys"]),
                   coldkeys=list(x["miner_coldkeys"]), answers=answers, ok=ok,
                   logged_rewards=list(x.get("miner_rewards") or []), encoded=x["encoded_matrix"])


def load(paths, cache: bool = True, workers: int | None = None) -> list[Problem]:
    """Load one or more .jsonl.gz files written by analysis.wandb_logs (globs allowed). Decoding and answer validation
    are cached next to each file as <name>.problems.pkl."""
    if isinstance(paths, str):
        paths = [paths]
    files = sorted(f for pat in paths for f in glob.glob(pat))
    out: list[Problem] = []
    for f in files:
        pkl = f[:-len(".jsonl.gz")] + ".problems.pkl" if f.endswith(".jsonl.gz") else f + ".problems.pkl"
        if cache and os.path.exists(pkl) and os.path.getmtime(pkl) >= os.path.getmtime(f):
            out.extend(pickle.load(open(pkl, "rb")))
            continue
        name = os.path.basename(f).replace(".jsonl.gz", "")
        nproc = workers or max(1, (os.cpu_count() or 2) - 1)
        with gzip.open(f, "rt") as fh:
            if nproc == 1:
                rows = [r for r in (_prepare_row((name, line)) for line in fh) if r is not None]
            else:
                with mp.Pool(nproc) as pool:
                    rows = [r for r in pool.imap(_prepare_row, ((name, line) for line in fh), chunksize=20) if r is not None]
        if cache:
            pickle.dump(rows, open(pkl, "wb"))
        out.extend(rows)
    return out


def check_scoring(problems, limit: int = 500) -> tuple[int, int]:
    """Return (mismatches, checked): how often our re-implementation disagrees with the logged rewards."""
    bad = checked = 0
    for p in problems[:limit]:
        if len(p.logged_rewards) != len(p.answers):
            continue
        checked += 1
        if np.abs(score(p.answers, p.ok, p.d)[2] - np.array(p.logged_rewards, dtype=float)).max() > 1e-6:
            bad += 1
    return bad, checked


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    P = load(args or ["data/*.jsonl.gz"])
    print(f"{len(P)} problems loaded")
    if "--check" in sys.argv:
        bad, n = check_scoring(P, limit=len(P))
        print(f"scoring re-implementation: {bad} mismatches in {n} problems")
