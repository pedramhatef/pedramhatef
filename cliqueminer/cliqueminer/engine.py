"""Request-independent solving pipeline: encoded graph + deadline -> one verified maximal clique.

Kept free of any bittensor import so that it can be unit-tested and benchmarked offline.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import numpy as np

from . import codec, policy
from .solver import SolveResult, native_available, native_error, solve_native

log = logging.getLogger("cliqueminer")


@dataclass
class EngineConfig:
    threads: int = max(1, (os.cpu_count() or 2) - 1)
    # time budget = clamp(budget_fraction * timeout, min_budget, max_budget), never more than timeout - safety_margin
    budget_fraction: float = 0.5
    min_budget: float = 0.4
    max_budget: float = 8.0
    safety_margin: float = 2.0          # seconds kept for network transfer, request verification and the reply
    default_timeout: float = 6.0        # shortest limit the validator uses; assumed when the request carries none


@dataclass
class Answer:
    clique: list[int]
    best_size: int
    n_max: int
    n_sub: int
    used_sub: bool
    native: bool
    solve_seconds: float
    total_seconds: float
    note: str = ""


def time_budget(timeout: float | None, cfg: EngineConfig, already_elapsed: float = 0.0) -> float:
    t = float(timeout) if timeout and timeout > 0 else cfg.default_timeout
    budget = min(max(cfg.budget_fraction * t, cfg.min_budget), cfg.max_budget)
    budget = min(budget, t - cfg.safety_margin - already_elapsed)
    return max(budget, 0.05)


def solve(encoded: str, timeout: float | None, seed: int, ecfg: EngineConfig | None = None, pcfg: policy.PolicyConfig | None = None,
          received_at: float | None = None) -> Answer:
    """Never raises for a decodable graph: falls back to a greedy maximal clique if the native solver is unavailable or
    fails. Raises ValueError only if the graph itself cannot be decoded (nothing valid can be returned then)."""
    ecfg, pcfg = ecfg or EngineConfig(), pcfg or policy.PolicyConfig()
    t0 = time.monotonic()
    elapsed0 = (t0 - received_at) if received_at is not None else 0.0
    rng = np.random.default_rng(seed)
    adj = codec.decode_matrix(encoded)             # ValueError if malformed
    n = adj.shape[0]
    budget = time_budget(timeout, ecfg, elapsed0 + (time.monotonic() - t0))
    res: SolveResult | None = None
    note = ""
    ts = time.monotonic()
    if native_available():
        try:
            res = solve_native(encoded, budget, ecfg.threads, seed)
        except Exception as e:                     # noqa: BLE001 - any solver failure must degrade, not drop the reply
            note = f"native solver error: {e}"
            log.warning(note)
    else:
        note = f"native solver unavailable: {native_error()}"
    solve_s = time.monotonic() - ts
    if res is not None:
        used_sub = policy.use_sub_size(len(res.max_cliques), len(res.sub_cliques), n, pcfg)
        for cand in policy.candidate_order(res.max_cliques, res.sub_cliques, n, rng, pcfg):
            if codec.is_maximal_clique(adj, cand):  # independent re-check against our own decode of the graph
                return Answer(sorted(int(v) for v in cand), res.best, len(res.max_cliques), len(res.sub_cliques),
                              used_sub and len(cand) == res.best - 1, True, solve_s, time.monotonic() - t0, note)
        note = (note + "; " if note else "") + "no native candidate passed verification"
        log.error(note)
    clique = codec.greedy_clique(adj, rng)          # always valid and maximal
    return Answer(clique, len(clique), 1, 0, False, False, solve_s, time.monotonic() - t0, note or "fallback")
