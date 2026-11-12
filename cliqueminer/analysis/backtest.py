"""Backtest an outsider miner against real response pools.

For each sampled historical problem we run our own solver, apply an answer policy, append that answer to the answers
the queried miners actually sent, and re-score the round exactly as the validator does. The tier-weighted mean of that
reward is what the newcomer's validator EMA would converge to.
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import random
from dataclasses import dataclass

import numpy as np

from cliqueminer import policy
from cliqueminer.solver import SolveResult, solve_native

from .dataset import Problem, reward_if_added

# single-thread solve time by tier, tuned so that enumeration of maximum cliques is complete on the two smaller tiers
SOLVE_SECONDS = {300: 0.6, 500: 1.2, 700: 3.0, 900: 8.0}


def tier_of(n: int) -> int:
    for max_nodes, _ in policy.TIERS:
        if n <= max_nodes:
            return max_nodes
    return policy.TIERS[-1][0]


@dataclass
class Case:
    problem: Problem
    result: SolveResult
    weight: float                       # probability that one miner is sampled for this problem


def solve_cases(problems: list[Problem], sample: int = 300, seed: int = 0, workers: int | None = None, scale: float = 1.0) -> list[Case]:
    rnd = random.Random(seed)
    chosen = rnd.sample(problems, min(sample, len(problems)))

    def one(p: Problem):
        try:
            res = solve_native(p.encoded, SOLVE_SECONDS[tier_of(p.n)] * scale, 1, seed=p.step + 1)
        except RuntimeError:
            return None
        return Case(p, res, policy.selection_probability(p.d))

    with cf.ThreadPoolExecutor(workers or max(1, (os.cpu_count() or 2) - 1)) as ex:   # the solver releases the GIL
        return [c for c in ex.map(one, chosen) if c is not None]


def _wmean(pairs):
    num = sum(w * x for w, x in pairs)
    den = sum(w for w, _ in pairs)
    return num / den if den else float("nan")


def evaluate(cases: list[Case], pcfg: policy.PolicyConfig | None = None, seed: int = 0, reps: int = 3) -> dict:
    """Mean reward / omega / delta of the given policy config, plus split by regime."""
    rng = np.random.default_rng(seed)
    pcfg = pcfg or policy.PolicyConfig()
    rw, om, dl, by = [], [], [], {"all_at_best": [], "minority_at_best": []}
    for _ in range(reps):
        for c in cases:
            ans = policy.choose_answer(c.result.max_cliques, c.result.sub_cliques, c.problem.n, rng, pcfg)
            r, o, d_ = reward_if_added(c.problem, ans)
            rw.append((c.weight, r))
            om.append((c.weight, o))
            dl.append((c.weight, d_))
            by["minority_at_best" if c.problem.frac_at_best < 0.25 else "all_at_best"].append((c.weight, r))
    return {"mean": _wmean(rw), "omega": _wmean(om), "delta": _wmean(dl), "by_regime": {k: _wmean(v) for k, v in by.items()}}


def oracle(cases: list[Case], max_sub: int = 60) -> float:
    """Upper bound: the best reward available among our candidates if we could see everyone else's answers first."""
    out = []
    for c in cases:
        best = max(reward_if_added(c.problem, a)[0] for a in c.result.max_cliques)
        for a in c.result.sub_cliques[:max_sub]:
            best = max(best, reward_if_added(c.problem, a)[0])
        out.append((c.weight, best))
    return _wmean(out)


def solver_vs_pool(cases: list[Case]) -> dict:
    """How our solver's best size compares with the best size any queried miner returned."""
    gt = sum(c.result.best > c.problem.best for c in cases)
    eq = sum(c.result.best == c.problem.best for c in cases)
    return {"larger": gt, "equal": eq, "smaller": len(cases) - gt - eq, "n": len(cases)}


def switch_thresholds(cases: list[Case]) -> dict[int, dict]:
    """Per tier: the number of maximum cliques K below which the crowd answers at best-1.
    Returns the range [lo, hi] of thresholds T (rule: K < T) that fit best, and that fit's accuracy. `lo` is one more
    than the largest K seen with the crowd at best-1; `hi` is the smallest K seen with the crowd at best."""
    out = {}
    for t in (300, 500, 700, 900):
        rows = [(len(c.result.max_cliques), c.problem.frac_at_best < 0.25) for c in cases if tier_of(c.problem.n) == t and c.result.best == c.problem.best]
        if len(rows) < 10 or not any(m for _, m in rows) or all(m for _, m in rows):
            continue
        accs = [(float(np.mean([(k < th) == minority for k, minority in rows])), th) for th in range(1, 60)]
        best = max(a for a, _ in accs)
        ths = [th for a, th in accs if a == best]
        out[t] = {"lo": min(ths), "hi": max(ths), "accuracy": best, "n": len(rows)}
    return out
