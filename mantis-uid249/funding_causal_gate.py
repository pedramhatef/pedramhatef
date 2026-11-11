"""Causal per-asset abstention gate for a MANTIS FUNDINGXSEC producer.

Status: EXPLORATORY. The mechanism has been checked against the pinned scorer
(Barbariandev/MANTIS@ace548a, funding_xsec.py sha256 99057f58...) on synthetic
fields only. NOT validated on the real 258-peer archive. NOT pilot-ready.

Why it can help: funding_xsec.py treats an exact 0.0 as missing at every
stage (univariate selection, sub-segment meta features, validation). Each
miner gets ONE pooled logistic slope across all 20 assets, so assets where
the producer's sign is wrong reduce its pooled AUC and its meta coefficient.
Abstaining (0.0) on those assets removes that noise. The decision must use
only labels that have resolved by submission time.

Label (source-native): y[s, a] = 1 if (fr[s+ahead, a] - fr[s, a]) is above
the cross-asset median of that change. It resolves at row s+ahead.
"""
from __future__ import annotations

import numpy as np

AHEAD_ROWS = 480          # FUNDINGXSEC blocks_ahead 2400 / SAMPLE_EVERY 5
WINDOW_ROWS = 4320        # 3 days of resolved history (pre-registered, not tuned)
MIN_HIST_ROWS = 1440      # 1 day of resolved history before the gate may fire


def resolved_labels(fr: np.ndarray, ahead: int = AHEAD_ROWS) -> np.ndarray:
    """(T, A) labels in {+0.5, -0.5}; NaN where unresolved or missing.

    Row s needs fr[s+ahead], so its label is knowable only at row >= s+ahead.
    Rows are assumed contiguous in sidx. On real data, pair by sidx like
    funding_xsec._build_forward_pairs does; do not pair by row position."""
    T, _ = fr.shape
    y = np.full(fr.shape, np.nan)
    d = fr[ahead:] - fr[:-ahead]
    med = np.nanmedian(d, axis=1, keepdims=True)
    lab = np.where(np.isnan(d) | np.isnan(med), np.nan, (d > med) - 0.5)
    y[: T - ahead] = lab
    return y


def gate(pred: np.ndarray, fr: np.ndarray, ahead: int = AHEAD_ROWS,
         window: int = WINDOW_ROWS, min_hist: int = MIN_HIST_ROWS,
         max_abstain: int | None = None) -> np.ndarray:
    """Return pred with exact zeros where the trailing resolved score <= 0.

    At row t, use only rows s with s + ahead <= t (labels resolved by t).
    Score = sum over those rows of pred[s, a] * y[s, a] (a sign-agreement
    score). max_abstain caps how many assets may be zeroed per row: the
    lowest scores are zeroed first. Use the cap to keep coverage at or
    above a pre-registered floor."""
    y = resolved_labels(fr, ahead)
    contrib = np.nan_to_num(pred * y)
    cs = np.vstack([np.zeros((1, pred.shape[1])), np.cumsum(contrib, axis=0)])
    out = pred.copy()
    for t in range(pred.shape[0]):
        hi = t - ahead + 1                     # rows s < hi have resolved by t
        if hi < min_hist:
            continue
        score = cs[hi] - cs[max(0, hi - window)]
        bad = np.where(score <= 0)[0]
        if max_abstain is not None and len(bad) > max_abstain:
            bad = bad[np.argsort(score[bad])[:max_abstain]]
        out[t, bad] = 0.0
    return out


def _causality_selftest(seed: int = 0) -> None:
    """The gate at row t must not change when data after row t is altered."""
    rng = np.random.default_rng(seed)
    T, A = 8000, 20
    fr = np.cumsum(rng.normal(0, 1e-6, (T, A)), axis=0)
    pred = np.tanh(rng.normal(0, 1, (T, A)))
    g1 = gate(pred, fr)
    t_cut = 5000
    fr2, pred2 = fr.copy(), pred.copy()
    fr2[t_cut + 1:] = rng.normal(0, 1, (T - t_cut - 1, A))
    pred2[t_cut + 1:] = -pred2[t_cut + 1:]
    g2 = gate(pred2, fr2)
    assert np.array_equal(g1[: t_cut + 1], g2[: t_cut + 1]), "LOOKAHEAD LEAK"
    capped = gate(pred, fr, max_abstain=3)
    assert ((capped == 0).sum(axis=1) <= 3).all()
    print("causality self-test PASS: gate output at rows <= t is independent of data after t; cap respected")


if __name__ == "__main__":
    _causality_selftest()
