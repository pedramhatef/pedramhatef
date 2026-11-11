"""Synthetic MECHANISM test of the pinned MANTIS FUNDINGXSEC scorer (unmodified).

Question: does the source scorer give a miner more challenge share when it
abstains (submits exact 0.0) on assets where its signal is wrong-signed,
and does a fully CAUSAL trailing self-evaluation gate capture that gain?

This says nothing about real-data gain; it only checks how the scorer
responds to abstention.  Synthetic world, known signal strengths.
"""
import sys, json, logging
import numpy as np

sys.path.insert(0, sys.argv[1])  # pinned MANTIS checkout
logging.disable(logging.CRITICAL)
import funding_xsec as fx  # noqa: E402
import config  # noqa: E402

A = len(config.FUNDING_ASSETS)          # 20
AHEAD = 2400 // int(config.SAMPLE_EVERY)  # 480 rows
T = int(sys.argv[2]) if len(sys.argv) > 2 else 24000
H_PEERS = int(sys.argv[3]) if len(sys.argv) > 3 else 49
SEEDS = [int(s) for s in (sys.argv[4].split(",") if len(sys.argv) > 4 else ["1", "2", "3"])]
import os
BAD = int(os.environ.get("BAD", "6"))  # assets where target sign is wrong


def world(seed):
    rng = np.random.default_rng(seed)
    # funding = market factor + slow idiosyncratic AR(1) + small noise
    mkt = np.cumsum(rng.normal(0, 1e-6, T))
    idio = np.zeros((T, A))
    for t in range(1, T):
        idio[t] = 0.9995 * idio[t - 1] + rng.normal(0, 2e-6, A)
    fr = mkt[:, None] + idio + rng.normal(0, 5e-7, (T, A))
    delta = np.full((T, A), np.nan)
    delta[:-AHEAD] = fr[AHEAD:] - fr[:-AHEAD]
    rel = delta - np.nanmedian(delta, axis=1, keepdims=True)
    z = rel / np.nanstd(rel)
    z = np.nan_to_num(z)                  # last AHEAD rows: no future
    return rng, fr, z


def miner(rng, z, strength_per_asset, shared=None, rho=0.0):
    noise = rng.normal(0, 1, z.shape)
    if shared is not None:
        noise = np.sqrt(1 - rho) * noise + np.sqrt(rho) * shared
    return np.tanh(0.5 * (strength_per_asset[None, :] * z + noise))


def causal_gate(sig, z, window=4320, min_hist=1440):
    """Abstain on asset a at row t iff the trailing realized correlation of
    our own past outputs with labels that had RESOLVED by t is <= 0.
    Label for row s resolves at s+AHEAD, so only rows s <= t-AHEAD are used.
    Uses per-asset rolling sums (O(T*A))."""
    T_, A_ = sig.shape
    out = sig.copy()
    y = (z > 0).astype(float) - 0.5
    prod = sig * y
    cs = np.vstack([np.zeros((1, A_)), np.cumsum(prod, axis=0)])
    for t in range(T_):
        hi = t - AHEAD + 1               # rows s < hi are resolved
        if hi < min_hist:
            continue                     # not enough history: keep output
        lo = max(0, hi - window)
        score = cs[hi] - cs[lo]
        out[t, score <= 0] = 0.0
    return out


def run(seed):
    rng, fr, z = world(seed)
    shared = rng.normal(0, 1, z.shape)
    peers = []
    for i in range(H_PEERS):
        s = rng.uniform(0.0, 0.35)
        s_vec = s * rng.uniform(0.6, 1.4, A)
        rho = 0.5 if i % 3 == 0 else 0.0  # a correlated sub-cohort
        peers.append(miner(rng, z, s_vec, shared if rho else None, rho))
    tgt_s = np.full(A, 0.28)
    bad_idx = rng.choice(A, BAD, replace=False)
    tgt_s[bad_idx] = -0.18
    base = miner(rng, z, tgt_s)

    oracle = base.copy(); oracle[:, bad_idx] = 0.0
    causal = causal_gate(base, z)
    # placebo: abstain on 6 RANDOM good assets (same coverage loss, no info)
    good = np.setdiff1d(np.arange(A), bad_idx)
    placebo = base.copy(); placebo[:, rng.choice(good, 6, replace=False)] = 0.0

    sidx = np.arange(T, dtype=np.int64)
    res = {"seed": seed, "bad_assets": sorted(bad_idx.tolist())}
    for name, tgt in [("baseline", base), ("oracle_abstain", oracle),
                      ("causal_abstain", causal), ("placebo_abstain", placebo)]:
        cols = [tgt] + peers
        X = np.stack(cols, axis=1).reshape(T, len(cols) * A).astype(np.float32)
        hk2idx = {f"hk{j}": j for j in range(len(cols))}
        sal = fx.compute_funding_xsec_salience((X, hk2idx), fr, 2400,
                                               int(config.SAMPLE_EVERY), sidx_arr=sidx)
        ranks = sorted(sal.values(), reverse=True)
        share = sal.get("hk0", 0.0)
        res[name] = {"share": round(share, 6),
                     "rank": (ranks.index(share) + 1) if share > 0 else None,
                     "coverage": round(float((tgt != 0).mean()), 4)}
    return res


if __name__ == "__main__":
    fx.tqdm = lambda *a, **k: type("N", (), {"update": lambda s, n=1: None, "close": lambda s: None})()
    out = [run(s) for s in SEEDS]
    print(json.dumps({"T": T, "peers": H_PEERS, "ahead_rows": AHEAD, "runs": out}, indent=1))
