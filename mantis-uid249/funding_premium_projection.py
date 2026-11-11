"""F2 experimental funding premium projection, interval-safe reference.

NOT PRODUCTION-READY. The MANTIS ``latest_prices.json.funding_rates`` feed's
venue, symbol mappings, funding intervals, premium sampling and publication
semantics have NOT been authenticated. This module only models Binance's
POST-2025-09-18 formula when that venue/model is explicitly configured.

No network or wallet operations. All input must be historical as-of data.
Source: Binance announcement c00588a7e8504b3eb28d02a2da00530b.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class Venue:
    # Explicit, per-symbol, as-of contract parameters (do not infer defaults).
    interval_min: int
    interest: float                  # Reference 8-hour interest, NOT N-hour
    clamp_c: float                   # Absolute funding interest/premium damper
    floor: float                     # Signed contract-specific funding floor
    cap: float                       # Contract-specific funding cap
    weighting: str                   # 'linear' (illustrative 1-min weights) or 'uniform'
    semantics: str                   # 'estimated' or 'settled'
    formula: str                     # MUST be 'binance_post_2025_09_18'
    settlement_offset_min: int = 0   # Offset from 00:00 UTC in minute-index coordinates

    def validate(self) -> None:
        if self.formula != 'binance_post_2025_09_18':
            raise ValueError('unverified funding formula / venue: fail closed')
        if type(self.interval_min) is not int or self.interval_min not in (60, 240, 480):
            raise ValueError('unsupported funding interval: verify actual symbol')
        if type(self.settlement_offset_min) is not int or not 0 <= self.settlement_offset_min < self.interval_min:
            raise ValueError('invalid settlement offset')
        if self.weighting not in ('linear', 'uniform'):
            raise ValueError('unknown weighting scheme')
        if self.semantics not in ('estimated', 'settled'):
            raise ValueError('unknown funding-feed semantics')
        if not all(math.isfinite(v) for v in (self.interest, self.clamp_c, self.floor, self.cap)):
            raise ValueError('nonfinite contract funding term')
        if self.clamp_c < 0 or self.floor >= self.cap:
            raise ValueError('invalid clamp or cap/floor')


def ar1_fit(p_hist: np.ndarray) -> tuple[float, float]:
    """Fit strictly on one contiguous ending segment; do not compress NaN gaps.

    The caller must provide one sample per real UTC minute; missing minutes must
    be represented by NaN. This is an illustrative premium-only projection.
    """
    x = np.asarray(p_hist, dtype=np.float64)
    if x.ndim != 1 or not x.size:
        raise ValueError('no premium history')
    missing = np.flatnonzero(~np.isfinite(x))
    if missing.size:
        x = x[int(missing[-1]) + 1:]
    if x.size < 100:
        raise ValueError('less than 100 contiguous fresh premium minutes')
    mu = float(np.mean(x))
    if float(np.ptp(x)) <= 1e-15:
        return mu, 0.0
    a, b = x[:-1] - mu, x[1:] - mu
    den = float(a @ a)
    phi = float(np.clip(float(a @ b) / den, 0.0, 0.99999)) if den > 0 else 0.0
    return mu, phi


def _rate(avg_p: float, v: Venue) -> float:
    """Binance post-2025-09-18 reference: [P+clamp(I8-P)]*(N/8).

    A venue's actual published bounds and exception-interest must be supplied.
    This is not an exact MANTIS rate until the source feed is matched as-of.
    """
    v.validate()
    if not math.isfinite(avg_p):
        raise ValueError('invalid average premium')
    unbounded_8h = avg_p + float(np.clip(v.interest - avg_p, -v.clamp_c, v.clamp_c))
    per_interval = unbounded_8h * (v.interval_min / 480.0)
    return float(np.clip(per_interval, v.floor, v.cap))


def _window_avg(t: int, ws: int, we: int, p_obs: np.ndarray,
                mu: float, phi: float, v: Venue) -> float:
    """Expected weighted premium [ws,we), conditioning on data through t.

    The time axis MUST be absolute UTC minute-indexed, with integer t
    referring to the real time of p_obs[t]. Using a file's arbitrary first row
    as t=0 without UTC alignment is a configuration error.

    One-minute weights are an approximation; exchange 5-second weights need
    an exchange-specific adapter and replay check before scoring.
    """
    if not (0 <= ws < we and 0 <= t < len(p_obs)):
        raise ValueError('invalid window or observation index')
    if not (math.isfinite(mu) and math.isfinite(phi) and 0 <= phi < 1):
        raise ValueError('invalid premium model parameters')
    mins = np.arange(ws, we, dtype=np.int64)
    obs = mins <= t
    pt = float(p_obs[t])
    if not math.isfinite(pt):
        raise ValueError('current premium absent / stale')
    vals = np.empty(mins.size, dtype=np.float64)
    vals[obs] = p_obs[mins[obs]]
    if not np.isfinite(vals[obs]).all():
        raise ValueError('missing observed premium within funding window')
    gap = mins[~obs] - t
    vals[~obs] = mu + (phi ** gap) * (pt - mu)
    w = np.arange(1, mins.size + 1, dtype=np.float64) if v.weighting == 'linear' else np.ones(mins.size)
    return float((w @ vals) / w.sum())


def projected_feed_rate(t: int, target: int, p_obs: np.ndarray,
                        mu: float, phi: float, v: Venue) -> float:
    """Estimate feed at target using premiums strictly available through t.

    Explicit settlement_offset_min is required for intervals not aligned to
    midnight UTC. Never infer a contract's funding clock from this function.
    """
    v.validate()
    if target < 0 or target < t:
        raise ValueError('target cannot precede observation time')
    L, offset = v.interval_min, v.settlement_offset_min
    boundary = ((target - offset) // L) * L + offset
    if v.semantics == 'estimated':
        ws, we = boundary, target + 1
    else:
        ws, we = boundary - L, boundary
    return _rate(_window_avg(t, ws, we, p_obs, mu, phi, v), v)


def feature_scores(t: int, p_obs_by_asset: np.ndarray, venues: list[Venue],
                   h: int = 480, fit_window: int = 14 * 1440) -> np.ndarray:
    """Return a candidate 20-D signal, not a validated MANTIS submission.

    Contiguous per-minute timestamp alignment and as-of sourcing are caller
    preconditions. True zero forecasts remain zero (no scorer gaming).
    """
    if p_obs_by_asset.ndim != 2 or len(venues) != p_obs_by_asset.shape[0]:
        raise ValueError('one verified venue term per asset is required')
    A, total = p_obs_by_asset.shape
    if not 0 <= t < total or h <= 0 or fit_window < 100:
        raise ValueError('invalid scoring time or horizon')
    x = np.full(A, np.nan, dtype=np.float64)
    for a, v in enumerate(venues):
        v.validate()  # bad provenance fails the entire batch, no silent fallback
        p = np.asarray(p_obs_by_asset[a, :t + 1], dtype=np.float64)
        try:
            mu, phi = ar1_fit(p[max(0, t + 1 - fit_window):])
            x[a] = (projected_feed_rate(t, t + h, p, mu, phi, v)
                    - projected_feed_rate(t, t, p, mu, phi, v))
        except ValueError:
            # Missing premium samples mean this particular asset is unavailable.
            continue
    ok = np.isfinite(x)
    if ok.sum() < 3:
        return np.zeros(A, dtype=np.float64)
    med = float(np.median(x[ok]))
    mad = float(1.4826 * np.median(np.abs(x[ok] - med)))
    if not mad > 0:
        return np.zeros(A, dtype=np.float64)
    scores = np.zeros(A, dtype=np.float64)
    scores[ok] = np.tanh((x[ok] - med) / mad)
    return scores
