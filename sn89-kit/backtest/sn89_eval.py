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
"""sn89_eval.py -- backtest a model's directional calls under SN89 LF rules.

Stdlib only, Python 3.10+. Uses hl_data.py (candles) and sn89_label.py (grader).

Subcommands
-----------
eval      signals CSV -> SN89 labels, hit/wash stats, Wilson LB, quota filter,
          causal qualification simulation, tier/efficiency/tally estimate,
          optional --prob-threshold sweep and calibration table.
baseline  random-time LONG/SHORT calls on each pair with the CURRENT board bands:
          wash rate, P(TP first | decisive), realized vs band-implied sigma,
          decisive calls/day. Re-run it weekly to pick pairs.
validate  re-grade the owner's public checkpoint (sn89_checkpoint.json) with HL
          candles and print the confusion matrix vs the official status.

Input CSV for ``eval`` (header required; extra columns ignored)::

    t0_unix,trade_pair,direction[,prob]
    1794300000,BTCUSD,LONG,0.66
    2026-11-10T14:05:00Z,ETHUSD,SHORT,0.71

``t0_unix`` may be epoch seconds, epoch ms or ISO-8601 UTC. ``t0`` is the time
the call would land on chain (SN89 entry = last price at/before the commit block
time). ``direction`` is LONG/SHORT (or 1/-1, BUY/SELL). ``prob`` is your model's
calibrated P(TP first | decisive) for the SN89 band; it is only used for the
threshold sweep and the calibration table.

What is applied, in order (mirrors scoring.apply_validity_filters at repo 2e48ae8)
------------------------------------------------------------------------------
1. ``--prob-threshold`` (if given): drop calls with prob < threshold.
2. not on the board / no HL data      -> dropped (reported).
3. session-bound classes (forex, metals, equities, indices, commodities): refused
   while the FX week is closed (Sun 17:00 -> Fri 17:00 New York) and voided when
   < 25 % of the horizon is open market.
4. >= 3600 s (- 30 s block-jitter slack) since the last ACCEPTED call (min_spacing).
5. <= 3 accepted calls per UTC day (daily_quota).
Voided calls consume no quota. Selection is greedy in time order (causal): the
first passing calls of each day are the ones submitted.

Qualification simulation (mirrors scoring.qualified_wins / decayed_qwin_tally)
-----------------------------------------------------------------------------
* first_seen = t0 of your FIRST accepted call (assumption: the validator journals
  your first SIGNAL at its commit); warmup ends first_seen + 8 d.
* Each WIN at t0 is QUALIFIED iff, over decisive rows with t0 in (t0-60 d, t0]
  that had RESOLVED by t0 (exit time <= t0) plus the win itself, capped to the 200
  most recent, n >= 8 and Wilson LB (z = 1.2816) >= 0.50. Warmup wins never pay.
* value = max(1, ramp(shrunk)) x efficiency, shrunk = (w+6)/(n+12), ramp linear
  through (0.55,1.0) (0.60,1.2) (0.70,2.0); efficiency = clamp(1 - 0.6 x
  (washes+16)/(graded+40), 0.55, 1) over won/lost/washed with t0 in [t0-60 d, t0].
* tally(now) = sum over the 20 most recent paid wins younger than 30 d of
  value x (1 - age/30 d). LF weight = tally / sum of all tallies; pass
  ``--field-tally`` (sum of everyone else's tallies) to convert to alpha/day.

Usage
-----
    python sn89_eval.py eval --signals my_calls.csv --fetch
    python sn89_eval.py eval --signals my_calls.csv --sweep 0.5:0.8:0.02 --oos-from 2026-10-15
    python sn89_eval.py baseline --pairs board --days 7 --interval 3m --fetch
    python sn89_eval.py validate --checkpoint sn89_checkpoint.json --fetch
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hl_data  # noqa: E402
import sn89_label as L  # noqa: E402

DAY_S = 86_400
# --- SN89 scoring constants (repo HEAD 2e48ae8, sn89_signals/config.py) --------
MAX_PER_DAY = 3
MIN_GAP_S = 3600
GAP_SLACK_S = 30
QUALIFY_Z = 1.2816
QUALIFY_LB_FLOOR = 0.50
QUALIFY_MIN_DECISIVE = 8
HIT_RATE_WINDOW_S = 60 * DAY_S
HIT_RATE_WINDOW_TRADES = 200
IMMUNITY_S = 8 * DAY_S
TIER_PRIOR_K = 12
WIN_RATE_TIERS = ((0.55, 1.0), (0.60, 1.2), (0.70, 2.0))
EFFICIENCY_SLOPE = 0.6
EFFICIENCY_MIN = 0.55
EFFICIENCY_PRIOR_WASH = 0.40
EFFICIENCY_PRIOR_K = 40
EMISSION_DECAY_S = 30 * DAY_S
WIN_CAP = 20
LF_POOL_ALPHA_PER_DAY = 885.6     # 30 % of SN89 miner emission (prior evaluation, 2026-11-13)
BAND_Z_40 = 1.0322471346          # b / (sigma sqrt(T)) at which a driftless BM has 40 % no-touch


# --- statistics -------------------------------------------------------------------
def wilson(w: int, n: int, z: float = QUALIFY_Z) -> tuple[float, float]:
    """Two-sided Wilson interval (lower, upper); identical to scoring._wilson."""
    if n <= 0:
        return (0.0, 1.0)
    p = w / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return ((centre - margin) / denom, (centre + margin) / denom)


def is_qualified(w: int, n: int) -> bool:
    return n >= QUALIFY_MIN_DECISIVE and wilson(w, n)[0] >= QUALIFY_LB_FLOOR


def shrunk_hit(w: int, n: int) -> float:
    return (w + TIER_PRIOR_K / 2.0) / (n + TIER_PRIOR_K)


def tier_ramp(r: float) -> float:
    asc = sorted(WIN_RATE_TIERS)
    if r <= asc[0][0]:
        return asc[0][1]
    if r >= asc[-1][0]:
        return asc[-1][1]
    for (a_th, a_m), (b_th, b_m) in zip(asc, asc[1:]):
        if a_th <= r < b_th:
            return a_m + (r - a_th) / (b_th - a_th) * (b_m - a_m)
    return asc[-1][1]


def efficiency(graded: list[tuple[float, bool]], t0: float) -> float:
    lo = t0 - HIT_RATE_WINDOW_S
    win = [w for t, w in graded if lo <= t <= t0]
    shr = (sum(1 for w in win if w) + EFFICIENCY_PRIOR_K * EFFICIENCY_PRIOR_WASH) / (
        len(win) + EFFICIENCY_PRIOR_K)
    return max(EFFICIENCY_MIN, min(1.0, 1.0 - EFFICIENCY_SLOPE * shr))


def min_wins_to_qualify(n: int) -> int | None:
    for w in range(n + 1):
        if is_qualified(w, n):
            return w
    return None


# --- qualification simulation -----------------------------------------------------
def qualified_wins(rows: list[dict], first_seen: float) -> list[dict]:
    """rows: dicts with t0 (s), status, resolve (s). Returns every WIN with its
    causal qualification verdict and value (value 0 when not paid)."""
    dec = sorted([r for r in rows if r["status"] in (L.WON, L.LOST)], key=lambda r: r["t0"])
    graded = sorted([(r["t0"], r["status"] == L.WASHED) for r in rows
                     if r["status"] in (L.WON, L.LOST, L.WASHED)])
    warm_end = first_seen + IMMUNITY_S
    out = []
    for i, r in enumerate(dec):
        if r["status"] != L.WON:
            continue
        t0 = r["t0"]
        cut = t0 - HIT_RATE_WINDOW_S
        window = [d for d in dec[:i] if d["t0"] >= cut and d["resolve"] <= t0]
        window.append(r)
        window = window[-HIT_RATE_WINDOW_TRADES:]
        rw = sum(1 for d in window if d["status"] == L.WON)
        rd = len(window)
        q = is_qualified(rw, rd)
        paid = q and t0 >= warm_end
        val = 0.0
        if paid:
            val = max(1.0, tier_ramp(shrunk_hit(rw, rd))) * efficiency(graded, t0)
        out.append({"t0": t0, "qualified": q, "paid": paid, "value": val,
                    "rep_w": rw, "rep_n": rd, "lb": wilson(rw, rd)[0]})
    return out


def tally_at(paid: list[tuple[float, float]], now: float) -> float:
    live = [(t, v) for t, v in paid if 0.0 <= now - t < EMISSION_DECAY_S]
    live.sort(key=lambda x: x[0], reverse=True)
    return sum(v * (1.0 - (now - t) / EMISSION_DECAY_S) for t, v in live[:WIN_CAP])


def simulate(rows: list[dict], field_tally: float | None = None,
             lf_pool: float = LF_POOL_ALPHA_PER_DAY) -> dict:
    """Causal qualification + tally path for one hotkey's accepted, labelled calls."""
    rows = [r for r in rows if r["status"] in (L.WON, L.LOST, L.WASHED)]
    if not rows:
        return {"calls": 0}
    rows.sort(key=lambda r: r["t0"])
    first = rows[0]["t0"]
    wins = qualified_wins(rows, first)
    paid = [(w["t0"], w["value"]) for w in wins if w["paid"]]
    end = max(r["resolve"] for r in rows)
    first_q = next((w for w in wins if w["qualified"]), None)
    first_paid = next((w for w in wins if w["paid"]), None)
    # daily tally path (end of each UTC day)
    path = []
    d = (int(first) // DAY_S + 1) * DAY_S
    while d <= end + DAY_S:
        path.append((d, tally_at(paid, d)))
        d += DAY_S
    steady = [v for t, v in path if t >= first + 30 * DAY_S]
    res = {
        "calls": len(rows),
        "span_days": round((end - first) / DAY_S, 1),
        "first_call_utc": _iso(first),
        "warmup_end_utc": _iso(first + IMMUNITY_S),
        "days_to_first_qualified_win": round((first_q["t0"] - first) / DAY_S, 2) if first_q else None,
        "days_to_first_PAID_win": round((first_paid["t0"] - first) / DAY_S, 2) if first_paid else None,
        "wins": len(wins),
        "wins_qualified": sum(1 for w in wins if w["qualified"]),
        "wins_paid": len(paid),
        "mean_paid_value": round(sum(v for _, v in paid) / len(paid), 3) if paid else None,
        "tally_at_end": round(tally_at(paid, end), 3),
        "tally_mean_after_day30": round(sum(steady) / len(steady), 3) if steady else None,
        "tally_max": round(max((v for _, v in path), default=0.0), 3),
        "share_of_days_with_tally": None,
    }
    if path:
        # share of simulated days (end of each UTC day) on which the tally was > 0
        res["share_of_days_with_tally"] = round(
            sum(1 for t, v in path if v > 0) / len(path), 3)
    if field_tally is not None and res["tally_mean_after_day30"] is not None:
        tt = res["tally_mean_after_day30"]
        res["alpha_per_day_steady"] = round(lf_pool * tt / (tt + field_tally), 2) if tt > 0 else 0.0
    return res


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# --- input ---------------------------------------------------------------------------
def parse_t0(s: str) -> float:
    s = str(s).strip()
    try:
        v = float(s)
        return v / 1000.0 if v > 1e11 else v
    except ValueError:
        pass
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s.replace(" ", "T"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_dir(s: str) -> str:
    v = str(s).strip().upper()
    if v in ("LONG", "BUY", "1", "+1", "L", "UP"):
        return "LONG"
    if v in ("SHORT", "SELL", "-1", "S", "DOWN"):
        return "SHORT"
    raise ValueError(f"bad direction {s!r}")


def read_signals(path: str) -> list[dict]:
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        cols = {c.lower().strip(): c for c in (rd.fieldnames or [])}
        tcol = cols.get("t0_unix") or cols.get("t0") or cols.get("t0_ms") or cols.get("time")
        pcol = cols.get("trade_pair") or cols.get("pair")
        dcol = cols.get("direction") or cols.get("dir") or cols.get("side")
        prcol = cols.get("prob") or cols.get("p")
        if not (tcol and pcol and dcol):
            raise SystemExit("CSV needs columns t0_unix, trade_pair, direction[, prob]")
        for i, r in enumerate(rd):
            try:
                out.append({"row": i, "t0": parse_t0(r[tcol]),
                            "pair": r[pcol].strip().upper(), "dir": parse_dir(r[dcol]),
                            "prob": float(r[prcol]) if prcol and r.get(prcol) not in (None, "") else None})
            except (ValueError, KeyError) as e:
                print(f"skip row {i}: {e}", file=sys.stderr)
    out.sort(key=lambda r: (r["t0"], r["row"]))
    return out


# --- quota filter ------------------------------------------------------------------
def apply_quota(sigs: list[dict], bands: dict, cap: int = MAX_PER_DAY,
                gap: int = MIN_GAP_S, slack: int = GAP_SLACK_S,
                enforce: bool = True) -> list[dict]:
    """Annotate each signal with 'accepted' and 'void_reason' (in time order)."""
    times: list[float] = []
    for s in sorted(sigs, key=lambda r: (r["t0"], r["row"])):
        s["accepted"], s["void_reason"] = False, None
        b = bands.get(s["pair"])
        if b is None:
            s["void_reason"] = "not_on_board"
            continue
        if not enforce:
            s["accepted"] = True
            continue
        vr = L.session_void_reason(b.asset_class, s["t0"], b.horizon_h)
        if vr:
            s["void_reason"] = vr
            continue
        if times and s["t0"] - times[-1] < gap - slack:
            s["void_reason"] = "min_spacing"
            continue
        day = int(s["t0"] // DAY_S)
        if sum(1 for t in times if int(t // DAY_S) == day) >= cap:
            s["void_reason"] = "daily_quota"
            continue
        s["accepted"] = True
        times.append(s["t0"])
    return sigs


# --- labelling -------------------------------------------------------------------------
def ensure_candles(sigs: list[dict], bands: dict, cache: str, intervals: list[str]) -> None:
    """Fetch the HL candles needed for these signals (only what HL still retains)."""
    span: dict[str, list[float]] = {}
    for s in sigs:
        b = bands.get(s["pair"])
        if b is None or hl_data.hl_coin(s["pair"]) is None:
            continue
        lo, hi = span.get(s["pair"], [s["t0"], s["t0"]])
        span[s["pair"]] = [min(lo, s["t0"]), max(hi, s["t0"] + b.horizon_h * 3600)]
    for pair, (lo, hi) in sorted(span.items()):
        coin = hl_data.hl_coin(pair)
        for iv in intervals:
            try:
                hl_data.update_cache(cache, coin, iv, int((lo - DAY_S) * 1000),
                                     int((hi + 3600) * 1000))
            except Exception as e:  # noqa: BLE001
                print(f"fetch {pair} {iv}: {e}", file=sys.stderr)


def label_all(sigs: list[dict], bands: dict, cache: str, intervals: list[str],
              cfg: L.LabelConfig, now_ms: int) -> None:
    memo: dict = {}
    for s in sigs:
        b = bands.get(s["pair"])
        s.update({"status": None, "ambiguous": False, "interval": None, "entry_px": None,
                  "exit_ms": None, "resolve": None, "mfe_bps": None, "mae_bps": None,
                  "mark_bps": None})
        if b is None:
            s["status"] = "not_on_board"
            continue
        if hl_data.hl_coin(s["pair"]) is None:
            s["status"] = "no_hl_data"
            continue
        t0_ms = int(s["t0"] * 1000)
        end_ms = t0_ms + b.horizon_h * 3_600_000
        ser = hl_data.finest_series(cache, s["pair"], t0_ms - 3_600_000,
                                    min(end_ms, now_ms - 3_600_000), intervals, _memo=memo)
        if ser is None:
            s["status"] = L.NODATA
            continue
        lab = L.label_series(ser, t0_ms, s["dir"], b.tp_bps, b.sl_bps, b.horizon_h, cfg,
                             now_ms=now_ms)
        s["status"] = lab.status
        s["ambiguous"] = lab.ambiguous
        s["interval"] = next((k for k, v in hl_data.INTERVAL_MS.items()
                              if v == ser.interval_ms), "")
        s["entry_px"], s["exit_ms"] = lab.entry_px, lab.exit_ms
        s["resolve"] = (lab.resolve_ms / 1000.0) if lab.resolve_ms else None
        s["mfe_bps"], s["mae_bps"], s["mark_bps"] = lab.mfe_bps, lab.mae_bps, lab.mark_bps


# --- reporting ---------------------------------------------------------------------------
def summarize(rows: list[dict]) -> dict:
    c = Counter(r["status"] for r in rows)
    w, l_, x = c[L.WON], c[L.LOST], c[L.WASHED]
    n = w + l_
    amb = sum(1 for r in rows if r.get("ambiguous"))
    g = n + x
    lb, ub = wilson(w, n)
    return {"calls": len(rows), "won": w, "lost": l_, "washed": x, "ambiguous": amb,
            "other": len(rows) - g,
            "hit": round(w / n, 4) if n else None,
            "hit_ex_amb": (round(w / (n - amb), 4) if n - amb > 0 else None),
            "wash": round(x / g, 4) if g else None,
            "wilson_lb": round(lb, 4) if n else None,
            "qualifies_now": is_qualified(w, n) if n else False}


def print_table(title: str, groups: dict[str, list[dict]]) -> None:
    print(f"\n{title}")
    print(f"  {'group':18s} {'calls':>5s} {'won':>4s} {'lost':>4s} {'wash':>4s} {'amb':>3s} "
          f"{'hit':>6s} {'wash%':>6s} {'WilsonLB':>8s} qual")
    for k in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        s = summarize(groups[k])
        hit = f"{s['hit']:.3f}" if s["hit"] is not None else "-"
        wsh = f"{s['wash']:.3f}" if s["wash"] is not None else "-"
        lb = f"{s['wilson_lb']:.3f}" if s["wilson_lb"] is not None else "-"
        print(f"  {k:18s} {s['calls']:5d} {s['won']:4d} {s['lost']:4d} {s['washed']:4d} "
              f"{s['ambiguous']:3d} {hit:>6s} {wsh:>6s} {lb:>8s} {'Y' if s['qualifies_now'] else '-'}")


def calibration(rows: list[dict], nbins: int = 10) -> list[dict]:
    dec = [r for r in rows if r["status"] in (L.WON, L.LOST) and r.get("prob") is not None]
    if len(dec) < nbins:
        return []
    dec.sort(key=lambda r: r["prob"])
    out = []
    for b in range(nbins):
        chunk = dec[b * len(dec) // nbins:(b + 1) * len(dec) // nbins]
        if not chunk:
            continue
        w = sum(1 for r in chunk if r["status"] == L.WON)
        out.append({"prob_lo": round(chunk[0]["prob"], 4), "prob_hi": round(chunk[-1]["prob"], 4),
                    "mean_prob": round(sum(r["prob"] for r in chunk) / len(chunk), 4),
                    "n": len(chunk), "hit": round(w / len(chunk), 4),
                    "wilson_lb": round(wilson(w, len(chunk))[0], 4)})
    return out


def run_eval(a) -> int:
    bands = L.load_bands(a.bands)
    sigs = read_signals(a.signals)
    if not sigs:
        print("no signals", file=sys.stderr)
        return 2
    ivs = hl_data.INTERVAL_ORDER if a.interval == "auto" else [a.interval]
    if a.fetch:
        ensure_candles(sigs, bands, a.cache, ivs)
    cfg = L.LabelConfig(pierce_bps=a.pierce_bps, pierce_frac=a.pierce_frac,
                        ambiguous=a.ambiguous)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    label_all(sigs, bands, a.cache, ivs, cfg, now_ms)       # every raw signal, once
    oos = parse_t0(a.oos_from) if a.oos_from else None

    def select(thr: float | None) -> list[dict]:
        pool = [dict(s) for s in sigs
                if thr is None or (s["prob"] is not None and s["prob"] >= thr)]
        apply_quota(pool, bands, enforce=not a.no_quota)
        return pool

    def scored(pool: list[dict]) -> list[dict]:
        return [{"t0": s["t0"], "status": s["status"], "resolve": s["resolve"] or s["t0"]}
                for s in pool if s["accepted"] and s["status"] in (L.WON, L.LOST, L.WASHED)]

    pool = select(a.prob_threshold)
    if not pool:
        print("no signals pass --prob-threshold", file=sys.stderr)
        return 2
    acc = [s for s in pool if s["accepted"]]
    vr = Counter(s["void_reason"] for s in pool if not s["accepted"])
    st = Counter(s["status"] for s in acc)
    days = max(1.0, (max(s["t0"] for s in sigs) - min(s["t0"] for s in sigs)) / DAY_S)
    print(f"signals in: {len(sigs)}   after prob filter: {len(pool)}   accepted: {len(acc)} "
          f"({len(acc) / days:.2f}/day over {days:.1f} d)")
    print(f"dropped/voided: {dict(vr)}")
    print(f"label status (accepted): {dict(st)}   intervals used: "
          f"{dict(Counter(s['interval'] for s in acc if s['interval']))}")
    if a.no_quota:
        print("NOTE: --no-quota: the SN89 3/day + 1 h filter was NOT applied")

    groups_all = {"ALL": acc}
    print_table("== accepted calls, overall ==", groups_all)
    by_pd: dict[str, list[dict]] = defaultdict(list)
    for s in acc:
        by_pd[f"{s['pair']}/{s['dir'][0]}"].append(s)
        by_pd[f"{s['pair']}/*"].append(s)
    print_table("== per pair / direction ==", by_pd)
    if oos is not None:
        print_table(f"== out-of-sample (t0 >= {_iso(oos)}) ==",
                    {"OOS": [s for s in acc if s["t0"] >= oos],
                     "IS": [s for s in acc if s["t0"] < oos]})
    sim = simulate(scored(pool), a.field_tally, a.lf_pool)
    unl = sum(1 for s in acc if s["status"] not in (L.WON, L.LOST, L.WASHED))
    print("\n== causal qualification simulation (first accepted call starts warmup) ==")
    if unl:
        print(f"  NOTE: {unl} accepted calls have no label (no HL data / pending); they used "
              f"quota but are left out of the simulation")
    for k, v in sim.items():
        print(f"  {k:30s} {v}")
    if any(s["prob"] is not None for s in sigs):
        cal = calibration(acc if not a.calib_all else [s for s in sigs if s["status"]])
        if cal:
            print("\n== calibration: model prob vs realized P(TP first | decisive) ==")
            print(f"  {'prob range':>17s} {'mean_p':>7s} {'n':>5s} {'hit':>6s} {'WilsonLB':>8s}")
            for c in cal:
                print(f"  {c['prob_lo']:.3f}-{c['prob_hi']:.3f} {c['mean_prob']:7.3f} {c['n']:5d} "
                      f"{c['hit']:6.3f} {c['wilson_lb']:8.3f}")
    if a.sweep:
        lo, hi, step = (float(x) for x in a.sweep.split(":"))
        print("\n== --prob-threshold sweep (quota applied after the threshold) ==")
        print(f"  {'thr':>5s} {'pass/d':>6s} {'acc/d':>5s} {'dec':>4s} {'hit':>6s} {'LB':>6s} "
              f"{'wash':>5s} {'d->1st paid':>11s} {'paid':>4s} {'tally_ss':>8s}"
              + ("  OOS dec/hit/LB" if oos else ""))
        thr = lo
        while thr <= hi + 1e-9:
            p = select(thr)
            acc_t = [s for s in p if s["accepted"]]
            sm = summarize(acc_t)
            sim_t = simulate(scored(p), None)
            line = (f"  {thr:5.3f} {len(p) / days:6.2f} {len(acc_t) / days:5.2f} "
                    f"{sm['won'] + sm['lost']:4d} "
                    f"{(sm['hit'] if sm['hit'] is not None else float('nan')):6.3f} "
                    f"{(sm['wilson_lb'] if sm['wilson_lb'] is not None else float('nan')):6.3f} "
                    f"{(sm['wash'] if sm['wash'] is not None else float('nan')):5.3f} "
                    f"{str(sim_t.get('days_to_first_PAID_win')):>11s} "
                    f"{sim_t.get('wins_paid', 0):4d} {str(sim_t.get('tally_mean_after_day30')):>8s}")
            if oos is not None:
                so = summarize([s for s in acc_t if s["t0"] >= oos])
                line += (f"  {so['won'] + so['lost']}/"
                         f"{(so['hit'] if so['hit'] is not None else float('nan')):.3f}/"
                         f"{(so['wilson_lb'] if so['wilson_lb'] is not None else float('nan')):.3f}")
            print(line)
            thr += step
    if a.labels_out:
        cols = ["t0", "pair", "dir", "prob", "accepted", "void_reason", "status", "ambiguous",
                "interval", "entry_px", "exit_ms", "resolve", "mfe_bps", "mae_bps", "mark_bps"]
        with open(a.labels_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for s in sorted(pool, key=lambda r: r["t0"]):
                w.writerow([s.get(c) for c in cols])
        print(f"\nlabels written to {a.labels_out}")
    return 0


# --- baseline: random-time calls ---------------------------------------------------------
def realized_sigma_bps_sqrt_h(series, start_ms: int, end_ms: int,
                              session_bound: bool, robust: bool = False) -> float | None:
    """Std of log returns of the series' candles in [start,end), scaled to 1 hour.
    For session-bound classes only candles inside the open FX week are used.
    robust=True returns 1.4826 x median absolute deviation instead (one +9 % hour,
    e.g. TRX 2026-10-29, can double the plain std over 30 days)."""
    rets = []
    iv = series.interval_ms
    for j in range(1, len(series.t)):
        t = series.t[j]
        if t < start_ms or t >= end_ms or series.t[j - 1] != t - iv:
            continue
        if session_bound and (L.fx_market_closed(t / 1000) or L.fx_market_closed((t - iv) / 1000)):
            continue
        a_, b_ = series.c[j - 1], series.c[j]
        if a_ > 0 and b_ > 0:
            rets.append(math.log(b_ / a_))
    if len(rets) < 20:
        return None
    scale = math.sqrt(3_600_000 / iv) * 1e4
    if robust:
        srt = sorted(rets)
        med = srt[len(srt) // 2]
        mad = sorted(abs(x - med) for x in rets)[len(rets) // 2]
        return 1.4826 * mad * scale
    m = sum(rets) / len(rets)
    var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(3_600_000 / iv) * 1e4


def implied_sigma_bps_sqrt_h(band_bps: float, horizon_h: float) -> float:
    """Sigma (bps/sqrt(h)) at which a driftless Brownian motion has a 40 % chance of
    touching neither +band nor -band within the horizon (continuous monitoring)."""
    return band_bps / (BAND_Z_40 * math.sqrt(horizon_h))


def baseline_pair(pair: str, band, cache: str, interval: str, days: float,
                  step_min: float, now_ms: int, cfg: L.LabelConfig,
                  sigma_interval: str = "1h") -> dict | None:
    ser = hl_data.load_series(cache, pair, interval)
    if not len(ser):
        return None
    H_ms = band.horizon_h * 3_600_000
    last_t0 = min(now_ms, ser.last_end_ms) - H_ms - ser.interval_ms
    first_t0 = max(now_ms - int(days * DAY_S * 1000), ser.t[0] + 2 * ser.interval_ms)
    if last_t0 <= first_t0:
        return None
    step = int(step_min * 60_000)
    off = 37_000                                     # avoid sitting on candle boundaries
    sb = band.asset_class in L.SESSION_BOUND_CLASSES
    cnt = {"LONG": Counter(), "SHORT": Counter()}
    n_t0 = 0
    t0 = first_t0 - first_t0 % step + off
    while t0 <= last_t0:
        if t0 >= first_t0 and not (sb and L.session_void_reason(band.asset_class, t0 / 1000,
                                                                  band.horizon_h)):
            n_t0 += 1
            for d in ("LONG", "SHORT"):
                lab = L.label_series(ser, t0, d, band.tp_bps, band.sl_bps, band.horizon_h, cfg)
                cnt[d][lab.status] += 1
                cnt[d]["amb"] += lab.ambiguous
        t0 += step
    if n_t0 == 0:
        return None

    def ptp(c):
        n = c[L.WON] + c[L.LOST]
        return (c[L.WON] / n) if n else None

    tot = cnt["LONG"] + cnt["SHORT"]
    graded = tot[L.WON] + tot[L.LOST] + tot[L.WASHED]
    wash = tot[L.WASHED] / graded if graded else None
    sser = hl_data.load_series(cache, pair, sigma_interval)
    w0 = now_ms - int(days * DAY_S * 1000)
    sig = realized_sigma_bps_sqrt_h(sser, w0, now_ms, sb) if len(sser) else None
    sig_mad = realized_sigma_bps_sqrt_h(sser, w0, now_ms, sb, robust=True) if len(sser) else None
    imp = implied_sigma_bps_sqrt_h(band.tp_bps, band.horizon_h)
    dec_day = MAX_PER_DAY * (1 - wash) if wash is not None else None
    return {"pair": pair, "class": band.asset_class, "band": band.tp_bps, "H": band.horizon_h,
            "interval": interval, "t0s": n_t0, "wash": wash,
            "p_tp_long": ptp(cnt["LONG"]), "p_tp_short": ptp(cnt["SHORT"]),
            "p_tp_both": ptp(tot), "amb": tot["amb"] / max(1, graded),
            "sigma_real": sig, "sigma_mad": sig_mad, "sigma_impl": imp,
            "ratio": (sig / imp) if sig else None,
            "decisive_per_day": dec_day,
            "days_to_8_decisive": (8 / dec_day) if dec_day else None,
            "decisive_n_long": cnt["LONG"][L.WON] + cnt["LONG"][L.LOST]}


def run_baseline(a) -> int:
    bands = L.load_bands(a.bands)
    if a.pairs == "board":
        pairs = sorted(bands)
    else:
        pairs = [p.strip().upper() for p in a.pairs.split(",") if p.strip()]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if a.fetch:
        for p in pairs:
            coin = hl_data.hl_coin(p)
            if coin is None:
                continue
            for iv in {a.interval, a.sigma_interval}:
                try:
                    hl_data.update_cache(a.cache, coin, iv,
                                         now_ms - int((a.days + 2) * DAY_S * 1000), now_ms)
                except Exception as e:  # noqa: BLE001
                    print(f"fetch {p} {iv}: {e}", file=sys.stderr)
    cfg = L.LabelConfig(pierce_bps=a.pierce_bps, pierce_frac=a.pierce_frac)
    res, skipped = [], []
    for p in pairs:
        b = bands.get(p)
        if b is None:
            skipped.append((p, "not on board"))
            continue
        if hl_data.hl_coin(p) is None:
            skipped.append((p, hl_data.UNSUPPORTED.get(p, "no HL mapping")))
            continue
        r = baseline_pair(p, b, a.cache, a.interval, a.days, a.step_min, now_ms, cfg,
                          a.sigma_interval)
        if r is None:
            skipped.append((p, f"no {a.interval} data for the window"))
            continue
        r["fidelity"] = hl_data.PAIR_FIDELITY.get(p, "")
        res.append(r)
    res.sort(key=lambda r: (r["wash"] if r["wash"] is not None else 9))
    f = lambda x, d=3: "-" if x is None else f"{x:.{d}f}"  # noqa: E731
    print(f"random-time calls every {a.step_min:g} min over the last {a.days:g} d, "
          f"{a.interval} candles, board bands; session-bound pairs only when SN89 would accept")
    print(f"{'pair':10s} {'class':8s} {'band':>5s} {'H':>2s} {'t0s':>5s} {'wash':>6s} {'P(TP)L':>6s} "
          f"{'P(TP)S':>6s} {'amb':>5s} {'sig_re':>6s} {'sigMAD':>6s} {'sig_im':>6s} {'ratio':>5s} {'dec/d':>5s} "
          f"{'d->8dec':>7s} fid")
    for r in res:
        print(f"{r['pair']:10s} {r['class'][:8]:8s} {r['band']:5.0f} {r['H']:2d} {r['t0s']:5d} "
              f"{f(r['wash'])} {f(r['p_tp_long'])} {f(r['p_tp_short'])} {f(r['amb'], 3):>5s} "
              f"{f(r['sigma_real'], 1):>6s} {f(r['sigma_mad'], 1):>6s} {f(r['sigma_impl'], 1):>6s} {f(r['ratio'], 2):>5s} "
              f"{f(r['decisive_per_day'], 2):>5s} {f(r['days_to_8_decisive'], 1):>7s} {r['fidelity']}")
    for p, why in skipped:
        print(f"skipped {p}: {why}")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump({"args": vars(a) | {"fn": None}, "now_ms": now_ms, "rows": res,
                       "skipped": skipped}, fh, indent=1, default=str)
    return 0


# --- validate: re-grade the owner's public checkpoint ------------------------------------
def run_validate(a) -> int:
    """Re-grade resolved signals from the public SN89 checkpoint JSON with HL candles
    and compare with the official status (confusion matrix overall / per interval /
    per pair). Uses each signal's OWN committed tp/sl (not today's board)."""
    with open(a.checkpoint, encoding="utf-8") as fh:
        ck = json.load(fh)
    since = parse_t0(a.since)
    sigs = []
    for s in ck.get("signals", []):
        if s.get("status") not in (L.WON, L.LOST, L.WASHED) or not s.get("plaintext"):
            continue
        try:
            p = json.loads(s["plaintext"])
        except ValueError:
            continue
        pair = str(p.get("trade_pair", "")).upper()
        t0 = float(s["t0_unix"])
        if t0 < since or hl_data.hl_coin(pair) is None or p.get("tp_bps") is None:
            continue
        if a.pairs and pair not in a.pairs.upper().split(","):
            continue
        cls = p.get("asset_class") or "crypto"
        sigs.append({"t0": t0, "pair": pair, "dir": str(p.get("direction", "")).upper(),
                     "tp": float(p["tp_bps"]), "sl": float(p["sl_bps"]),
                     "hz": L.class_horizon_h(cls), "off": s["status"]})
    print(f"checkpoint signals selected: {len(sigs)} (t0 >= {_iso(since)}, resolved, HL-mapped)")
    ivs = hl_data.INTERVAL_ORDER if a.interval == "auto" else [a.interval]
    if a.fetch:
        fake = [{"t0": x["t0"], "pair": x["pair"], "row": 0} for x in sigs]
        bands = {x["pair"]: L.Band(x["pair"], "crypto", x["tp"], x["sl"], x["hz"]) for x in sigs}
        ensure_candles(fake, bands, a.cache, ivs)
    cfg = L.LabelConfig(pierce_bps=a.pierce_bps, pierce_frac=a.pierce_frac)
    S = (L.WON, L.LOST, L.WASHED)
    memo: dict = {}
    allm: Counter = Counter()
    by_iv: dict[str, Counter] = defaultdict(Counter)
    by_pair: dict[str, Counter] = defaultdict(Counter)
    amb = 0
    for x in sigs:
        t0_ms = int(x["t0"] * 1000)
        ser = hl_data.finest_series(a.cache, x["pair"], t0_ms - 3_600_000,
                                    t0_ms + x["hz"] * 3_600_000, ivs, _memo=memo)
        if ser is None or x["dir"] not in ("LONG", "SHORT"):
            continue
        lab = L.label_series(ser, t0_ms, x["dir"], x["tp"], x["sl"], x["hz"], cfg)
        if lab.status not in S:
            continue
        iv = next(k for k, v in hl_data.INTERVAL_MS.items() if v == ser.interval_ms)
        key = (x["off"], lab.status)
        allm[key] += 1
        by_iv[iv][key] += 1
        by_pair[x["pair"]][key] += 1
        amb += lab.ambiguous

    def line(m: Counter) -> str:
        n = sum(m.values())
        if not n:
            return "n=0"
        agree = sum(m[(k, k)] for k in S)
        nd = sum(v for (o, u), v in m.items() if o != L.WASHED and u != L.WASHED)
        da = m[(L.WON, L.WON)] + m[(L.LOST, L.LOST)]
        ow = sum(v for (o, _), v in m.items() if o == L.WASHED)
        uw = sum(v for (_, u), v in m.items() if u == L.WASHED)
        return (f"n={n:6d} 3-way agree={agree / n:.3f}  W/L agree (both decisive, n={nd})="
                f"{(da / nd if nd else float('nan')):.3f}  wash official={ow / n:.3f} ours={uw / n:.3f}")

    print("\nOVERALL ", line(allm), f" ambiguous={amb}")
    print("            ours:won  ours:lost ours:washed")
    for o in S:
        print(f"  off:{o:7s} " + " ".join(f"{allm[(o, u)]:9d}" for u in S))
    print("\nby finest interval used:")
    for iv in hl_data.INTERVAL_ORDER:
        if iv in by_iv:
            print(f"  {iv:4s}", line(by_iv[iv]))
    print("\nby pair:")
    for p in sorted(by_pair, key=lambda k: -sum(by_pair[k].values())):
        print(f"  {p:10s}", line(by_pair[p]))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SN89 LF backtest harness (stdlib).")
    ap.add_argument("--cache", default=os.path.join(HERE, "hl_cache"))
    ap.add_argument("--bands", default=os.path.join(HERE, "sn89_bands.json"))
    ap.add_argument("--pierce-bps", type=float, default=0.0)
    ap.add_argument("--pierce-frac", type=float, default=0.02)
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("eval", help="evaluate a signals CSV")
    e.add_argument("--signals", required=True)
    e.add_argument("--interval", default="auto", help="auto = finest cached covering each call")
    e.add_argument("--fetch", action="store_true", help="fetch missing HL candles first")
    e.add_argument("--prob-threshold", type=float, default=None)
    e.add_argument("--sweep", help="lo:hi:step prob-threshold sweep, e.g. 0.5:0.8:0.02")
    e.add_argument("--oos-from", help="ISO date/epoch: report t0 >= this separately")
    e.add_argument("--no-quota", action="store_true", help="skip the 3/day + 1h filter")
    e.add_argument("--ambiguous", default="lost", choices=["lost", "won"])
    e.add_argument("--field-tally", type=float, default=None,
                   help="sum of all OTHER hotkeys' LF tallies, to estimate alpha/day")
    e.add_argument("--lf-pool", type=float, default=LF_POOL_ALPHA_PER_DAY)
    e.add_argument("--calib-all", action="store_true",
                   help="calibration on all labelled signals, not just accepted ones")
    e.add_argument("--labels-out", help="write per-signal labels CSV")
    e.set_defaults(fn=run_eval)

    b = sub.add_parser("baseline", help="random-time wash / P(TP) per pair")
    b.add_argument("--pairs", default="board")
    b.add_argument("--days", type=float, default=7.0)
    b.add_argument("--interval", default="3m")
    b.add_argument("--sigma-interval", default="1h")
    b.add_argument("--step-min", type=float, default=10.0)
    b.add_argument("--fetch", action="store_true")
    b.add_argument("--json-out")
    b.set_defaults(fn=run_baseline)

    v = sub.add_parser("validate", help="re-grade the public checkpoint vs official status")
    v.add_argument("--checkpoint", required=True, help="sn89_checkpoint.json")
    v.add_argument("--since", default="2026-08-21",
                   help="only t0 >= this (default: start of the HL-graded crypto era)")
    v.add_argument("--pairs", default="", help="comma list filter")
    v.add_argument("--interval", default="auto")
    v.add_argument("--fetch", action="store_true")
    v.set_defaults(fn=run_validate)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
