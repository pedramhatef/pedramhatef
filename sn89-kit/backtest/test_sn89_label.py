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
"""Unit tests for the SN89 backtest harness (stdlib unittest; no network).

    python -m unittest -v test_sn89_label        # or: python test_sn89_label.py

The synthetic drift test checks the labeler against the closed form for Brownian
motion with drift mu and vol sigma and symmetric barriers +-b:

    P(TP first | a barrier is hit) = 1 / (1 + exp(-2 mu b / sigma^2))

which holds for ANY horizon (for a symmetric interval the exit side and exit time
are independent), so it applies to SN89's finite 8 h window conditioned on
decisive. Paths are simulated on a 10 s grid, aggregated to 1 m / 5 m OHLC candles,
and labelled exactly as real candles would be.
"""
from __future__ import annotations

import math
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sn89_label as L  # noqa: E402
import sn89_eval as E  # noqa: E402

M = 60_000
H8 = 8 * 3_600_000


def candles(rows, t_start=0, iv=M):
    """rows: list of (o,h,l,c) -> parallel arrays starting at t_start."""
    t = [t_start + i * iv for i in range(len(rows))]
    o = [r[0] for r in rows]
    h = [r[1] for r in rows]
    l_ = [r[2] for r in rows]
    c = [r[3] for r in rows]
    return t, o, h, l_, c


def flat(n, px=100.0):
    return [(px, px, px, px)] * n


class LabelBasics(unittest.TestCase):
    def lab(self, rows, t0, is_long, band=100.0, horizon=H8, cfg=L.DEFAULT_CONFIG, now=None):
        t, o, h, l_, c = candles(rows)
        return L.label_candles(t, o, h, l_, c, M, t0, is_long, band, band, horizon, cfg, now)

    def test_long_tp(self):
        rows = flat(10) + [(100, 101.5, 99.9, 101.2)] + flat(600, 101.2)
        lab = self.lab(rows, t0=5 * M, is_long=True)          # t0 on a candle boundary
        self.assertEqual(lab.status, L.WON)
        self.assertEqual(lab.exit_ms, 10 * M)
        self.assertAlmostEqual(lab.entry_px, 100.0)
        self.assertFalse(lab.ambiguous)

    def test_short_sl(self):
        rows = flat(10) + [(100, 101.5, 99.9, 101.2)] + flat(600, 101.2)
        lab = self.lab(rows, t0=5 * M, is_long=False)
        self.assertEqual(lab.status, L.LOST)

    def test_short_tp(self):
        rows = flat(10) + [(100, 100.1, 98.5, 98.8)] + flat(600, 98.8)
        self.assertEqual(self.lab(rows, t0=5 * M, is_long=False).status, L.WON)

    def test_wash_and_mark(self):
        rows = flat(10) + [(100, 100.5, 99.5, 100.4)] * 600
        lab = self.lab(rows, t0=5 * M, is_long=True)
        self.assertEqual(lab.status, L.WASHED)
        self.assertAlmostEqual(lab.mark_bps, 40.0, places=6)
        self.assertEqual(lab.resolve_ms, 5 * M + H8)

    def test_ambiguous_same_candle_is_lost(self):
        rows = flat(10) + [(100, 101.5, 98.5, 100.0)] + flat(600)
        lab = self.lab(rows, t0=5 * M, is_long=True)
        self.assertEqual(lab.status, L.LOST)
        self.assertTrue(lab.ambiguous)
        lab2 = self.lab(rows, t0=5 * M, is_long=True, cfg=L.LabelConfig(ambiguous="won"))
        self.assertEqual(lab2.status, L.WON)
        self.assertTrue(lab2.ambiguous)

    def test_prev_close_entry(self):
        rows = flat(5) + [(100, 100, 100, 102.0)] + [(102, 102.2, 101.9, 102.1)] * 600
        # t0 half-way through candle 6: prev_close entry = close of candle 5 (102.0) ...
        lab = self.lab(rows, t0=6 * M + 30_000, is_long=True,
                       cfg=L.LabelConfig(entry="prev_close"))
        self.assertAlmostEqual(lab.entry_px, 102.0)
        # ... default interp entry = halfway between 102.0 and candle 6's close 102.1
        lab = self.lab(rows, t0=6 * M + 30_000, is_long=True)
        self.assertAlmostEqual(lab.entry_px, 102.05)

    def test_first_candle_wick_before_t0_ignored(self):
        # candle containing t0 has a TP-level high that (by default) may be pre-t0
        rows = flat(5) + [(100, 101.5, 99.9, 100.0)] + flat(600)
        lab = self.lab(rows, t0=5 * M + 50_000, is_long=True)
        self.assertEqual(lab.status, L.WASHED)
        lab2 = self.lab(rows, t0=5 * M + 50_000, is_long=True,
                        cfg=L.LabelConfig(first_candle="include"))
        self.assertEqual(lab2.status, L.WON)

    def test_touch_after_horizon_not_counted(self):
        n = 8 * 60
        rows = flat(10) + flat(n) + [(100, 101.5, 99.9, 101.2)] + flat(10)
        # t0 = 10 min; horizon ends exactly at the open of the spike candle
        lab = self.lab(rows, t0=10 * M, is_long=True)
        self.assertEqual(lab.status, L.WASHED)

    def test_two_tick_proxy(self):
        # marginal wick: high passes TP by 0.5 bps (< 2 % of 100 bps), closes inside
        wick = (100, 101.005, 99.9, 100.0)
        rows = flat(10) + [wick] + flat(50) + [(100, 100.2, 99.9, 100.0)] * 600
        lab = self.lab(rows, t0=5 * M, is_long=True)
        self.assertEqual(lab.status, L.WASHED)            # one marginal touch: no score
        rows2 = flat(10) + [wick] + flat(50) + [wick] + flat(600)
        lab2 = self.lab(rows2, t0=5 * M, is_long=True)
        self.assertEqual(lab2.status, L.WON)               # second touching candle scores
        self.assertEqual(lab2.exit_ms, 61 * M)
        lab3 = self.lab(rows, t0=5 * M, is_long=True,
                        cfg=L.LabelConfig(pierce_frac=0.0))  # plain wick rule
        self.assertEqual(lab3.status, L.WON)

    def test_close_beyond_level_scores(self):
        rows = flat(10) + [(100, 101.01, 99.9, 101.01)] + flat(600, 100)   # close +1 bp past TP
        self.assertEqual(self.lab(rows, t0=5 * M, is_long=True).status, L.WON)

    def test_sl_first_then_tp(self):
        rows = flat(10) + [(100, 100.1, 98.8, 99.0)] + [(99, 101.5, 99, 101.2)] + flat(600)
        self.assertEqual(self.lab(rows, t0=5 * M, is_long=True).status, L.LOST)

    def test_pending_and_nodata(self):
        rows = flat(100)
        self.assertEqual(self.lab(rows, t0=5 * M, is_long=True, now=50 * M).status, L.PENDING)
        self.assertEqual(self.lab(rows, t0=5 * M, is_long=True, now=10 ** 12).status, L.NODATA)
        self.assertEqual(self.lab(rows, t0=500 * M, is_long=True).status, L.NODATA)


