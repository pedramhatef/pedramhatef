"""MULTIXSEC (XSEC-RANK) source-native counterfactual harness for UID249. Off-production only.

Scorer: Barbariandev/MANTIS@ace548a xsec_rank.compute_xsec_rank_salience, imported and called UNMODIFIED.
Label (source): 4h forward return above the cross-sectional median of 33 BREAKOUT_ASSETS, paired by
ROW position (prices[t + 240]), not by sidx.

PRE-REGISTERED (frozen before any real-data run):
  Primary candidate C2 "funding crowding": s_a(t) = -tanh(robust_z_a(f_a(t))), using the as-of funding level
    f_a(t) from the same feed snapshot at row t (OKX-upstream per round-3 diagnostic). Covers the 20 assets
    that have funding; the other 13 assets are submitted as exact 0.0 (= missing to the scorer).
    Rationale: high funding = crowded longs paying carry, expected to underperform cross-sectionally.
  Ablation C1 "BTC-residual 4h reversal": s_a(t) = -tanh(robust_z_a(r_a - beta_a r_BTC)), with r = 240-row log
    return ending at t and beta from the trailing 10,080 rows of 1-row returns (rows <= t only). It is
    REPORTED ONLY and cannot be promoted.
  Promotable variants (exactly two): V1 = C2 replaces UID249's MULTIXSEC column;
    V2 = 0.5*tanh(robust_z(UID249 current)) + 0.5*C2, on assets where both exist, else whichever exists.
  Folds: F1 = rows[:24000 + 240] (last scorable segment = original segment 3);
         F2 = rows[8000:32000 + 240] (shifting by one CHUNK makes original segment 4 the ONLY scorable segment).
         The scorable segments of every run are recorded in the output (original-row coordinates).
         FULL = all rows (what a validator would compute on this window).
  Control R (reported, never promoted): R = tanh(robust_z(UID249 current)) alone. V2 contains this re-scaling,
    so a V2 gain is attributed to C2 only if V2 also beats R (gate applies in F1, F2 and FULL).
  Gates: V share - baseline share > 0 in F1 AND F2 (and, for V2, V2 - R > 0 in F1 AND F2); FULL delta >= +0.025 absolute; day-cluster bootstrap
    95% lower bound > 0 (--daily: recompute at each UTC-day end row, bootstrap the paired deltas over days;
    end-date runs overlap, so this CI is optimistic and is stated as such); other challenges untouched.
  Freeze contamination: report the rows whose all-33 prices are unchanged for >= 30 min.
    Also report rows where funding is frozen while prices move (that is a price_service funding fallback).
  Memory option (OFF by default): --drop-zero-cols removes miner columns that are all-zero across the window.
    It is NOT bit-exact: the lbfgs meta-fit sees a different design matrix. Measured on a synthetic
    13-column field: same keys and ranks, max |share diff| 3.1e-6 (test asserts < 1e-5). The default
    passes every column, exactly as a validator would.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone

import numpy as np

AHEAD_ROWS = 1200 // 5
CHUNK = 8000
BETA_WIN = 10080
STALE_S = 1800


def robust_z(x: np.ndarray) -> np.ndarray:
    """Cross-sectional robust z over finite, non-zero-masked entries; NaN elsewhere."""
    out = np.full(x.shape, np.nan)
    ok = np.isfinite(x)
    if ok.sum() < 3:
        return out
    med = np.median(x[ok]); mad = 1.4826 * np.median(np.abs(x[ok] - med))
    if not mad > 0:
        return out
    out[ok] = (x[ok] - med) / mad
    return out


def feature_c2(funding_row_by_breakout: np.ndarray) -> np.ndarray:
    """funding_row_by_breakout: (33,) as-of funding, NaN where the asset has no funding series."""
    z = robust_z(funding_row_by_breakout)
    return np.where(np.isfinite(z), -np.tanh(z), 0.0)


def feature_c1(logp: np.ndarray, t: int, btc: int = 0) -> np.ndarray:
    """logp: (T, 33) log prices; uses rows <= t only."""
    A = logp.shape[1]
    if t < AHEAD_ROWS + 100:
        return np.zeros(A)
    r = logp[t] - logp[t - AHEAD_ROWS]
    lo = max(1, t - BETA_WIN + 1)
    d = np.diff(logp[lo - 1:t + 1], axis=0)
    db = d[:, btc]
    vb = np.nanvar(db)
    beta = np.array([np.nanmean((d[:, a] - np.nanmean(d[:, a])) * (db - np.nanmean(db))) / vb if vb > 0 else 1.0
                     for a in range(A)])
    resid = r - beta * r[btc]
    resid[btc] = np.nan                       # BTC residual is identically 0: no information
    z = robust_z(resid)
    return np.where(np.isfinite(z), -np.tanh(z), 0.0)


def blend(cur: np.ndarray, cand: np.ndarray) -> np.ndarray:
    zc = robust_z(np.where(cur != 0, cur, np.nan))
    a = np.where(np.isfinite(zc), np.tanh(zc), 0.0)
    both = (a != 0) & (cand != 0)
    return np.where(both, 0.5 * a + 0.5 * cand, np.where(a != 0, a, cand))


def build_candidate(prices: np.ndarray, funding33: np.ndarray, cur: np.ndarray, which: str) -> np.ndarray:
    T, A = prices.shape
    out = np.zeros((T, A), dtype=np.float32)
    logp = np.where(prices > 0, np.log(np.where(prices > 0, prices, 1.0)), np.nan)
    for t in range(T):
        if which in ("C2", "V1", "V2"):
            c = feature_c2(funding33[t])
        else:
            c = feature_c1(logp, t)
        if which == "R":
            c = blend(cur[t], np.zeros(A))
        elif which in ("V2", "C1_blend"):
            c = blend(cur[t], c)
        out[t] = np.clip(c, -1, 1)
    return out


def freeze_audit(prices: np.ndarray, funding33: np.ndarray | None, ts: np.ndarray) -> dict:
    def stale_mask(M):
        ch = np.r_[True, np.any((M[1:] != M[:-1]) & np.isfinite(M[1:]), axis=1)]
        last = np.maximum.accumulate(np.where(ch, ts, -np.inf))
        return (ts - last) >= STALE_S
    sp = stale_mask(prices)
    res = {"rows": int(ts.size), "price_stale_rows": int(sp.sum())}
    if funding33 is not None:
        fm = funding33[:, np.isfinite(funding33).any(axis=0)]
        sf = stale_mask(np.nan_to_num(fm, nan=0.0))
        res |= {"funding_stale_rows": int(sf.sum()),
                "funding_stale_while_prices_move_rows": int((sf & ~sp).sum()),
                "both_stale_rows": int((sf & sp).sum())}
    fwd_zero = np.zeros(ts.size, bool)
    fwd_zero[:-AHEAD_ROWS] = np.all(prices[AHEAD_ROWS:] == prices[:-AHEAD_ROWS], axis=1)
    res["label_rows_all33_zero_return"] = int(fwd_zero.sum())
    return res


def scorable_segments(T: int, offset: int = 0) -> list:
    """Replicates xsec_rank's segment rule (CHUNK 8000, LAG 60, MIN_TRAIN_ROWS 200, ahead 240).
    A segment is scorable if its selection-fit window holds rows. Returned in original-row coordinates."""
    T_u, out, s = T - AHEAD_ROWS, [], 0
    while s < T_u:
        vs = s + 60
        if vs >= T_u:
            break
        e = min(s + CHUNK, T_u)
        if e <= vs:
            break
        fit_end = max(0, max(0, vs - CHUNK) - 60)
        if fit_end > 0 and (e - vs) > 0:
            out.append((s + offset, vs + offset, e + offset))
        s = e
    return out


DROP_ZERO_COLS = False


def score(scorer, X: np.ndarray, hks: list[str], prices: np.ndarray, drop_zero=None) -> dict:
    """X: (T, H, 33). Calls the pinned scorer; optionally drops all-zero columns (numerically, not bit, equal)."""
    drop = DROP_ZERO_COLS if drop_zero is None else drop_zero
    keep = np.flatnonzero(np.any(X != 0, axis=(0, 2))) if drop else np.arange(X.shape[1])
    Xk = X[:, keep, :]
    hk2idx = {hks[k]: i for i, k in enumerate(keep)}
    flat = Xk.reshape(Xk.shape[0], -1).astype(np.float32)
    return scorer.compute_xsec_rank_salience((flat, hk2idx), prices, 1200, 5)


def share_rank(sal: dict, hk: str):
    s = sal.get(hk, 0.0)
    ranks = sorted(sal.values(), reverse=True)
    return round(float(s), 6), (ranks.index(s) + 1 if s > 0 else None), len(sal)


def run(scorer, X, hks, prices, funding33, target, variants, folds):
    j = hks.index(target)
    cur = X[:, j, :].astype(np.float64)
    cols = {"baseline": cur.astype(np.float32)}
    for v in variants:
        cols[v] = build_candidate(prices, funding33, cur, v)
    out = {}
    T = X.shape[0]
    fold_slices = {"F1": slice(0, min(T, 3 * CHUNK + AHEAD_ROWS)),
                   "F2": slice(CHUNK, min(T, 4 * CHUNK + AHEAD_ROWS)), "FULL": slice(0, T)}
    for fname in folds:
        sl = fold_slices[fname]
        n = len(range(*sl.indices(T)))
        out[fname] = {"_scorable_segments": scorable_segments(n, sl.start or 0)}
        for v, col in cols.items():
            Xv = X[sl].copy(); Xv[:, j, :] = col[sl]
            out[fname][v] = share_rank(score(scorer, Xv, hks, prices[sl]), target)
    cov = {v: round(float((c != 0).mean()), 4) for v, c in cols.items()}
    return out, cov, cols


def daily(scorer, X, hks, prices, ts, target, cols, variants, n_boot=2000, seed=0):
    """Paired deltas at each UTC-day end row where >= 1 segment is scorable; bootstrap over days."""
    j = hks.index(target)
    day = (ts // 86400).astype(np.int64)
    ends = [int(np.flatnonzero(day == d)[-1]) + 1 for d in np.unique(day)]
    ends = [e for e in ends if scorable_segments(e)]
    rows = []
    for e in ends:
        r = {"end_row": e, "end_utc": datetime.fromtimestamp(float(ts[e - 1]), timezone.utc).isoformat()}
        for v in ["baseline"] + variants:
            Xv = X[:e].copy(); Xv[:, j, :] = cols[v][:e]
            r[v] = share_rank(score(scorer, Xv, hks, prices[:e]), target)[0]
        rows.append(r)
    rng = np.random.default_rng(seed)
    ci = {}
    for v in variants:
        d = np.array([r[v] - r["baseline"] for r in rows])
        if d.size >= 2:
            bs = [rng.choice(d, d.size, replace=True).mean() for _ in range(n_boot)]
            ci[v] = {"n_days": int(d.size), "mean_delta": float(d.mean()),
                     "ci95": [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]}
    return {"rows": rows, "bootstrap": ci, "caveat": "end-date runs share history; CI is optimistic"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mantis", required=True, help="pinned MANTIS checkout (ace548a)")
    ap.add_argument("--npz", required=True, help="keys: prices (T,33), multixsec (T,H,33), hotkeys (H,), capture_ts (T,), optional funding33 (T,33)")
    ap.add_argument("--target", required=True)
    ap.add_argument("--variants", default="V1,V2,R,C1")
    ap.add_argument("--folds", default="F1,F2,FULL")
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--drop-zero-cols", action="store_true")
    ap.add_argument("--out", default="multixsec_result.json")
    a = ap.parse_args(argv)
    global DROP_ZERO_COLS
    DROP_ZERO_COLS = a.drop_zero_cols
    sys.path.insert(0, a.mantis)
    logging.disable(logging.CRITICAL)
    import xsec_rank as scorer  # noqa: E402
    scorer.tqdm = lambda *x, **k: type("N", (), {"update": lambda s, n=1: None, "close": lambda s: None})()
    src = open(scorer.__file__, "rb").read()
    z = np.load(a.npz, allow_pickle=False)
    prices, X, hks, ts = z["prices"], z["multixsec"], [str(h) for h in z["hotkeys"]], z["capture_ts"]
    funding33 = z["funding33"] if "funding33" in z.files else np.full(prices.shape, np.nan)
    res = {"utc": datetime.now(timezone.utc).isoformat(),
           "scorer_sha256": hashlib.sha256(src).hexdigest(),
           "input_sha256": hashlib.sha256(open(a.npz, "rb").read()).hexdigest(),
           "shape": {"T": int(X.shape[0]), "H": int(X.shape[1]), "A": int(X.shape[2])},
           "freeze_audit": freeze_audit(prices, funding33 if "funding33" in z.files else None, ts)}
    variants = a.variants.split(",")
    res["shares"], res["coverage"], cols = run(scorer, X, hks, prices, funding33, a.target,
                                               variants, a.folds.split(","))
    if a.daily:
        res["daily"] = daily(scorer, X, hks, prices, ts, a.target, cols, [v for v in variants if v in ("V1", "V2", "R")])
    json.dump(res, open(a.out, "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
