#!/usr/bin/env python3
"""sn89_status.py — your SN89 LF standing, read from the subnet's PUBLIC checkpoint.

Shows, per hotkey: UID, warmup clock, record (won/lost/washed/void/pending),
trailing-60-day decisive count and Wilson lower bound, whether you are
QUALIFIED now, your banked (paid) qualified wins and an estimate of your LF
tally, your current on-chain mechanism-0 weight, and strikes.

Also checks the one irreversible step of onboarding: that a referral claim
(recruiter A -> recruit B) is journaled BEFORE B registers.

    python sn89_status.py --hotkey <B_ss58>
    python sn89_status.py --hotkey <A_ss58> --hotkey <B_ss58> --json
    python sn89_status.py --check-referral <A_ss58> <B_ss58>
    python sn89_status.py --file checkpoint.json --hotkey <ss58>   # offline

Scoring replicates sn89_signals/scoring.py at commit 2e48ae8 (current-era
rules only; every cutover predates any newcomer): causal qualification window,
Wilson z=1.2816 >= 0.50 with >= 8 decisive, 60 d / 200-trade reputation window,
8 d warmup from first_seen, tier ramp on the shrunk rate, wash efficiency,
30 d linear decay, 20-win cap. Not modelled: the habitual-copier exclusion
(copy flags are reported instead), the LF bond gate (check it with
`btcli stake list`), the +10% recruit bonus. The checkpoint is what the
owner's validator publishes; the live production config may differ.

Stdlib only; Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request

CKPT_URL = "https://partner.infinitequant.app/sn89/checkpoint.json"
DAY = 86400.0
Z = 1.2816
LB_FLOOR = 0.50
MIN_DECISIVE = 8
WINDOW_S = 60 * DAY
WINDOW_TRADES = 200
WARMUP_S = 8 * DAY
DECAY_S = 30 * DAY
WIN_CAP = 20
TIER_PRIOR_K = 12
TIERS = ((0.55, 1.0), (0.60, 1.2), (0.70, 2.0))  # ascending (threshold, multiplier)
EFF_SLOPE, EFF_MIN, EFF_PRIOR_WASH, EFF_PRIOR_K = 0.6, 0.55, 0.40, 40
REFERRAL_MIN_LEAD_BLOCKS = 10
LF_SHARE_OF_MECH0 = 0.375
HORIZON_H = {"forex": 12, "metals": 12}


def wilson(w: int, n: int, z: float = Z) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = w / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return (centre - margin) / denom, (centre + margin) / denom


def qualified(w: int, n: int) -> bool:
    return n >= MIN_DECISIVE and wilson(w, n)[0] >= LB_FLOOR


def tier(w: int, n: int) -> float:
    r = (w + TIER_PRIOR_K / 2.0) / (n + TIER_PRIOR_K)
    if r <= TIERS[0][0]:
        return TIERS[0][1]
    if r >= TIERS[-1][0]:
        return TIERS[-1][1]
    for (a, am), (b, bm) in zip(TIERS, TIERS[1:]):
        if a <= r < b:
            return am + (r - a) / (b - a) * (bm - am)
    return TIERS[-1][1]


def efficiency(graded: list[tuple[float, bool]], t0: float) -> float:
    window = [is_wash for t, is_wash in graded if t0 - WINDOW_S <= t <= t0]
    shrunk = (sum(window) + EFF_PRIOR_K * EFF_PRIOR_WASH) / (len(window) + EFF_PRIOR_K)
    return max(EFF_MIN, min(1.0, 1.0 - EFF_SLOPE * shrunk))


def load_checkpoint(path: str | None, url: str) -> dict:
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    req = urllib.request.Request(url, headers={"User-Agent": "sn89-status/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def rows_for(ckpt: dict, hotkey: str) -> list[dict]:
    out = []
    for s in ckpt.get("signals", []):
        if s.get("hotkey") != hotkey:
            continue
        pt = {}
        if s.get("plaintext"):
            try:
                pt = json.loads(s["plaintext"])
            except json.JSONDecodeError:
                pt = {}
        hz = pt.get("horizon_h") or HORIZON_H.get(pt.get("asset_class", ""), 8)
        exit_ms = s.get("exit_at_ms")
        out.append({
            "t0": float(s["t0_unix"]),
            "status": s.get("status"),
            "is_copy": bool(s.get("is_copy")),
            "pair": pt.get("trade_pair"),
            "direction": pt.get("direction"),
            "band_bps": pt.get("tp_bps"),
            "resolved": (exit_ms / 1000.0) if exit_ms else float(s["t0_unix"]) + hz * 3600,
            "void_reason": s.get("void_reason"),
        })
    out.sort(key=lambda r: r["t0"])
    return out


def paid_wins(rows: list[dict], first_seen: float) -> list[tuple[float, float]]:
    warmup_end = first_seen + WARMUP_S
    dec = [r for r in rows if r["status"] in ("won", "lost")]
    graded = [(r["t0"], r["status"] == "washed") for r in rows
              if r["status"] in ("won", "lost", "washed")]
    out = []
    for i, r in enumerate(dec):
        if r["status"] != "won" or r["t0"] < warmup_end:
            continue
        window = [d for d in dec[:i] if d["t0"] >= r["t0"] - WINDOW_S and d["resolved"] <= r["t0"]]
        window.append(r)
        window = window[-WINDOW_TRADES:]
        w = sum(1 for d in window if d["status"] == "won")
        n = len(window)
        if qualified(w, n):
            out.append((r["t0"], max(1.0, tier(w, n)) * efficiency(graded, r["t0"])))
    return out


def tally(qwins: list[tuple[float, float]], now: float) -> float:
    live = sorted(((t, v) for t, v in qwins if 0.0 <= now - t < DECAY_S), reverse=True)
    return sum(v * (1.0 - (now - t) / DECAY_S) for t, v in live[:WIN_CAP])


def hotkey_report(ckpt: dict, hk: str) -> dict:
    now = float(ckpt.get("now_unix") or time.time())
    meta = ckpt.get("meta", {}).get(hk)
    uid = ckpt.get("uid_by_hotkey", {}).get(hk)
    rows = rows_for(ckpt, hk)
    rep = {"hotkey": hk, "uid": uid, "registered": uid is not None,
           "checkpoint_age_min": round((time.time() - now) / 60, 1)}
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    rep["counts"] = counts
    if not meta:
        rep["note"] = "no SIGNAL journaled yet: the 8-day warmup has not started"
        return rep
    first_seen = float(meta["first_seen_unix"])
    warmup_end = first_seen + WARMUP_S
    rep["first_seen_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(first_seen))
    rep["warmup_ends_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(warmup_end))
    rep["in_warmup"] = now < warmup_end
    rep["strikes"] = meta.get("strikes", 0)
    dec = [r for r in rows if r["status"] in ("won", "lost")
           and r["t0"] >= now - WINDOW_S and r["resolved"] <= now][-WINDOW_TRADES:]
    w, n = sum(1 for r in dec if r["status"] == "won"), len(dec)
    lb, ub = wilson(w, n)
    graded = sum(counts.get(k, 0) for k in ("won", "lost", "washed"))
    rep["window"] = {"decisive": n, "won": w, "hit_rate": round(w / n, 4) if n else None,
                     "wilson_lb": round(lb, 4), "qualified_now": qualified(w, n),
                     "wash_rate": round(counts.get("washed", 0) / graded, 4) if graded else None}
    need = None
    for extra in range(0, 400):
        if qualified(w + extra, n + extra):
            need = extra
            break
    rep["consecutive_wins_to_qualify"] = need
    qw = paid_wins(rows, first_seen)
    rep["paid_wins_total"] = len(qw)
    rep["paid_wins_live_30d"] = sum(1 for t, _ in qw if now - t < DECAY_S)
    rep["est_tally"] = round(tally(qw, now), 3)
    rep["copy_flags"] = sum(1 for r in rows if r["is_copy"])
    w_on = ckpt.get("weights_onchain", {}).get(str(uid)) if uid is not None else None
    rep["mech0_weight_onchain"] = w_on
    if w_on is not None and str(uid) != "39":
        rep["est_lf_share"] = round(w_on / LF_SHARE_OF_MECH0, 5)
    rep["last_calls"] = [{"utc": time.strftime("%m-%d %H:%M", time.gmtime(r["t0"])),
                          "pair": r["pair"], "dir": r["direction"], "band": r["band_bps"],
                          "status": r["status"], "void": r["void_reason"]} for r in rows[-8:]]
    return rep


def referral_report(ckpt: dict, recruiter: str, recruit: str) -> dict:
    hits = [r for r in ckpt.get("referrals", [])
            if r.get("recruiter_hk") == recruiter and r.get("recruit_hk") == recruit]
    other = [r for r in ckpt.get("referrals", [])
             if r.get("recruit_hk") == recruit and r.get("recruiter_hk") != recruiter]
    registered = recruit in ckpt.get("uid_by_hotkey", {})
    out = {"recruiter": recruiter, "recruit": recruit, "journaled": bool(hits),
           "recruit_registered": registered, "claimed_by_someone_else": bool(other)}
    if hits:
        r = hits[0]
        out["commit_block"] = r.get("commit_block")
        out["register_recruit_at_or_after_block"] = (r.get("commit_block") or 0) + REFERRAL_MIN_LEAD_BLOCKS
        if r.get("recruit_reg_block"):
            out["recruit_reg_block"] = r["recruit_reg_block"]
            out["valid"] = r["recruit_reg_block"] >= out["register_recruit_at_or_after_block"]
    if other:
        out["WARNING"] = "another recruiter claimed this hotkey first: the earliest claim wins"
    elif registered and not hits:
        out["WARNING"] = "recruit is already registered without this claim: it can never be claimed now"
    elif hits and not registered:
        out["next"] = ("OK to register the recruit now (after the block above). "
                       "Do it promptly: the claim is public until you do.")
    elif not hits:
        out["next"] = "claim not in the checkpoint yet: wait (~5 min republish) and re-run. Do NOT register the recruit."
    return out


def print_human(rep: dict) -> None:
    for k, v in rep.items():
        if k == "last_calls":
            print("  last_calls:")
            for c in v:
                print(f"    {c['utc']}  {c['pair']} {c['dir']} ±{c['band']}  {c['status']}"
                      + (f" ({c['void']})" if c['void'] else ""))
        else:
            print(f"  {k}: {v}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--hotkey", action="append", default=[])
    p.add_argument("--check-referral", nargs=2, metavar=("RECRUITER", "RECRUIT"))
    p.add_argument("--file", help="read a saved checkpoint instead of fetching")
    p.add_argument("--url", default=CKPT_URL)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if not a.hotkey and not a.check_referral:
        p.error("give --hotkey and/or --check-referral")
    ckpt = load_checkpoint(a.file, a.url)
    out = []
    if a.check_referral:
        out.append(("referral", referral_report(ckpt, *a.check_referral)))
    for hk in a.hotkey:
        out.append((hk, hotkey_report(ckpt, hk)))
    if a.json:
        print(json.dumps({k: v for k, v in out}, indent=2))
    else:
        for k, v in out:
            print(f"[{k}]")
            print_human(v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
