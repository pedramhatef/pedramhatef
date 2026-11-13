#!/usr/bin/env python3
# MIT License
#
# Copyright (c) 2026 the sn89-kit authors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""sn89_label.py -- label one SN89 LF ("Signals") call as WON / LOST / WASH from candles.

Stdlib only, Python 3.10+. A re-implementation (not an import) of the SN89
validator's touch grader, ``sn89_signals/grader.py::_grade_touch_ticks`` at repo
HEAD 2e48ae8, adapted to OHLC candles.

What SN89 does (ticks, t0 >= 2026-07-25)
----------------------------------------
* entry  = price of the LAST tick at or before t0 (t0 = commit-block timestamp)
* TP     = entry * (1 + s*tp_bps/1e4), SL = entry * (1 - s*sl_bps/1e4), s = +1 LONG / -1 SHORT
           (on the current board TP == SL, symmetric 1:1)
* walk every tick with t0 < t <= t0 + horizon; per tick SL is tested FIRST, then TP;
  a level "scores" once MIN_TOUCH_TICKS = 2 ticks have touched it (counts are
  CUMULATIVE over the window, not consecutive). First level to score decides.
* nothing scores by the horizon  =>  WASHED (non-decisive)
* horizon by asset class: crypto/equities/indices/commodities 8 h, forex and
  metals ("forex-commodities") 12 h.

How candles approximate it (documented approximations)
------------------------------------------------------
1. ENTRY (``entry="interp"``, default): linear (Brownian-bridge) interpolation at t0
   between the close of the candle that ended before t0 and the close of the candle
   containing t0. It reads one candle close AFTER t0, which is fine for an outcome
   label (never feed it to the model). Measured against official grades it beats
   ``"prev_close"`` (last close at/before t0, up to one interval stale) at every
   interval: 3-way agreement 97.3 vs 96.0 % on 1m, 94.3 vs 91.2 % on 15m, 87.5 vs
   82.0 % on 1h, and it removes most of the hit-rate bias a stale entry gives
   momentum-timed calls on coarse candles. ``"open"``/``"containing_close"`` are
   diagnostics.
2. FIRST CANDLE (the one containing t0) holds prices from BEFORE t0, so its high/low
   may be pre-entry. Default ``first_candle="close_only"``: only its CLOSE is tested
   (a close beyond a level proves a post-t0 crossing). ``"include"`` treats its whole
   range as post-t0, ``"exclude"`` skips it.
3. LAST CANDLE (the one containing t0+horizon) holds prices from AFTER the horizon.
   Default ``last_candle="include"`` (a small bias toward decisive); ``"exclude"`` drops it.
4. TWO-TICK RULE. A candle's high/low is one traded print; whether >= 2 ticks touched
   the level is unknown. A candle touching a level SCORES when
       (a) its extreme passes the level by >= max(``pierce_bps``, ``pierce_frac`` x band)
           (defaults 0 bps / 0.02 => 2 % of the band, ~2-7 bps on crypto), or
       (b) its close is at/beyond the level (price persisted), or
       (c) an earlier candle in the window already touched the same level
           (cumulative count => certainly >= 2 ticks).
   pierce_bps = pierce_frac = 0 is the plain "wick" rule. Measured against official
   grades the choice moves agreement by < 0.5 pp; 2 % of band reproduced the official
   crypto wash rate best (see README), so it is the default.
5. ORDER INSIDE ONE CANDLE is unknown. If TP and SL both score inside the same candle
   the call is resolved CONSERVATIVELY as LOST (SN89 tests SL first per tick) and
   flagged ``ambiguous=True`` so you can measure / exclude it. Use finer candles to
   shrink the ambiguous share (it scales roughly with the interval).
6. Prices: HL candles are TRADE prices. SN89's tick line for HL markets mixes trades
   and book mids; for FX it is a different feed altogether (see hl_data.py).

CLI (one-off)
-------------
    python sn89_label.py --pair BTCUSD --direction LONG --t0 2026-11-10T14:05:00Z \\
                         [--cache ./hl_cache] [--interval auto] [--fetch]
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone

WON, LOST, WASHED, PENDING, NODATA = "won", "lost", "washed", "pending", "nodata"

CLASS_HORIZON_H = {"crypto": 8, "forex": 12, "forex-commodities": 12,
                   "equities": 8, "indices": 8, "commodities": 8}
DEFAULT_HORIZON_H = 72
MIN_TOUCH_TICKS = 2          # documented; the candle approximation is described above

SESSION_BOUND_CLASSES = ("forex", "forex-commodities", "equities", "indices", "commodities")
FX_MIN_OPEN_FRACTION = 0.25


