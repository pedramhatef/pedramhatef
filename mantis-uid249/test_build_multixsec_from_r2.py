"""Known-answer and fail-closed tests for build_multixsec_from_r2.py (synthetic shards only)."""
import hashlib
import json

import numpy as np
import pytest

import build_multixsec_from_r2 as b

T0 = b.LOCK_SWITCH_SIDX - 30
N = 60


def make(tmp, *, prices_key=True):
    shard_dir = tmp / "shards"; shard_dir.mkdir()
    sid = np.arange(T0, T0 + N, dtype=np.int64)
    shas = []
    for i, s in enumerate(sid):
        hks = [b.TARGET, "hkA", "hkB"] + (["hkC"] if i < 40 else [])        # hkC deregisters at row 40
        if i % 2:
            hks = hks[::-1]                                                  # order changes between shards
        X = np.zeros((len(hks), 194), dtype=np.float16)
        for r, h in enumerate(hks):
            val = {"hkA": .25, "hkB": -.5, "hkC": .75}.get(h, .125 if s < b.LOCK_SWITCH_SIDX else 0.0)
            X[r, 113:146] = val
            X[r, :113] = 9; X[r, 146:] = 9                                  # other challenges: must be ignored
        d = {"X": X, "hotkeys": np.array(hks), "sidx": np.array(s)}
        if prices_key:
            d["prices"] = np.arange(33, dtype=np.float64) + 100 + i
        p = tmp / "tmp.npz"; np.savez(p, **d)
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        p.rename(shard_dir / f"{h}.npz"); shas.append(h)
    np.save(tmp / "sidx.npy", sid)
    np.save(tmp / "sha.npy", np.array(shas, dtype="S64"))
    np.savez(tmp / "clock.npz", sidx=sid, capture_ts=1.8e9 + 60.0 * np.arange(N))
    np.save(tmp / "fund.npy", np.tile(np.arange(20) * 1e-5, (N, 1)))
    return shard_dir, sid, shas


def args(tmp, shard_dir, *extra):
    return ["--manifest-sidx", str(tmp / "sidx.npy"), "--manifest-sha", str(tmp / "sha.npy"),
            "--clock", str(tmp / "clock.npz"), "--shard-dir", str(shard_dir),
            "--funding-values", str(tmp / "fund.npy"),
            "--out", str(tmp / "out.npz"), "--report", str(tmp / "rep.json"), *extra]


def test_happy_path_maps_by_hotkey_and_reports_visibility(tmp_path):
    sd, sid, _ = make(tmp_path)
    assert b.main(args(tmp_path, sd)) == 0
    z = np.load(tmp_path / "out.npz")
    hk = list(z["hotkeys"]); F = z["multixsec"]
    assert hk == sorted([b.TARGET, "hkA", "hkB", "hkC"]) and F.shape == (N, 4, 33)
    assert np.all(F[:, hk.index("hkA")] == .25) and np.all(F[:, hk.index("hkB")] == -.5)
    assert np.all(F[:40, hk.index("hkC")] == .75) and np.all(F[40:, hk.index("hkC")] == 0)
    t = hk.index(b.TARGET)
    assert np.all(F[:30, t] == .125) and np.all(F[30:, t] == 0)
    assert np.all(np.abs(F) < 1)                                             # the 9s from other slices never leak
    assert np.array_equal(z["sidx"], sid) and z["prices"][5, 0] == 105
    assert np.isnan(z["funding33"][:, b.BREAKOUT_ASSETS.index("XMR")]).all()
    rep = json.loads((tmp_path / "rep.json").read_text())
    v = rep["visibility"]
    assert v["target_nonzero_frac_pre_switch"] == 1.0 and v["target_nonzero_frac_post_switch"] == 0.0
    assert v["target_present_but_zero_rows"] == 30


def test_check_only_writes_nothing(tmp_path):
    sd, _, _ = make(tmp_path)
    assert b.main(args(tmp_path, sd, "--check-only")) == 0
    assert not (tmp_path / "out.npz").exists() and not (tmp_path / "rep.json").exists()


def test_missing_shard_stops(tmp_path):
    sd, _, shas = make(tmp_path)
    (sd / f"{shas[7]}.npz").unlink()
    with pytest.raises(RuntimeError, match="1 shards missing"):
        b.main(args(tmp_path, sd))


def test_tampered_shard_stops(tmp_path):
    sd, _, shas = make(tmp_path)
    p = sd / f"{shas[3]}.npz"; raw = bytearray(p.read_bytes()); raw[-30] ^= 1; p.write_bytes(bytes(raw))
    with pytest.raises(RuntimeError, match="sha mismatch"):
        b.main(args(tmp_path, sd))


def test_sidx_mismatch_stops(tmp_path):
    sd, sid, shas = make(tmp_path)
    np.save(tmp_path / "sidx.npy", sid + 1); np.savez(tmp_path / "clock.npz", sidx=sid + 1, capture_ts=1.8e9 + 60.0 * np.arange(N))
    with pytest.raises(RuntimeError, match="contains sidx"):
        b.main(args(tmp_path, sd))


def test_price_crosscheck_mismatch_stops(tmp_path):
    sd, sid, _ = make(tmp_path)
    arch = tmp_path / "arch"; arch.mkdir()
    np.save(arch / "sidx_i64.npy", sid)
    px = np.tile(np.arange(33, dtype=np.float64) + 100, (N, 1)) + np.arange(N)[:, None]
    px[10, 4] += 1e-9
    np.save(arch / "prices_f64.npy", px)
    with pytest.raises(RuntimeError, match="shard prices != archived prices"):
        b.main(args(tmp_path, sd, "--prices-archive", str(arch)))


def test_no_prices_anywhere_stops(tmp_path):
    sd, _, _ = make(tmp_path, prices_key=False)
    with pytest.raises(RuntimeError, match="without a full 33-price vector"):
        b.main(args(tmp_path, sd))
