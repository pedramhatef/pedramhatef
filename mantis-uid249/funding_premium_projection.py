"""Candidate F2: mechanical funding-rate projection from the premium index (FUNDINGXSEC).

Status: DESIGN. Only the reference implementation and its synthetic unit tests exist.
No real-data result. Nothing here has been evaluated on the 258-peer field.

Idea. Perp funding is a formula of the premium index averaged over each funding interval:
    F = avgP + clamp(I - avgP, -c, +c)        (then the venue cap/floor)
The rate the validator feed shows at t+h comes from an averaging window that is partly
observed by t and partly in the future. The observed part is known exactly. The future part
has a conditional expectation under the premium's own AR(1) mean reversion, fitted on
premium history only. No label is used, so nothing is fitted to the target.

Feature for asset a at time t:  x_a = F_hat(t+h) - F_hat(t), with h = 480 min (8h).
Submission: s_a = tanh((x_a - median_b x_b) / (1.4826 * MAD_b x_b)). This is cross-sectional
and matches the label (change relative to the cross-asset median).

Two feed semantics, pinned by the P0 provenance check BEFORE any label is examined:
  "estimated": the feed shows the running estimate for the CURRENT interval (it moves every minute).
  "settled":   the feed shows the LAST SETTLED rate (a step function that changes at settlements).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Venue:
    interval_min: int = 480        # funding interval (some symbols use 240 or 60)
    interest: float = 0.0001       # I per interval
    clamp_c: float = 0.0005        # interest-premium clamp
    cap: float = 0.02              # |F| cap (per-symbol on real venues)
    weighting: str = "linear"      # "linear" (Binance-style 1..n weights) or "uniform"
    semantics: str = "estimated"   # "estimated" or "settled"


def ar1_fit(p_hist: np.ndarray) -> tuple[float, float]:
    """(mu, phi) per minute, fitted on premium history only. phi is clipped to [0, 0.99999]."""
    x = p_hist[np.isfinite(p_hist)]
    if x.size < 100:
        return float(np.nanmean(x)) if x.size else 0.0, 0.0
    mu = float(np.mean(x))
    a, b = x[:-1] - mu, x[1:] - mu
    den = float(a @ a)
    phi = float(np.clip((a @ b) / den, 0.0, 0.99999)) if den > 0 else 0.0
    return mu, phi


def _rate(avg_p: float, v: Venue) -> float:
    f = avg_p + np.clip(v.interest - avg_p, -v.clamp_c, v.clamp_c)
    return float(np.clip(f, -v.cap, v.cap))


def _window_avg(t: int, ws: int, we: int, p_obs: np.ndarray, mu: float, phi: float, v: Venue) -> float:
    """Expected weighted average premium over minutes [ws, we), conditional on data up to minute t.

    p_obs holds minute premiums indexed by absolute minute. Only entries <= t are read."""
    mins = np.arange(ws, we)
    if mins.size == 0:
        return np.nan
    pt = p_obs[t]
    vals = np.empty(mins.size)
    obs = mins <= t
    vals[obs] = p_obs[mins[obs]]
    k = mins[~obs] - t
    vals[~obs] = mu + (phi ** k) * (pt - mu)
    w = np.arange(1, mins.size + 1, dtype=float) if v.weighting == "linear" else np.ones(mins.size)
    good = np.isfinite(vals)
    return float((w[good] @ vals[good]) / w[good].sum()) if good.any() else np.nan


def projected_feed_rate(t: int, target: int, p_obs: np.ndarray, mu: float, phi: float, v: Venue) -> float:
    """E[feed funding value at minute `target` | premium up to minute t]. Intervals start at multiples of interval_min."""
    L = v.interval_min
    if v.semantics == "estimated":
        ws = (target // L) * L
        we = target + 1                       # partial window up to and including target
    else:                                     # settled: last interval that ended at or before target
        we = (target // L) * L
        ws = we - L
    return _rate(_window_avg(t, ws, we, p_obs, mu, phi, v), v)


def feature_scores(t: int, p_obs_by_asset: np.ndarray, venues: list[Venue], h: int = 480,
                   fit_window: int = 14 * 1440) -> np.ndarray:
    """Cross-sectional scores in [-1, 1] for all assets at minute t. Reads only p_obs[:, :t+1]."""
    A = p_obs_by_asset.shape[0]
    x = np.full(A, np.nan)
    for a in range(A):
        p = p_obs_by_asset[a, : t + 1]
        mu, phi = ar1_fit(p[max(0, t + 1 - fit_window):])
        x[a] = (projected_feed_rate(t, t + h, p, mu, phi, venues[a])
                - projected_feed_rate(t, t, p, mu, phi, venues[a]))
    ok = np.isfinite(x)
    if ok.sum() < 3:
        return np.zeros(A)
    med = np.median(x[ok])
    mad = 1.4826 * np.median(np.abs(x[ok] - med))
    if not mad > 0:
        return np.zeros(A)
    s = np.where(ok, np.tanh((x - med) / mad), 0.0)
    # Exact 0.0 means "missing" to funding_xsec.py; nudge true zeros so a real forecast is never dropped.
    s[ok & (s == 0.0)] = 1e-6
    return s


def _selftest() -> None:
    rng = np.random.default_rng(0)
    A, T = 6, 20 * 1440
    v = [Venue() for _ in range(A)]
    p = np.zeros((A, T))
    for a in range(A):
        for i in range(1, T):
            p[a, i] = 0.999 * p[a, i - 1] + rng.normal(0, 2e-5)

    # 1. Causality: output at t cannot depend on premium after t.
    t = 16 * 1440 + 123
    s1 = feature_scores(t, p, v)
    p2 = p.copy(); p2[:, t + 1:] = rng.normal(0, 1, (A, T - t - 1))
    assert np.array_equal(s1, feature_scores(t, p2, v)), "LOOKAHEAD LEAK"

    # 2. Formula: constant premium q gives rate = q + clamp(I - q, +-c).
    for q in (0.0, 0.0003, 0.002, -0.004):
        const = np.full(3000, q)
        r = projected_feed_rate(2000, 2400, const, q, 0.5, Venue())
        exp = q + np.clip(0.0001 - q, -0.0005, 0.0005)
        assert abs(r - exp) < 1e-12, (q, r, exp)
    # Inside the clamp band the rate is pinned to the interest rate.
    assert abs(projected_feed_rate(2000, 2400, np.full(3000, 0.0003), 0.0003, 0.5, Venue()) - 0.0001) < 1e-12

    # 3. A fully observed settled window equals the realized weighted average (no projection used).
    vs = Venue(semantics="settled")
    pp = rng.normal(0, 1e-3, 3000)
    ws, we = 960, 1440
    w = np.arange(1, we - ws + 1, dtype=float)
    exp = _rate(float(w @ pp[ws:we] / w.sum()), vs)
    assert abs(projected_feed_rate(1500, 1500, pp, 0.0, 0.9, vs) - exp) < 1e-12

    # 4. Mechanism sanity: if the premium jumped above the running average, projected change is positive.
    pj = np.zeros(3000); pj[1990:] = 0.003      # jump inside the current interval [1920, 2400)
    assert projected_feed_rate(2000, 2480, pj, 0.0, 0.9999, Venue()) > projected_feed_rate(2000, 2000, pj, 0.0, 0.9999, Venue())

    assert np.all(np.abs(s1) <= 1) and np.all(s1 != 0)
    print("F2 self-test PASS: causal at t; formula and clamp exact; settled window exact; jump sign correct; outputs in [-1,1], no exact zeros")


if __name__ == "__main__":
    _selftest()