class Sessions(unittest.TestCase):
    def ts(self, s):
        return E.parse_t0(s)

    def test_calendar(self):
        self.assertTrue(L.fx_market_closed(self.ts("2026-11-14T12:00:00Z")))    # Saturday
        self.assertFalse(L.fx_market_closed(self.ts("2026-11-13T20:00:00Z")))   # Fri 15:00 NY (EST)
        self.assertTrue(L.fx_market_closed(self.ts("2026-11-13T22:30:00Z")))    # Fri 17:30 NY
        self.assertFalse(L.fx_market_closed(self.ts("2026-11-15T22:30:00Z")))   # Sun 17:30 NY
        self.assertTrue(L.fx_market_closed(self.ts("2026-11-15T21:30:00Z")))    # Sun 16:30 NY

    def test_dead_horizon(self):
        # Fri 16:30 NY (21:30Z, EST) + 8 h -> only 0.5 h open (< 25 %)
        self.assertEqual(L.session_void_reason("equities", self.ts("2026-11-13T21:30:00Z"), 8),
                         "fx_dead_horizon")
        self.assertIsNone(L.session_void_reason("equities", self.ts("2026-11-13T19:00:00Z"), 8))
        self.assertIsNone(L.session_void_reason("crypto", self.ts("2026-11-14T12:00:00Z"), 8))


