"""Diagnostic on the Paris P0 evidence. NOT a verification; the frozen P0 thresholds are unchanged.

Rules declared before the first run:
  * Events: OKX settlements (fundingTime) inside the archive window, from the saved raw responses.
  * Archive "pre" value: the last archive row with capture_ts <= tau, accepted only if tau - ts <= 300 s.
  * Field test: compare against OKX fundingRate AND realizedRate separately.
  * Staleness (archive-only, never uses venue data): a row is STALE if the full 20-asset vector
    equals the vector at every row back to >= 30 min earlier (capture time).
  * Closeness: |a-b| <= max(0.01*|b|, 1e-7) (the frozen P0 tolerance). Looser tolerances are reported
    as a description only and do not change any verdict.
Usage: python -I p0_okx_stratified_diagnostic.py <evidence_dir> <out.json>
"""
import glob
import hashlib
import json
import os
import sys

import numpy as np

ASSETS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "SUI",
          "NEAR", "AAVE", "UNI", "LTC", "HBAR", "PEPE", "TRX", "SHIB", "TAO", "ONDO"]
STALE_S = 1800
PRE_S = 300


def main(ev, out):
    z = np.load(os.path.join(ev, "archive/funding_20assets_capture_times.npz"), allow_pickle=False)
    F, T = z["funding"], z["capture_ts"]
    assert F.shape == (T.size, 20) and np.all(np.diff(T) > 0)
    res = json.load(open(os.path.join(ev, "p0/P0_PROVENANCE_RESULT.json")))
    log = res["fetch_log"]

    # Archive-only staleness: time since the 20-vector last changed.
    changed = np.r_[True, np.any(F[1:] != F[:-1], axis=1)]
    last_change_t = np.maximum.accumulate(np.where(changed, T, -np.inf))
    stale = (T - last_change_t) >= STALE_S

    okx = {a: {} for a in ASSETS}
    for l in log:
        if "funding-rate-history" not in l["url"]:
            continue
        inst = l["url"].split("instId=")[1].split("&")[0]
        a = inst.split("-")[0]
        raw = open(os.path.join(ev, "p0/raw", l["sha256"] + ".json"), "rb").read()
        assert hashlib.sha256(raw).hexdigest() == l["sha256"]
        for r in json.loads(raw)["data"]:
            okx[a][int(r["fundingTime"]) / 1000.0] = (float(r["fundingRate"]), float(r["realizedRate"]))

    def close(a, b, rel=0.01, ab=1e-7):
        return np.abs(a - b) <= np.maximum(rel * np.abs(b), ab)

    per_asset, all_rows = {}, []
    for j, a in enumerate(ASSETS):
        taus = np.array(sorted(t for t in okx[a] if T[0] <= t <= T[-1]))
        fr = np.array([okx[a][t][0] for t in taus]); rr = np.array([okx[a][t][1] for t in taus])
        i = np.searchsorted(T, taus, side="right") - 1
        ok = (i >= 0) & (taus - T[np.clip(i, 0, None)] <= PRE_S)
        i = i[ok]; taus, fr, rr = taus[ok], fr[ok], rr[ok]
        x = F[i, j]; st = stale[i]
        d = {}
        for name, ref in (("fundingRate", fr), ("realizedRate", rr)):
            c = close(x, ref)
            d[name] = {"n": int(c.size), "close_all": round(float(c.mean()), 4),
                       "n_fresh": int((~st).sum()), "close_fresh": round(float(c[~st].mean()), 4) if (~st).any() else None,
                       "n_stale": int(st.sum()), "close_stale": round(float(c[st].mean()), 4) if st.any() else None,
                       "exact_fresh": round(float((x[~st] == ref[~st]).mean()), 4) if (~st).any() else None,
                       "median_absdiff_fresh_miss": float(np.median(np.abs(x - ref)[(~st) & ~c])) if ((~st) & ~c).any() else None}
        d["fundingRate_vs_realizedRate_differ_frac"] = round(float((fr != rr).mean()), 4)
        for tol in (0.01, 0.02, 0.05):
            d[f"fresh_close_fundingRate_rel{tol}"] = round(float(close(x[~st], fr[~st], rel=tol).mean()), 4) if (~st).any() else None
        per_asset[a] = d
        for k in range(taus.size):
            all_rows.append((taus[k], j, bool(st[k]), bool(close(x[k:k+1], fr[k:k+1])[0])))

    # Event-level: how many of 20 assets close at each settlement, fresh vs stale.
    ev_tab = {}
    for t, j, s, c in all_rows:
        e = ev_tab.setdefault(t, {"stale": s, "n": 0, "close": 0}); e["n"] += 1; e["close"] += c
    evs = sorted(ev_tab.items())
    summary = {
        "events": len(evs),
        "fresh_events": sum(not e["stale"] for _, e in evs),
        "fresh_events_all20_close": sum((not e["stale"]) and e["close"] == e["n"] == 20 for _, e in evs),
        "stale_events": sum(e["stale"] for _, e in evs),
        "stale_events_all20_close": sum(e["stale"] and e["close"] == e["n"] == 20 for _, e in evs),
        "fresh_event_close_counts": sorted(e["close"] for _, e in evs if not e["stale"]),
    }
    outd = {"rules": {"stale_s": STALE_S, "pre_s": PRE_S, "tol": "max(0.01*|b|,1e-7)"},
            "archive_rows": int(T.size), "stale_row_frac": round(float(stale.mean()), 4),
            "event_summary": summary, "per_asset": per_asset}
    json.dump(outd, open(out, "w"), indent=1)
    print(json.dumps(summary, indent=1))
    for a, d in per_asset.items():
        print(f"{a:5s} FR all={d['fundingRate']['close_all']:.2f} fresh={d['fundingRate']['close_fresh']} "
              f"(n={d['fundingRate']['n_fresh']}) stale={d['fundingRate']['close_stale']} (n={d['fundingRate']['n_stale']}) "
              f"exact_fresh={d['fundingRate']['exact_fresh']} | RR fresh={d['realizedRate']['close_fresh']} "
              f"| FR!=RR {d['fundingRate_vs_realizedRate_differ_frac']} | fresh tol .02/.05: "
              f"{d['fresh_close_fundingRate_rel0.02']}/{d['fresh_close_fundingRate_rel0.05']} miss|d|={d['fundingRate']['median_absdiff_fresh_miss']}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
