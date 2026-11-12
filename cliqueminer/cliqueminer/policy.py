"""Answer selection.

The validator's reward is  omega_n * (1 + d) + delta_n  where delta is 1 / (number of queried miners returning the
identical vertex set), normalised by the best delta in the round. So *which* maximum clique is returned matters as much
as its size, and occasionally a unique clique one vertex smaller pays more than a crowded maximum clique.

What the logged validator data (Nov 2026) shows about the miners already on the subnet:
  * they enumerate every maximum clique and spread their miners evenly over them;
  * when the graph has fewer maximum cliques than a fixed per-tier threshold (12 / 8 / 5 / 3 for the 300 / 500 / 700 /
    900-node tiers, roughly expected_queried_miners / 8) they switch, together, to distinct maximal cliques of size
    best-1 (keeping omega ~0.95 but delta ~1).
`mirror` reproduces that size rule and picks uniformly at random inside the chosen size class. Which individual cliques
the incumbents occupy is only weakly predictable at request time (a learned picker gained about +0.04 reward in
backtests, see FINDINGS.md); simple rules based on discovery order or search hit counts gained nothing, so the pick is
uniform.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# (max_nodes, difficulty) per problem tier — CliqueAI/selection/problem_selector.py at v0.0.17
TIERS = ((300, 0.7), (500, 0.8), (700, 0.9), (900, 1.0))
REFERENCE_R = 1.5                      # CliqueAI/selection/miner_selector.py


def difficulty_for_nodes(n: int) -> float:
    for max_nodes, d in TIERS:
        if n <= max_nodes:
            return d
    return TIERS[-1][1]


def selection_probability(difficulty: float) -> float:
    """Probability that any given eligible miner is queried for a problem of this difficulty."""
    return 1.0 - math.exp(-max(0.0, math.sqrt(1.0 + REFERENCE_R) - difficulty - 0.5))


def expected_queried(n_nodes: int, eligible_miners: int) -> float:
    return eligible_miners * selection_probability(difficulty_for_nodes(n_nodes))


# Observed switch points of the incumbent miners (validator logs, Nov 2026): with K maximum cliques they answer at
# size best-1 when K < threshold. Fitted on 1,190 problems: exact on the 300- and 500-node tiers, 99% / 95% on 700 / 900.
SUB_THRESHOLDS = {300: 12, 500: 8, 700: 5, 900: 3}     # keyed by the tier's max node count


@dataclass
class PolicyConfig:
    mode: str = "mirror"               # "mirror" | "always_best"
    crowd_divisor: float = 8.5         # for graph sizes outside SUB_THRESHOLDS: switch when K < expected_queried / divisor
    eligible_miners: int = 248         # miners the validator can sample; the miner refreshes this from the metagraph
    sub_thresholds: dict | None = None  # override SUB_THRESHOLDS (None = use the fitted table)


def sub_threshold(n_nodes: int, cfg: PolicyConfig) -> float:
    table = SUB_THRESHOLDS if cfg.sub_thresholds is None else cfg.sub_thresholds
    for max_nodes, _ in TIERS:
        if n_nodes <= max_nodes:
            if max_nodes in table and n_nodes > max_nodes - 20:      # inside a known tier's size band
                return float(table[max_nodes])
            break
    return expected_queried(n_nodes, cfg.eligible_miners) / cfg.crowd_divisor


def use_sub_size(n_max_cliques: int, n_sub_cliques: int, n_nodes: int, cfg: PolicyConfig) -> bool:
    if cfg.mode != "mirror" or n_sub_cliques == 0:
        return False
    return n_max_cliques < sub_threshold(n_nodes, cfg)


def choose_answer(max_cliques, sub_cliques, n_nodes: int, rng, cfg: PolicyConfig | None = None) -> tuple[int, ...]:
    """Pick the clique to return. `max_cliques` must be non-empty; `rng` is a numpy Generator."""
    cfg = cfg or PolicyConfig()
    if not max_cliques:
        raise ValueError("no candidate cliques")
    if use_sub_size(len(max_cliques), len(sub_cliques), n_nodes, cfg):
        return tuple(sub_cliques[int(rng.integers(len(sub_cliques)))])
    return tuple(max_cliques[int(rng.integers(len(max_cliques)))])


def candidate_order(max_cliques, sub_cliques, n_nodes: int, rng, cfg: PolicyConfig | None = None):
    """Yield candidates in preference order: the policy's pick first, then the rest of its size class, then the other
    class. The miner walks this list and returns the first candidate that passes its independent validity check."""
    cfg = cfg or PolicyConfig()
    first, second = (sub_cliques, max_cliques) if use_sub_size(len(max_cliques), len(sub_cliques), n_nodes, cfg) else (max_cliques, sub_cliques)
    for pool in (first, second):
        if pool:
            for i in rng.permutation(len(pool)):
                yield tuple(pool[int(i)])