class QuotaAndScoring(unittest.TestCase):
    def bands(self):
        return {"BTCUSD": L.Band("BTCUSD", "crypto", 105, 105, 8)}

    def test_quota(self):
        d0 = 1_794_268_800                     # 2026-11-10 00:00 UTC
        sigs = [{"row": i, "t0": d0 + x, "pair": "BTCUSD", "dir": "LONG", "prob": None}
                for i, x in enumerate([0, 3500, 3575, 7200, 10800, 14400, 86400])]
        E.apply_quota(sigs, self.bands())
        got = [(s["accepted"], s["void_reason"]) for s in sigs]
        # 3500 s < 3600-30 -> void; 3575 s ok (slack); 3rd call at 7200 ok; 4th+ same day void
        self.assertEqual(got, [(True, None), (False, "min_spacing"), (True, None),
                               (True, None), (False, "daily_quota"), (False, "daily_quota"),
                               (True, None)])

    def test_wilson_and_gate(self):
        lb, ub = E.wilson(6, 8)
        p, z, n = 0.75, 1.2816, 8
        ref = (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)
        self.assertAlmostEqual(lb, ref, places=12)
        self.assertEqual(E.min_wins_to_qualify(8), 6)
        self.assertFalse(E.is_qualified(5, 7))      # n < 8
        self.assertTrue(E.is_qualified(6, 8))

    def test_tier_and_efficiency(self):
        self.assertAlmostEqual(E.tier_ramp(0.50), 1.0)
        self.assertAlmostEqual(E.tier_ramp(0.575), 1.1)
        self.assertAlmostEqual(E.tier_ramp(0.65), 1.6)
        self.assertAlmostEqual(E.tier_ramp(0.80), 2.0)
        self.assertAlmostEqual(E.shrunk_hit(12, 16), 18 / 28)
        self.assertAlmostEqual(E.efficiency([], 1e9), 1 - 0.6 * 16 / 40)
        g = [(1e9 - i, True) for i in range(40)]           # 40 washes
        self.assertAlmostEqual(E.efficiency(g, 1e9), max(0.55, 1 - 0.6 * 56 / 80))

    def test_causal_window(self):
        day = 86400.0
        rows = []
        t = 0.0
        for i in range(8):                                  # 8 wins resolved quickly
            rows.append({"t0": t, "status": L.WON, "resolve": t + 3600})
            t += day
        # a loss opened before the next win but resolving after it must not count
        rows.append({"t0": t, "status": L.LOST, "resolve": t + 7 * 3600})
        rows.append({"t0": t + 2 * 3600, "status": L.WON, "resolve": t + 3 * 3600})
        wins = E.qualified_wins(rows, first_seen=-30 * day)
        last = wins[-1]
        self.assertEqual((last["rep_w"], last["rep_n"]), (9, 9))
        self.assertTrue(last["paid"])

    def test_warmup_never_pays(self):
        rows = [{"t0": i * 3600.0 * 8, "status": L.WON, "resolve": i * 3600.0 * 8 + 60}
                for i in range(20)]                         # all inside the first 8 days
        wins = E.qualified_wins(rows, first_seen=0.0)
        self.assertTrue(any(w["qualified"] for w in wins))
        self.assertFalse(any(w["paid"] for w in wins))

    def test_tally_decay_and_cap(self):
        now = 100 * 86400.0
        paid = [(now - 15 * 86400.0, 1.0)]
        self.assertAlmostEqual(E.tally_at(paid, now), 0.5)
        paid = [(now - i * 60.0, 1.0) for i in range(30)]
        self.assertLessEqual(E.tally_at(paid, now), 20.0)
        self.assertGreater(E.tally_at(paid, now), 19.9)