# --- board ---------------------------------------------------------------------
@dataclass
class Band:
    pair: str
    asset_class: str
    tp_bps: float
    sl_bps: float
    horizon_h: int


def load_bands(path: str) -> dict[str, Band]:
    """Load sn89_bands.json (or the repo's raw signals-bands.json)."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    out = {}
    for p, v in doc["bands"].items():
        cls = v["asset_class"]
        out[p] = Band(p, cls, float(v["tp_bps"]), float(v["sl_bps"]),
                      int(v.get("horizon_h", CLASS_HORIZON_H.get(cls, DEFAULT_HORIZON_H))))
    return out


def class_horizon_h(asset_class: str) -> int:
    return CLASS_HORIZON_H.get(asset_class, DEFAULT_HORIZON_H)


# --- FX-week session calendar (re-implementation of sessions.py) ----------------
def _nth_sunday(year: int, month: int, n: int) -> int:
    first = date(year, month, 1)
    first_sunday = 1 + (6 - first.weekday()) % 7
    return first_sunday + 7 * (n - 1)


def _dst_span_utc(year: int) -> tuple[float, float]:
    start = datetime(year, 3, _nth_sunday(year, 3, 2), 7, 0, tzinfo=timezone.utc).timestamp()
    end = datetime(year, 11, _nth_sunday(year, 11, 1), 6, 0, tzinfo=timezone.utc).timestamp()
    return start, end


def ny_utc_offset_h(ts: float) -> int:
    year = datetime.fromtimestamp(ts, tz=timezone.utc).year
    start, end = _dst_span_utc(year)
    return -4 if start <= ts < end else -5


def fx_market_closed(ts: float) -> bool:
    """True when the FX week (Sun 17:00 -> Fri 17:00 New York) is shut at ts."""
    ny = datetime.fromtimestamp(ts + ny_utc_offset_h(ts) * 3600, tz=timezone.utc)
    wd, hour = ny.weekday(), ny.hour
    if wd == 5:
        return True
    if wd == 4 and hour >= 17:
        return True
    if wd == 6 and hour < 17:
        return True
    return False


def open_fraction(t0_unix: float, horizon_h: float) -> float:
    """Share of [t0, t0+horizon) that falls in an open FX session (60 s grid)."""
    if horizon_h <= 0:
        return 0.0
    end = t0_unix + horizon_h * 3600.0
    total, t = 0.0, float(t0_unix)
    while t < end:
        step = min(60.0, end - t)
        if not fx_market_closed(t):
            total += step
        t += 60.0
    return total / (horizon_h * 3600.0)


def session_void_reason(asset_class: str, t0_unix: float, horizon_h: float) -> str | None:
    """Why SN89 would refuse/void a session-bound call at t0, else None.

    The miner refuses a submit while the FX week is closed; the validator voids a
    call whose horizon is < 25 % open market (fx_dead_horizon)."""
    if asset_class not in SESSION_BOUND_CLASSES:
        return None
    if fx_market_closed(t0_unix):
        return "market_closed"
    if open_fraction(t0_unix, horizon_h) < FX_MIN_OPEN_FRACTION:
        return "fx_dead_horizon"
    return None


# --- labeler ---------------------------------------------------------------------
@dataclass
class LabelConfig:
    entry: str = "interp"              # interp | prev_close | open | containing_close
    first_candle: str = "close_only"   # close_only | include | exclude
    last_candle: str = "include"       # include | exclude
    pierce_bps: float = 0.0            # single-candle touch must pass the level by >= this (bps) ...
    pierce_frac: float = 0.02          # ... or by >= this fraction of the band, whichever is larger
    ambiguous: str = "lost"            # lost | won  (same-candle TP+SL resolution)


DEFAULT_CONFIG = LabelConfig()


@dataclass
class Label:
    status: str
    ambiguous: bool = False
    entry_px: float | None = None
    tp_px: float | None = None
    sl_px: float | None = None
    exit_ms: int | None = None           # open time of the deciding candle
    resolve_ms: int | None = None        # when the outcome became known (exit candle end / horizon)
    mark_bps: float | None = None        # signed move at horizon (washes) / at exit
    mfe_bps: float | None = None         # max favourable excursion in window (signed)
    mae_bps: float | None = None         # max adverse excursion in window (signed, <= 0)
    interval_ms: int | None = None
    n_candles: int = 0
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _touch(px_hi: float, px_lo: float, level: float, up: bool) -> bool:
    return px_hi >= level if up else px_lo <= level


def label_candles(t: list[int], o: list[float], h: list[float], l: list[float],
                  c: list[float], interval_ms: int, t0_ms: int, is_long: bool,
                  tp_bps: float, sl_bps: float, horizon_ms: int,
                  cfg: LabelConfig = DEFAULT_CONFIG,
                  now_ms: int | None = None) -> Label:
    """Label one call on parallel candle arrays (sorted by open time ``t``, ms).

    Returns a Label; ``status`` is won/lost/washed, or pending (horizon not yet
    reached by ``now_ms``) / nodata (series does not cover the window)."""
    iv = int(interval_ms)
    if not t:
        return Label(NODATA, note="empty series")
    end_ms = t0_ms + horizon_ms
    k = bisect.bisect_right(t, t0_ms) - 1          # candle containing t0
    if k < 0 or t0_ms >= t[k] + iv:
        return Label(NODATA, note="no candle at t0", interval_ms=iv)
    # entry
    if cfg.entry == "prev_close":
        if k - 1 < 0 or t[k - 1] + iv != t[k]:
            # t0 on the very first candle or a hole before it: fall back to open
            entry = o[k]
        else:
            entry = c[k - 1]
    elif cfg.entry == "open":
        entry = o[k]
    elif cfg.entry == "containing_close":
        entry = c[k]
    elif cfg.entry == "interp":
        # Brownian-bridge expectation of the price at t0 between the containing
        # candle's open boundary (prev close) and its close: uses the close, which is
        # AFTER t0, so it is an outcome-side estimate, never a model feature.
        p0 = c[k - 1] if (k - 1 >= 0 and t[k - 1] + iv == t[k]) else o[k]
        f = (t0_ms - t[k]) / iv
        entry = p0 + f * (c[k] - p0)
    else:
        raise ValueError(cfg.entry)
    if not entry or entry <= 0:
        return Label(NODATA, note="bad entry price", interval_ms=iv)
    sign = 1.0 if is_long else -1.0
    tp_px = entry * (1 + sign * tp_bps / 1e4)
    sl_px = entry * (1 - sign * sl_bps / 1e4)
    tp_pierce = max(cfg.pierce_bps, cfg.pierce_frac * tp_bps) / 1e4
    sl_pierce = max(cfg.pierce_bps, cfg.pierce_frac * sl_bps) / 1e4
    tp_strong_px = entry * (1 + sign * (tp_bps / 1e4 + tp_pierce))
    sl_strong_px = entry * (1 - sign * (sl_bps / 1e4 + sl_pierce))
    up_tp = is_long            # TP is above entry for LONG
    m = bisect.bisect_left(t, end_ms) - 1          # last candle opening before the horizon end
    last_complete = t[-1] + iv
    lab = Label(WASHED, entry_px=entry, tp_px=tp_px, sl_px=sl_px, interval_ms=iv)
    tp_cnt = sl_cnt = 0
    mfe = mae = 0.0
    n = 0
    j = k
    prev_t = None
    while j < len(t) and t[j] < end_ms:
        tj = t[j]
        if prev_t is not None and tj - prev_t != iv:
            lab.note = "gap"
        prev_t = tj
        partial_first = (j == k and t0_ms > tj)
        is_last = (tj + iv > end_ms)              # straddles the horizon end
        if is_last and cfg.last_candle == "exclude" and j != k:
            break
        if partial_first:
            if cfg.first_candle == "exclude":
                j += 1
                continue
            if cfg.first_candle == "close_only":
                hi = lo = c[j]
            else:
                hi, lo = h[j], l[j]
        else:
            hi, lo = h[j], l[j]
        n += 1
        # excursions (signed, bps)
        fav = ((hi - entry) if is_long else (entry - lo)) / entry * 1e4
        adv = ((lo - entry) if is_long else (entry - hi)) / entry * 1e4
        mfe = max(mfe, fav)
        mae = min(mae, adv)
        tp_t = _touch(hi, lo, tp_px, up_tp)
        sl_t = _touch(hi, lo, sl_px, not up_tp)
        if tp_t:
            tp_cnt += 1
        if sl_t:
            sl_cnt += 1
        cj = c[j]
        tp_sc = tp_t and (tp_cnt >= 2 or _touch(hi, lo, tp_strong_px, up_tp)
                          or _touch(cj, cj, tp_px, up_tp))
        sl_sc = sl_t and (sl_cnt >= 2 or _touch(hi, lo, sl_strong_px, not up_tp)
                          or _touch(cj, cj, sl_px, not up_tp))
        if tp_sc or sl_sc:
            lab.exit_ms = tj
            lab.resolve_ms = min(tj + iv, end_ms)
            if tp_sc and sl_sc:
                lab.ambiguous = True
                lab.status = WON if cfg.ambiguous == "won" else LOST
            else:
                lab.status = LOST if sl_sc else WON
            won = lab.status == WON
            lab.mark_bps = (tp_bps if won else -sl_bps)
            lab.mfe_bps, lab.mae_bps, lab.n_candles = mfe, mae, n
            return lab
        j += 1
    # nothing scored
    lab.mfe_bps, lab.mae_bps, lab.n_candles = mfe, mae, n
    if last_complete < end_ms or m < k:
        if now_ms is not None and now_ms < end_ms:
            lab.status = PENDING
        else:
            lab.status = NODATA
            lab.note = (lab.note + " short series").strip()
        return lab
    lab.status = WASHED
    lab.resolve_ms = end_ms
    mk = c[m] if m >= k else entry
    lab.mark_bps = sign * (mk - entry) / entry * 1e4
    return lab


def label_series(series, t0_ms: int, direction: str, tp_bps: float, sl_bps: float,
                 horizon_h: float, cfg: LabelConfig = DEFAULT_CONFIG,
                 now_ms: int | None = None) -> Label:
    """Label against an hl_data.CandleSeries (or any object with t,o,h,l,c,interval_ms)."""
    d = direction.strip().upper()
    if d not in ("LONG", "SHORT"):
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")
    return label_candles(series.t, series.o, series.h, series.l, series.c,
                         series.interval_ms, int(t0_ms), d == "LONG",
                         float(tp_bps), float(sl_bps), int(horizon_h * 3_600_000),
                         cfg=cfg, now_ms=now_ms)


def _parse_t0(s: str) -> int:
    s = s.strip()
    try:
        v = float(s)
        return int(v if v > 1e11 else v * 1000)
    except ValueError:
        pass
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import hl_data  # noqa: E402  (sibling module)

    ap = argparse.ArgumentParser(description="Label one SN89 LF call from HL candles.")
    ap.add_argument("--pair", required=True)
    ap.add_argument("--direction", required=True, choices=["LONG", "SHORT", "long", "short"])
    ap.add_argument("--t0", required=True, help="epoch s/ms or ISO-8601 UTC")
    ap.add_argument("--bands", default=os.path.join(here, "sn89_bands.json"))
    ap.add_argument("--tp-bps", type=float, help="override board TP (bps)")
    ap.add_argument("--sl-bps", type=float, help="override board SL (bps)")
    ap.add_argument("--cache", default=os.path.join(here, "hl_cache"))
    ap.add_argument("--interval", default="auto", help="auto (finest cached) or 1m/5m/...")
    ap.add_argument("--fetch", action="store_true", help="fetch candles if missing")
    ap.add_argument("--pierce-bps", type=float, default=0.0)
    ap.add_argument("--pierce-frac", type=float, default=0.02)
    a = ap.parse_args(argv)
    pair = a.pair.upper()
    bands = load_bands(a.bands)
    if pair not in bands and (a.tp_bps is None or a.sl_bps is None):
        print(f"{pair} not on the board; pass --tp-bps/--sl-bps", file=sys.stderr)
        return 2
    b = bands.get(pair)
    tp = a.tp_bps if a.tp_bps is not None else b.tp_bps
    sl = a.sl_bps if a.sl_bps is not None else b.sl_bps
    hz = b.horizon_h if b else 8
    t0 = _parse_t0(a.t0)
    start, end = t0 - 2 * 3_600_000, t0 + hz * 3_600_000 + 3_600_000
    coin = hl_data.hl_coin(pair)
    if coin is None:
        print(f"{pair}: {hl_data.UNSUPPORTED.get(pair, 'no HL mapping')}", file=sys.stderr)
        return 2
    ivs = hl_data.INTERVAL_ORDER if a.interval == "auto" else [a.interval]
    if a.fetch:
        for iv in ivs:
            try:
                hl_data.update_cache(a.cache, coin, iv, start, end)
            except Exception as e:  # noqa: BLE001
                print(f"fetch {iv}: {e}", file=sys.stderr)
            s = hl_data.load_series(a.cache, pair, iv)
            if len(s) and s.covers(t0 - s.interval_ms, t0 + hz * 3_600_000):
                break
    s = hl_data.finest_series(a.cache, pair, t0 - 3_600_000, t0 + hz * 3_600_000, ivs)
    if s is None:
        print("no cached series covers the window (use --fetch)", file=sys.stderr)
        return 3
    lab = label_series(s, t0, a.direction, tp, sl, hz,
                       LabelConfig(pierce_bps=a.pierce_bps, pierce_frac=a.pierce_frac),
                       now_ms=int(datetime.now(timezone.utc).timestamp() * 1000))
    out = lab.as_dict()
    out.update({"pair": pair, "direction": a.direction.upper(), "t0_ms": t0,
                "tp_bps": tp, "sl_bps": sl, "horizon_h": hz, "series": s.source})
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
