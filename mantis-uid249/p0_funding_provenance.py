"""P0: establish MANTIS FUNDINGXSEC feed provenance per asset. No labels, no F2 outputs.

Read-only. It uses only public no-key endpoints and saves every raw response with its sha256.
It never computes forward changes, AUC or any score; it compares the archived feed VALUES
with each venue's SETTLED funding history.

Prior evidence (Barbariandev/MANTIS README, 2026-04-15 to 2026-08-17, commits d595b18 to d453933):
"price_service.py | Fetches spot prices (Polygon) + funding rates (OKX/HL/CoinGlass)". Binance is
included below only as a control. CoinGlass needs an API key, so it is not queried; if no free venue
matches an asset, the result is BLOCKED (possibly an aggregate) rather than assumed.

Pre-registered verification rule, per asset and venue (fixed before running):
  n_matched >= 30 settlements, Pearson r >= 0.99, |median(archive/venue) - 1| <= 0.02,
  and >= 90% of settlements within 1% relative error (or 1e-7 absolute).
  Semantics: 'estimated' if the last archive value BEFORE settlement matches; 'settled' if the first
  value AFTER settlement (+ publication delay <= 30 min) matches. Both or neither -> unresolved.
  Exactly one passing venue -> VERIFIED. Two -> AMBIGUOUS. None -> BLOCKED.

Usage (Paris):
  python -I p0_funding_provenance.py --npz '/path/to/archive/*.npz' \
     --funding-key funding --time-key capture_ts --sidx-key sidx --out p0_out/
  # --phase A runs the offline fingerprint only (no network).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import numpy as np

FUNDING_ASSETS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "SUI",
                  "NEAR", "AAVE", "UNI", "LTC", "HBAR", "PEPE", "TRX", "SHIB", "TAO", "ONDO"]
HL_COIN = {a: a for a in FUNDING_ASSETS} | {"PEPE": "kPEPE", "SHIB": "kSHIB"}
RULE = dict(min_n=30, min_r=0.99, max_ratio_dev=0.02, min_close_frac=0.90, rel_tol=0.01, abs_tol=1e-7,
            pre_window_s=300, max_pub_delay_s=1800)


# ---------------------------------------------------------------- archive

def _to_epoch_s(x) -> np.ndarray:
    a = np.asarray(x)
    if a.dtype.kind in "iuf":
        a = a.astype(np.float64)
        return a / 1000.0 if np.nanmedian(a) > 1e11 else a
    out = []
    for v in a.astype(str):
        out.append(datetime.fromisoformat(v.replace("Z", "+00:00")).replace(
            tzinfo=datetime.fromisoformat(v.replace("Z", "+00:00")).tzinfo or timezone.utc).timestamp())
    return np.asarray(out, dtype=np.float64)


def load_archive(pattern: str, fkey: str, tkey: str, skey: str):
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit(f"no files match {pattern}")
    F, T, S = [], [], []
    for f in files:
        with np.load(f, allow_pickle=False) as z:
            fr = np.asarray(z[fkey], dtype=np.float64)
            fr = fr.reshape(1, -1) if fr.ndim == 1 else fr
            ts = _to_epoch_s(z[tkey]).reshape(-1)
            sx = np.asarray(z[skey]).reshape(-1)
            if fr.shape != (len(ts), len(FUNDING_ASSETS)):
                raise SystemExit(f"{f}: funding shape {fr.shape} != ({len(ts)}, 20)")
            F.append(fr); T.append(ts); S.append(sx)
    F, T, S = np.vstack(F), np.concatenate(T), np.concatenate(S)
    order = np.argsort(T, kind="stable")
    F, T, S = F[order], T[order], S[order]
    keep = np.r_[True, np.diff(T) > 0]
    return F[keep], T[keep], S[keep], files


def fingerprint(F: np.ndarray, T: np.ndarray) -> dict:
    """Value and clock signatures of the feed itself. No forward pairing."""
    out = {}
    dt = np.diff(T)
    for j, a in enumerate(FUNDING_ASSETS):
        x = F[:, j]
        fin = np.isfinite(x)
        xv = x[fin]
        vals, cnt = np.unique(np.round(xv, 12), return_counts=True)
        top = np.argsort(-cnt)[:3]
        ch = np.flatnonzero(np.r_[False, np.diff(x) != 0] & fin)
        hours = np.bincount(((T[ch] // 3600) % 24).astype(int), minlength=24) if ch.size else np.zeros(24, int)
        mins = np.bincount(((T[ch] // 60) % 60).astype(int), minlength=60) if ch.size else np.zeros(60, int)
        out[a] = {
            "n": int(x.size), "finite_frac": round(float(fin.mean()), 4),
            "median_abs": float(np.median(np.abs(xv))) if xv.size else None,
            "modal_values": [[float(vals[i]), int(cnt[i])] for i in top],
            "change_frac_per_row": round(float(ch.size / max(1, x.size - 1)), 4),
            "median_row_gap_s": float(np.median(dt)) if dt.size else None,
            "changes_by_utc_hour": hours.tolist(),
            "changes_by_minute_of_hour_top5": [[int(m), int(mins[m])] for m in np.argsort(-mins)[:5]],
        }
    return out


# ---------------------------------------------------------------- venues (public, no key)

class Fetcher:
    def __init__(self, outdir: str):
        self.dir = os.path.join(outdir, "raw")
        os.makedirs(self.dir, exist_ok=True)
        self.log = []

    def get(self, url: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                              "User-Agent": "provenance-check/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        h = hashlib.sha256(raw).hexdigest()
        with open(os.path.join(self.dir, h + ".json"), "wb") as f:
            f.write(raw)
        self.log.append({"url": url, "body": body, "sha256": h, "bytes": len(raw),
                         "captured_utc": datetime.now(timezone.utc).isoformat()})
        time.sleep(0.25)
        return json.loads(raw)


def okx_settled(fx: Fetcher, asset: str, t0: float, t1: float):
    inst = f"{asset}-USDT-SWAP"
    rows, after = [], None
    for _ in range(100):
        q = {"instId": inst, "limit": "100"} | ({"after": str(after)} if after else {})
        d = fx.get("https://www.okx.com/api/v5/public/funding-rate-history?" + urllib.parse.urlencode(q))
        page = d.get("data") or []
        if not page:
            break
        rows += [(int(r["fundingTime"]) / 1000.0, float(r["realizedRate"] or r["fundingRate"])) for r in page]
        after = page[-1]["fundingTime"]
        if int(after) / 1000.0 < t0:
            break
    meta = fx.get("https://www.okx.com/api/v5/public/funding-rate?" + urllib.parse.urlencode({"instId": inst}))
    return inst, sorted(r for r in rows if t0 <= r[0] <= t1), (meta.get("data") or [None])[0]


def hl_settled(fx: Fetcher, asset: str, t0: float, t1: float):
    coin = HL_COIN[asset]
    rows, start = [], int(t0 * 1000)
    for _ in range(200):
        d = fx.get("https://api.hyperliquid.xyz/info",
                   {"type": "fundingHistory", "coin": coin, "startTime": start, "endTime": int(t1 * 1000)})
        if not d:
            break
        rows += [(int(r["time"]) / 1000.0, float(r["fundingRate"])) for r in d]
        nxt = int(d[-1]["time"]) + 1
        if nxt <= start or len(d) < 500:
            break
        start = nxt
    return coin, sorted(rows), None


def binance_settled(fx: Fetcher, asset: str, t0: float, t1: float):
    sym = ("1000" + asset if asset in ("PEPE", "SHIB") else asset) + "USDT"
    rows, start = [], int(t0 * 1000)
    for _ in range(50):
        d = fx.get("https://fapi.binance.com/fapi/v1/fundingRate?" + urllib.parse.urlencode(
            {"symbol": sym, "startTime": start, "endTime": int(t1 * 1000), "limit": 1000}))
        if not d:
            break
        rows += [(int(r["fundingTime"]) / 1000.0, float(r["fundingRate"])) for r in d]
        if len(d) < 1000:
            break
        start = int(d[-1]["fundingTime"]) + 1
    return sym, sorted(rows), None


VENUES = {"okx": okx_settled, "hyperliquid": hl_settled, "binance_control": binance_settled}


# ---------------------------------------------------------------- matching

def match(x: np.ndarray, T: np.ndarray, settled: list[tuple[float, float]]) -> dict:
    """Compare the archive with settled rates. 'pre' = last value <= tau (within 5 min);
    'post' = first value at tau+d, choosing the smallest d (0..30 min) where the median rel error is minimal."""
    res = {}
    taus = np.array([s[0] for s in settled]); vr = np.array([s[1] for s in settled])
    if taus.size == 0:
        return {"n_settlements": 0}

    def stats(a, b):
        ok = np.isfinite(a) & np.isfinite(b)
        a, b = a[ok], b[ok]
        if a.size < 3:
            return {"n": int(a.size)}
        r = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else float("nan")
        nz = np.abs(b) > 1e-12
        ratio = float(np.median(a[nz] / b[nz])) if nz.any() else float("nan")
        close = np.abs(a - b) <= np.maximum(RULE["rel_tol"] * np.abs(b), RULE["abs_tol"])
        return {"n": int(a.size), "r": r, "median_ratio": ratio, "close_frac": float(close.mean())}

    i = np.searchsorted(T, taus, side="right") - 1
    pre = np.where((i >= 0) & (taus - T[np.clip(i, 0, None)] <= RULE["pre_window_s"]), x[np.clip(i, 0, None)], np.nan)
    res["pre_settlement"] = stats(pre, vr)
    best = None
    for d in range(0, RULE["max_pub_delay_s"] + 1, 60):
        k = np.searchsorted(T, taus + d, side="left")
        post = np.where(k < T.size, x[np.clip(k, 0, T.size - 1)], np.nan)
        st = stats(post, vr) | {"delay_s": d}
        if st.get("n", 0) >= 3 and (best is None or st["close_frac"] > best["close_frac"]):
            best = st
    res["post_settlement"] = best or {"n": 0}
    res["n_settlements"] = int(taus.size)
    return res


def passes(st: dict) -> bool:
    return (st.get("n", 0) >= RULE["min_n"] and st.get("r", 0) >= RULE["min_r"]
            and abs(st.get("median_ratio", 0) - 1) <= RULE["max_ratio_dev"]
            and st.get("close_frac", 0) >= RULE["min_close_frac"])


def decide(per_venue: dict) -> dict:
    ok = []
    for v, m in per_venue.items():
        if "error" in m:
            continue
        sem = [s for s, k in (("estimated", "pre_settlement"), ("settled", "post_settlement")) if passes(m.get(k, {}))]
        if len(sem) == 1:
            ok.append((v, sem[0]))
    if len(ok) == 1:
        return {"status": "VERIFIED", "venue": ok[0][0], "semantics": ok[0][1]}
    if len(ok) > 1:
        return {"status": "AMBIGUOUS", "candidates": ok}
    return {"status": "BLOCKED", "reason": "no free public venue matches under the pre-registered rule "
                                           "(possible aggregate e.g. CoinGlass, scaling, or other venue)"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--funding-key", default="funding")
    ap.add_argument("--time-key", default="capture_ts")
    ap.add_argument("--sidx-key", default="sidx")
    ap.add_argument("--phase", choices=["A", "AB"], default="AB")
    ap.add_argument("--venues", default="okx,hyperliquid,binance_control")
    ap.add_argument("--out", default="p0_out")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)

    F, T, S, files = load_archive(a.npz, a.funding_key, a.time_key, a.sidx_key)
    t0, t1 = float(T[0]), float(T[-1])
    report = {"rule": RULE, "archive_files": len(files), "rows": int(T.size),
              "window_utc": [datetime.fromtimestamp(t0, timezone.utc).isoformat(),
                             datetime.fromtimestamp(t1, timezone.utc).isoformat()],
              "archive_funding_sha256": hashlib.sha256(np.ascontiguousarray(F).tobytes()).hexdigest(),
              "fingerprint": fingerprint(F, T)}
    if a.phase == "AB":
        fx = Fetcher(a.out)
        report["assets"] = {}
        for j, asset in enumerate(FUNDING_ASSETS):
            pv = {}
            for v in a.venues.split(","):
                try:
                    sym, rows, meta = VENUES[v](fx, asset, t0 - 86400, t1 + 86400)
                    pv[v] = {"symbol": sym, "venue_meta": meta} | match(F[:, j], T, rows)
                except Exception as e:  # record, never substitute
                    pv[v] = {"error": f"{type(e).__name__}: {e}"}
            report["assets"][asset] = {"per_venue": pv, "decision": decide(pv)}
        report["fetch_log"] = fx.log
    path = os.path.join(a.out, "P0_PROVENANCE_RESULT.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=1, default=str)
    if "assets" in report:
        for asset, r in report["assets"].items():
            print(asset, r["decision"])
    print("wrote", path, hashlib.sha256(open(path, "rb").read()).hexdigest())


if __name__ == "__main__":
    main()
