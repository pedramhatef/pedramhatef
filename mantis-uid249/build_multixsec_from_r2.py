"""Assemble the MULTIXSEC observer field from ORIGINAL R2 NPZ shards (full (miners, 194) X). Off-production, read-only.

Replaces the round-4 bridge, which sliced [113:146] out of challenge-specific extracts (66-D breakout / 20-D funding);
that slice is invalid for those arrays.

Fail-closed rules (no row dropping, no fabricated values):
  * The required rows are exactly the manifest sidx (e.g. the 32,805 funding-archive sidx), in order.
  * Each row's shard is found by its recorded SHA256 (content-addressed: <shard_dir>/<sha256>.npz).
    The file is re-hashed and must match; any missing or mismatching shard -> STOP.
  * The shard's own sidx must equal the manifest sidx. A multi-snapshot shard must contain it exactly once.
  * Miner rows are mapped by the shard's own HOTKEYS array (UIDs are reassigned over time, so they are never used).
  * X must be (n_miners, 194); MULTIXSEC = X[:, 113:146] (layout unchanged at every config commit
    2026-08-17..2026-11-06, verified in round 5).
  * Prices (33, BREAKOUT order) come from the shard key if present. If --prices-archive is also given, both must agree exactly.
  * The output field is OBSERVER data. Values the observer could not decrypt at capture are 0 here, and 0 is NOT
    evidence the validator saw 0. The visibility report quantifies this per hotkey and per window.

Usage (Paris):
  python -I build_multixsec_from_r2.py --manifest-sidx <fund>/sidx_i64.npy --manifest-sha <fund>/sha256_S64.npy \
     --clock <fund>/P0_FABLE_CAPTURE_TIME_VALIDATED_20261112.npz --shard-dir <dir of <sha>.npz> \
     --x-key X --hotkeys-key hotkeys --sidx-key sidx --prices-key prices \
     --out <out>/multixsec.npz --report <out>/INPUT_INTEGRITY_R2.json [--check-only]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np

MULTIXSEC = slice(113, 146)
WIDTH = 194
BREAKOUT_ASSETS = ["BTC", "ETH", "XRP", "SOL", "TRX", "DOGE", "ADA", "BCH", "XMR", "LINK", "LEO", "HYPE", "XLM", "ZEC",
                   "SUI", "LTC", "AVAX", "HBAR", "SHIB", "TON", "CRO", "DOT", "UNI", "MNT", "BGB", "TAO", "AAVE", "PEPE",
                   "NEAR", "ICP", "ETC", "ONDO", "SKY"]
FUNDING_ASSETS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "SUI",
                  "NEAR", "AAVE", "UNI", "LTC", "HBAR", "PEPE", "TRX", "SHIB", "TAO", "ONDO"]
TARGET = "5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD"
LOCK_SWITCH_SIDX = 1880131  # first all-zero UID249 observer row (2026-10-31 01:50:52 UTC)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _s(x) -> str:
    return x.decode() if isinstance(x, (bytes, np.bytes_)) else str(x)


def read_shard(path: Path, expect_sha: str, sidx: int, keys: dict):
    got = sha256_file(path)
    if got != expect_sha:
        raise RuntimeError(f"STOP: sha mismatch for sidx {sidx}: file {got} != manifest {expect_sha}")
    with np.load(path, allow_pickle=False) as z:
        for k in ("x", "hotkeys", "sidx"):
            if keys[k] not in z.files:
                raise RuntimeError(f"STOP: shard {path.name} lacks key '{keys[k]}' (has {z.files})")
        sx = np.atleast_1d(np.asarray(z[keys["sidx"]], dtype=np.int64))
        hit = np.flatnonzero(sx == sidx)
        if hit.size != 1:
            raise RuntimeError(f"STOP: shard {path.name} contains sidx {sidx} {hit.size} times")
        r = int(hit[0])
        X = np.asarray(z[keys["x"]])
        X = X[r] if X.ndim == 3 else X
        hk = [_s(h) for h in (np.asarray(z[keys["hotkeys"]])[r] if np.asarray(z[keys["hotkeys"]]).ndim == 2
                              else np.asarray(z[keys["hotkeys"]]))]
        if X.shape != (len(hk), WIDTH):
            raise RuntimeError(f"STOP: shard {path.name} X shape {X.shape} != ({len(hk)}, {WIDTH})")
        if len(set(hk)) != len(hk):
            raise RuntimeError(f"STOP: duplicate hotkeys in shard {path.name}")
        px = None
        if keys.get("prices") and keys["prices"] in z.files:
            p = np.asarray(z[keys["prices"]], dtype=np.float64)
            px = p[r] if p.ndim == 2 else p
            if px.shape != (33,):
                raise RuntimeError(f"STOP: shard {path.name} prices shape {px.shape} != (33,)")
    return np.asarray(X[:, MULTIXSEC], dtype=np.float32), hk, px


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest-sidx", type=Path, required=True)
    ap.add_argument("--manifest-sha", type=Path, required=True)
    ap.add_argument("--clock", type=Path, required=True)
    ap.add_argument("--shard-dir", type=Path, required=True)
    ap.add_argument("--x-key", default="X")
    ap.add_argument("--hotkeys-key", default="hotkeys")
    ap.add_argument("--sidx-key", default="sidx")
    ap.add_argument("--prices-key", default="prices")
    ap.add_argument("--prices-archive", type=Path, nargs="*", default=[],
                    help="optional dirs with sidx_i64.npy + prices_f64.npy (33 cols) to cross-check")
    ap.add_argument("--funding-values", type=Path, help="(T,20) funding matrix aligned to the manifest")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args(argv)
    keys = {"x": a.x_key, "hotkeys": a.hotkeys_key, "sidx": a.sidx_key, "prices": a.prices_key}

    sid = np.load(a.manifest_sidx).astype(np.int64)
    sha = [_s(s) for s in np.load(a.manifest_sha)]
    with np.load(a.clock, allow_pickle=False) as z:
        ts = np.asarray(z["capture_ts"], dtype=np.float64)
        if not np.array_equal(np.asarray(z["sidx"], dtype=np.int64), sid):
            raise RuntimeError("STOP: clock sidx != manifest sidx")
    if not (len(sid) == len(sha) == len(ts)) or not np.all(np.diff(sid) > 0):
        raise RuntimeError("STOP: manifest lengths differ or sidx not strictly increasing")
    paths = [a.shard_dir / f"{s}.npz" for s in sha]
    missing = [int(sid[i]) for i, p in enumerate(paths) if not p.is_file()]
    pre = {"rows": int(len(sid)), "first_sidx": int(sid[0]), "last_sidx": int(sid[-1]),
           "missing_shards": len(missing), "missing_first10": missing[:10],
           "total_shard_bytes_present": int(sum(p.stat().st_size for p in paths if p.is_file()))}
    print(json.dumps({"preflight": pre}))
    if missing:
        raise RuntimeError(f"STOP: {len(missing)} shards missing; fetch exactly those objects first (no row dropping)")

    # Pass 1: verify every shard and collect the hotkey universe (cheap: per-shard decode then discard).
    hk_all, seen = [], set()
    for i, p in enumerate(paths):
        _, hk, _ = read_shard(p, sha[i], int(sid[i]), keys)
        for h in hk:
            if h not in seen:
                seen.add(h); hk_all.append(h)
    hk_all.sort()
    if TARGET not in seen:
        raise RuntimeError("STOP: target hotkey never present in shards")
    T, H = len(sid), len(hk_all)
    need = T * H * 33 * 4
    print(json.dumps({"verified_shards": T, "hotkeys": H, "field_bytes": need}))
    if a.check_only:
        return 0

    free = os.statvfs(a.out.parent).f_bavail * os.statvfs(a.out.parent).f_frsize
    if free < 2 * need + (2 << 30):
        raise RuntimeError(f"STOP: insufficient disk: free {free} < {2 * need + (2 << 30)}")
    if a.out.exists() or a.report.exists():
        raise RuntimeError("STOP: refusing to overwrite existing output")
    col = {h: j for j, h in enumerate(hk_all)}
    xarch = []
    for d in a.prices_archive:
        xarch.append((np.load(d / "sidx_i64.npy"), np.load(d / "prices_f64.npy", mmap_mode="r")))
    with tempfile.TemporaryDirectory(prefix="mx_r2_", dir=a.out.parent) as td:
        F = np.lib.format.open_memmap(Path(td) / "f.npy", mode="w+", dtype=np.float32, shape=(T, H, 33))
        P = np.full((T, 33), np.nan)
        present = np.zeros((T, H), dtype=bool)
        cross = {"checked": 0, "mismatch": 0}
        for i, p in enumerate(paths):
            mx, hk, px = read_shard(p, sha[i], int(sid[i]), keys)
            idx = np.array([col[h] for h in hk])
            F[i] = 0.0
            F[i, idx, :] = mx
            present[i, idx] = True
            if px is not None:
                P[i] = px
            for asid, apx in xarch:
                k = np.searchsorted(asid, sid[i])
                if k < len(asid) and asid[k] == sid[i]:
                    cross["checked"] += 1
                    ref = np.asarray(apx[k], dtype=np.float64)
                    if px is None:
                        P[i] = ref
                    elif not np.array_equal(ref, px):
                        cross["mismatch"] += 1
        if cross["mismatch"]:
            raise RuntimeError(f"STOP: {cross['mismatch']} rows where shard prices != archived prices")
        if not np.all(np.isfinite(P)):
            raise RuntimeError(f"STOP: {int((~np.isfinite(P).all(1)).sum())} rows without a full 33-price vector")
        F.flush()
        f33 = np.full((T, 33), np.nan, dtype=np.float32)
        if a.funding_values:
            fv = np.load(a.funding_values)
            if fv.shape != (T, 20):
                raise RuntimeError(f"STOP: funding shape {fv.shape} != ({T}, 20)")
            for j, asset in enumerate(FUNDING_ASSETS):
                f33[:, BREAKOUT_ASSETS.index(asset)] = fv[:, j]
        # Visibility: fraction of rows where each hotkey is present AND non-zero (observer-decrypted).
        nz = np.zeros((T, H), dtype=bool)
        for s in range(0, T, 2048):
            nz[s:s + 2048] = np.any(F[s:s + 2048] != 0, axis=2)
        t = col[TARGET]
        post = sid >= LOCK_SWITCH_SIDX
        vis = {
            "target_nonzero_frac_all": round(float(nz[:, t].mean()), 6),
            "target_nonzero_frac_pre_switch": round(float(nz[~post, t].mean()), 6) if (~post).any() else None,
            "target_nonzero_frac_post_switch": round(float(nz[post, t].mean()), 6) if post.any() else None,
            "target_present_but_zero_rows": int((present[:, t] & ~nz[:, t]).sum()),
            "hotkeys_never_nonzero": int((~nz.any(axis=0)).sum()),
            "hotkeys_nonzero_frac_quantiles": np.quantile(nz.mean(axis=0), [0, .1, .5, .9, 1]).round(4).tolist(),
        }
        np.savez(a.out, prices=P, multixsec=F, hotkeys=np.asarray(hk_all), capture_ts=ts, funding33=f33, sidx=sid)
        del F
    rep = {"schema": "UID249_MULTIXSEC_FROM_ORIGINAL_R2_V1",
           "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
           "rows": T, "hotkeys": H, "slice": [113, 146], "every_shard_sha_verified": True,
           "price_crosscheck": cross, "visibility": vis,
           "observer_caveat": "0 = not decrypted/observed at capture; NOT evidence the paying validator saw 0",
           "output_sha256": sha256_file(a.out), "no_production_wallet_r2_mutation": True}
    a.report.write_text(json.dumps(rep, indent=2) + "\n")
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
