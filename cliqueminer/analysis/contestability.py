"""One-command go / no-go check: could a NEW miner reach the top 20 right now?

    python -m analysis.wandb_logs --steps 3000 --out data/        # ~3 min, ~200 MB
    python -m analysis.contestability data/*.jsonl.gz

It reports who runs the miners, whether their answers are coordinated across operators, what an outsider running this
repository's miner would have earned on the same rounds (and the perfect-foresight upper bound), and compares that with
the reward level of the current rank-20 miner. Re-run it whenever the mechanism or the miner set changes.
"""
from __future__ import annotations

import argparse
import json

from . import backtest, dataset, market


def run(paths, sample: int = 300, seed: int = 0, solve_scale: float = 1.0) -> dict:
    problems = dataset.load(paths)
    if not problems:
        raise SystemExit("no problems loaded")
    bad, checked = dataset.check_scoring(problems)
    tags = market.operator_tags(problems)
    means = market.per_miner_means(problems)
    ranked = sorted((m["mean"] for m in means.values() if m["n"] >= 100), reverse=True)
    report = {
        "problems": len(problems),
        "window_hours": (max(p.ts for p in problems) - min(p.ts for p in problems)) / 3600,
        "scoring_reimplementation_mismatches": f"{bad}/{checked}",
        "concentration": market.concentration(problems, tags),
        "operators": market.operator_summary(problems, tags),
        "round_types": market.bimodality(problems),
        "cross_operator_balance": market.balance_test(problems, tags),
        "miner_reward_levels": {"rank_1": ranked[0] if ranked else None, "rank_20": market.rank_threshold(means, 20),
                                "median": ranked[len(ranked) // 2] if ranked else None, "miners_ranked": len(ranked)},
    }
    cases = backtest.solve_cases(problems, sample=sample, seed=seed, scale=solve_scale)
    newcomer = backtest.evaluate(cases)
    report["backtest"] = {"rounds": len(cases), "our_solver_vs_pool_best_size": backtest.solver_vs_pool(cases),
                          "switch_thresholds": backtest.switch_thresholds(cases), "newcomer_policy": newcomer,
                          "newcomer_oracle_upper_bound": backtest.oracle(cases)}
    need = report["miner_reward_levels"]["rank_20"]
    have, ceiling = newcomer["mean"], report["backtest"]["newcomer_oracle_upper_bound"]
    rank_if = 1 + sum(1 for x in ranked if x > have)
    report["verdict"] = {
        "reward_needed_for_rank_20": need, "newcomer_expected_reward": have, "newcomer_ceiling_with_perfect_foresight": ceiling,
        "newcomer_expected_rank": f"{rank_if} of {len(ranked) + 1}",
        "decision": "GO" if have >= need else ("MARGINAL" if ceiling >= need else "NO-GO"),
        "why": ("expected reward clears the rank-20 level" if have >= need else
                "only a perfect predictor of the other miners' answers would clear the rank-20 level" if ceiling >= need else
                "even with perfect foresight of every other miner's answer the reward stays below the rank-20 level"),
    }
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--sample", type=int, default=300, help="rounds to backtest (each costs 0.6-8 s of one core)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--solve-scale", type=float, default=1.0, help="multiply per-tier solve time")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rep = run(a.paths, a.sample, a.seed, a.solve_scale)
    if a.json:
        print(json.dumps(rep, indent=1, default=float))
        return
    c, v, b = rep["concentration"], rep["verdict"], rep["backtest"]
    print(f"{rep['problems']} rounds over {rep['window_hours']:.0f} h; scoring re-implementation mismatches: {rep['scoring_reimplementation_mismatches']}")
    print(f"\nMINERS  {c['miners_seen']} hotkeys, {c['coldkeys']} coldkeys; {c['share_of_miners_in_large_operators']:.0%} run by operators with >=10 miners")
    print(f"  {'operator':12s} {'miners':>6s} {'reward':>7s} {'omega':>6s} {'delta':>6s} {'valid':>6s} {'at best':>7s}")
    for name, s in rep["operators"].items():
        miners = c["operators"].get(name, {}).get("miners", "")
        print(f"  {name:12s} {str(miners):>6s} {s['reward']:7.3f} {s['omega']:6.3f} {s['delta']:6.3f} {s['valid']:6.3f} {s['at_best']:7.3f}")
    rt, bal = rep["round_types"], rep["cross_operator_balance"]
    print(f"\nROUNDS  everyone at best size: {rt['all_at_best(>=90%)']:.0%}; only a minority at best: {rt['minority_at_best(<25%)']:.0%}; in between: {rt['in_between']:.1%}")
    if bal.get("crowded_rounds"):
        print(f"COORDINATION  in {bal['crowded_rounds']} crowded rounds the large operators' combined per-clique counts are within 1 of each other "
              f"{bal['balanced_share']:.0%} of the time (independent operators: {bal['balanced_share_if_independent']:.1%})")
    sv = b["our_solver_vs_pool_best_size"]
    print(f"\nBACKTEST  {b['rounds']} rounds; our best size vs best in pool: larger {sv['larger']}, equal {sv['equal']}, smaller {sv['smaller']}")
    from cliqueminer.policy import SUB_THRESHOLDS
    parts = []
    for t, x in b["switch_thresholds"].items():
        cfg = SUB_THRESHOLDS.get(t)
        flag = "" if cfg is None or x["lo"] <= cfg <= x["hi"] else f"  <-- miner is configured with {cfg}: update cliqueminer/policy.py"
        parts.append(f"{t}-node: K<{x['lo']}" + (f"..{x['hi']}" if x["hi"] != x["lo"] else "") + f" ({x['accuracy']:.0%} of {x['n']}){flag}")
    print("  crowd switches to best-1 when the graph has fewer than K maximum cliques: " + "; ".join(parts))
    nb = b["newcomer_policy"]
    print(f"  newcomer with this miner: reward {nb['mean']:.3f} (omega {nb['omega']:.3f}, delta {nb['delta']:.3f}); perfect-foresight ceiling {b['newcomer_oracle_upper_bound']:.3f}")
    lv = rep["miner_reward_levels"]
    print(f"  current miners: rank 1 {lv['rank_1']:.3f}, rank 20 {lv['rank_20']:.3f}, median {lv['median']:.3f}")
    print(f"\nVERDICT  {v['decision']}: {v['why']}  (expected rank {v['newcomer_expected_rank']})")


if __name__ == "__main__":
    main()
