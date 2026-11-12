"""Market-structure diagnostics: who runs the miners, how they behave, and whether their answers are coordinated."""
from __future__ import annotations

import collections

import numpy as np

from .dataset import Problem, score


def operator_tags(problems: list[Problem], min_miners: int = 10) -> dict[str, str]:
    """coldkey -> 'Op1', 'Op2', ... for every coldkey running at least `min_miners` distinct hotkeys (largest first)."""
    hk = collections.defaultdict(set)
    for p in problems:
        for h, c in zip(p.hotkeys, p.coldkeys):
            hk[c].add(h)
    big = sorted((c for c in hk if len(hk[c]) >= min_miners), key=lambda c: (-len(hk[c]), c))
    return {c: f"Op{i + 1}" for i, c in enumerate(big)}


def concentration(problems: list[Problem], tags: dict[str, str]) -> dict:
    hk = collections.defaultdict(set)
    for p in problems:
        for h, c in zip(p.hotkeys, p.coldkeys):
            hk[c].add(h)
    total = sum(len(v) for v in hk.values())
    return {"miners_seen": total, "coldkeys": len(hk),
            "operators": {tags[c]: {"coldkey": c, "miners": len(hk[c])} for c in tags},
            "share_of_miners_in_large_operators": sum(len(hk[c]) for c in tags) / max(1, total)}


def per_miner_means(problems: list[Problem]) -> dict[str, dict]:
    """hotkey -> {mean, n, coldkey}: the quantity each validator's EMA converges to."""
    acc = collections.defaultdict(lambda: [0.0, 0, ""])
    for p in problems:
        rw = score(p.answers, p.ok, p.d)[2]
        for h, c, x in zip(p.hotkeys, p.coldkeys, rw):
            a = acc[h]
            a[0] += float(x)
            a[1] += 1
            a[2] = c
    return {h: {"mean": s / n, "n": n, "coldkey": c} for h, (s, n, c) in acc.items()}


def rank_threshold(means: dict[str, dict], rank: int = 20, min_samples: int = 100) -> float:
    v = sorted((m["mean"] for m in means.values() if m["n"] >= min_samples), reverse=True)
    return v[rank - 1] if len(v) >= rank else float("nan")


def operator_summary(problems: list[Problem], tags: dict[str, str]) -> dict[str, dict]:
    acc = collections.defaultdict(lambda: collections.defaultdict(float))
    for p in problems:
        om, dl, rw = score(p.answers, p.ok, p.d)
        b = p.best
        for i, c in enumerate(p.coldkeys):
            a = acc[tags.get(c, "independent")]
            a["n"] += 1
            a["reward"] += rw[i]
            a["omega"] += om[i]
            a["delta"] += dl[i]
            a["valid"] += p.ok[i]
            a["at_best"] += p.ok[i] and len(p.answers[i]) == b
    return {k: {m: (v[m] / v["n"] if m != "n" else int(v["n"])) for m in ("n", "reward", "omega", "delta", "valid", "at_best")}
            for k, v in sorted(acc.items())}


def bimodality(problems: list[Problem]) -> dict:
    f = np.array([p.frac_at_best for p in problems])
    return {"all_at_best(>=90%)": float((f >= 0.9).mean()), "minority_at_best(<25%)": float((f < 0.25).mean()),
            "in_between": float(((f >= 0.25) & (f < 0.9)).mean())}


def balance_test(problems: list[Problem], tags: dict[str, str], null_draws: int = 200, seed: int = 0) -> dict:
    """In 'crowded' rounds (far fewer distinct best-size answers than miners) count, per clique, the miners of the large
    operators COMBINED. A shared allocator gives max-min <= 1; operators that merely de-duplicate their own miners,
    independently of each other, almost never do. Returns observed share and the share under that independence null."""
    rng = np.random.default_rng(seed)
    obs, null, spread, spread_null, n = [], [], [], [], 0
    for p in problems:
        if p.frac_at_best < 0.9:
            continue
        b = p.best
        at = [(tags.get(c), a) for c, a, o in zip(p.coldkeys, p.answers, p.ok) if o and len(a) == b]
        cliques = sorted(set(a for _, a in at))
        k = len(cliques)
        big = [(t, a) for t, a in at if t is not None]
        if k < 3 or k > 0.6 * len(big):
            continue
        idx = {a: i for i, a in enumerate(cliques)}
        cnt = np.zeros(k, int)
        for _, a in big:
            cnt[idx[a]] += 1
        per_op = collections.Counter(t for t, _ in big)
        sims = []
        for _ in range(null_draws):
            c2 = np.zeros(k, int)
            for m in per_op.values():
                q, r = divmod(m, k)
                c2 += q
                c2[rng.permutation(k)[:r]] += 1
            sims.append(c2.max() - c2.min())
        n += 1
        obs.append(cnt.max() - cnt.min() <= 1)
        null.append(np.mean(np.array(sims) <= 1))
        spread.append(cnt.max() - cnt.min())
        spread_null.append(np.mean(sims))
    if n == 0:
        return {"crowded_rounds": 0}
    return {"crowded_rounds": n, "balanced_share": float(np.mean(obs)), "balanced_share_if_independent": float(np.mean(null)),
            "mean_spread": float(np.mean(spread)), "mean_spread_if_independent": float(np.mean(spread_null))}