# --- synthetic drift test ----------------------------------------------------------
def simulate_paths(n_paths, mu_h, sigma_h, band, horizon_h, step_s, iv_ms, seed):
    """Arithmetic BM in fractional price units: X(t) = mu t + sigma W(t), price
    = 100 (1 + X). Returns list of candle arrays (t,o,h,l,c), one per path, with one
    flat candle before t0 = iv_ms (so prev_close entry = 100 exactly)."""
    rng = random.Random(seed)
    dt_h = step_s / 3600.0
    drift = mu_h * dt_h
    vol = sigma_h * math.sqrt(dt_h)
    per_candle = int(iv_ms / 1000 / step_s)
    n_candles = int(horizon_h * 3600 * 1000 / iv_ms)
    stop_at = band * 1.10
    out = []
    for _ in range(n_paths):
        t, o, h, l_, c = [0], [100.0], [100.0], [100.0], [100.0]
        x = 0.0
        for k in range(n_candles):
            po = 100.0 * (1 + x)
            hi = lo = po
            for _s in range(per_candle):
                x += drift + vol * rng.gauss(0.0, 1.0)
                px = 100.0 * (1 + x)
                if px > hi:
                    hi = px
                elif px < lo:
                    lo = px
            t.append((k + 1) * iv_ms)
            o.append(po)
            h.append(hi)
            l_.append(lo)
            c.append(100.0 * (1 + x))
            if abs(x) >= stop_at:                      # decided; pad flat to the horizon
                last = c[-1]
                for k2 in range(k + 1, n_candles):
                    t.append((k2 + 1) * iv_ms)
                    o.append(last)
                    h.append(last)
                    l_.append(last)
                    c.append(last)
                break
        t.append((n_candles + 1) * iv_ms)              # one candle past the horizon
        o.append(c[-1])
        h.append(c[-1])
        l_.append(c[-1])
        c.append(c[-1])
        out.append((t, o, h, l_, c))
    return out


def p_no_touch(z):
    """P(driftless BM stays inside +-b over T), z = sigma sqrt(T) / b."""
    s = 0.0
    for k in range(60):
        s += (-1) ** k / (2 * k + 1) * math.exp(-((2 * k + 1) ** 2) * math.pi ** 2 * z * z / 8)
    return 4 / math.pi * s


class SyntheticDrift(unittest.TestCase):
    BAND = 0.0100            # 100 bps
    SIGMA = 0.0040           # 40 bps / sqrt(h)
    H = 8

    def run_case(self, mu, iv_ms, n_paths, seed, is_long=True):
        paths = simulate_paths(n_paths, mu, self.SIGMA, self.BAND, self.H, 10, iv_ms, seed)
        w = l_ = x = amb = 0
        for t, o, h, lo, c in paths:
            lab = L.label_candles(t, o, h, lo, c, iv_ms, iv_ms, is_long, 100.0, 100.0,
                                  self.H * 3_600_000)
            if lab.status == L.WON:
                w += 1
            elif lab.status == L.LOST:
                l_ += 1
            elif lab.status == L.WASHED:
                x += 1
            amb += lab.ambiguous
        return w, l_, x, amb

    def check(self, mu, iv_ms, n_paths, seed, is_long=True, tol_se=3.5):
        w, l_, x, amb = self.run_case(mu, iv_ms, n_paths, seed, is_long)
        n = w + l_
        k = 2 * mu * self.BAND / self.SIGMA ** 2
        p_th = 1 / (1 + math.exp(-k)) if is_long else 1 / (1 + math.exp(k))
        p_hat = w / n
        se = math.sqrt(p_th * (1 - p_th) / n)
        msg = (f"mu={mu} iv={iv_ms // M}m: P(TP|dec) sim={p_hat:.4f} theory={p_th:.4f} "
               f"(n_dec={n}, se={se:.4f}, wash={x / (n + x):.3f}, amb={amb})")
        print("   ", msg)
        self.assertLess(abs(p_hat - p_th), tol_se * se + 0.005, msg)
        return x / (n + x)

    def test_drift_long_1m(self):
        mu = math.log(1.5) * self.SIGMA ** 2 / (2 * self.BAND)     # theory P = 0.60
        self.check(mu, M, 2500, seed=1)

    def test_drift_short_5m(self):
        mu = math.log(1.5) * self.SIGMA ** 2 / (2 * self.BAND)     # SHORT: theory P = 0.40
        self.check(mu, 5 * M, 2500, seed=2, is_long=False)

    def test_strong_drift_1m(self):
        mu = math.log(3.0) * self.SIGMA ** 2 / (2 * self.BAND)     # theory P = 0.75
        self.check(mu, M, 2000, seed=3)

    def test_no_drift_wash_rate(self):
        wash = self.check(0.0, M, 2500, seed=4)
        z = self.SIGMA * math.sqrt(self.H) / self.BAND
        th = p_no_touch(z)
        # discrete 10 s monitoring + the 2-touch proxy make wash slightly HIGHER
        self.assertLess(abs(wash - th), 0.04, f"wash sim {wash:.3f} vs continuous {th:.3f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
