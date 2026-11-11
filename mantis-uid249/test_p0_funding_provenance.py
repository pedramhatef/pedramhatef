"""Offline known-answer tests for p0_funding_provenance.py (venue fetchers mocked; no network)."""
import json
import os

import numpy as np
import pytest

import p0_funding_provenance as p0

T0 = 1_760_054_400.0  # 2025-10-10 00:00 UTC (any midnight works)
DAYS = 12  # >= 30 eight-hour settlements (rule min_n); the real archive spans ~22 days


def _world(tmp_path):
    rng = np.random.default_rng(3)
    T = T0 + 60.0 * np.arange(DAYS * 1440) + 7.0          # capture 7 s after each minute
    A = len(p0.FUNDING_ASSETS)
    F = np.full((T.size, A), np.nan)
    okx, hl = {}, {}
    for j in range(A):
        # 8h OKX-like: estimate drifts to the final rate within each interval.
        taus8 = T0 + 28800.0 * np.arange(1, DAYS * 3 + 1)
        fin8 = rng.normal(1e-4, 8e-5, taus8.size)
        okx[j] = list(zip(taus8.tolist(), fin8.tolist()))
        # 1h HL-like settled rates.
        taus1 = T0 + 3600.0 * np.arange(1, DAYS * 24 + 1)
        fin1 = rng.normal(1.25e-5, 1e-5, taus1.size)
        hl[j] = list(zip(taus1.tolist(), fin1.tolist()))
        est = np.empty(T.size)
        for k, tau in enumerate(taus8):
            m = (T > tau - 28800) & (T <= tau)
            frac = (T[m] - (tau - 28800)) / 28800
            est[m] = fin8[k] + (1 - frac) * rng.normal(0, 5e-5)
        sett = np.full(T.size, fin1[0])
        for k, tau in enumerate(taus1):
            sett[T >= tau + 120] = fin1[k]               # published 2 min after settlement
        if j < 10:
            F[:, j] = est
        elif j < 18:
            F[:, j] = sett
        else:
            F[:, j] = 0.5 * est + 0.5 * sett * 8       # aggregate of both, 8h-equivalent
    np.savez(tmp_path / "a.npz", funding=F, capture_ts=T, sidx=(np.arange(T.size) + 1880000))
    return okx, hl


def _run(tmp_path, monkeypatch, okx, hl, dup=False):
    def mk(src, name):
        def f(fx, asset, t0, t1):
            j = p0.FUNDING_ASSETS.index(asset)
            return f"{name}:{asset}", [r for r in src[j] if t0 <= r[0] <= t1], None
        return f
    monkeypatch.setattr(p0, "VENUES", {"okx": mk(okx, "okx"), "hyperliquid": mk(hl, "hl")}
                        | ({"okx_dup": mk(okx, "dup")} if dup else {}))
    out = tmp_path / "o"
    p0.main(["--npz", str(tmp_path / "a.npz"), "--out", str(out),
             "--venues", "okx,hyperliquid" + (",okx_dup" if dup else "")])
    return json.load(open(out / "P0_PROVENANCE_RESULT.json"))


def test_known_answers(tmp_path, monkeypatch):
    okx, hl = _world(tmp_path)
    r = _run(tmp_path, monkeypatch, okx, hl)
    d = {a: r["assets"][a]["decision"] for a in p0.FUNDING_ASSETS}
    for a in p0.FUNDING_ASSETS[:10]:
        assert d[a] == {"status": "VERIFIED", "venue": "okx", "semantics": "estimated"}, (a, d[a])
    for a in p0.FUNDING_ASSETS[10:18]:
        assert d[a] == {"status": "VERIFIED", "venue": "hyperliquid", "semantics": "settled"}, (a, d[a])
        assert r["assets"][a]["per_venue"]["hyperliquid"]["post_settlement"]["delay_s"] == 120
    for a in p0.FUNDING_ASSETS[18:]:
        assert d[a]["status"] == "BLOCKED", (a, d[a])


def test_ambiguous_when_two_venues_identical(tmp_path, monkeypatch):
    okx, hl = _world(tmp_path)
    r = _run(tmp_path, monkeypatch, okx, hl, dup=True)
    assert r["assets"]["BTC"]["decision"]["status"] == "AMBIGUOUS"


def test_phase_a_offline_and_no_labels(tmp_path, monkeypatch):
    _world(tmp_path)
    def boom(*a, **k):
        raise AssertionError("network/venue called in phase A")
    monkeypatch.setattr(p0, "VENUES", {"okx": boom})
    p0.main(["--npz", str(tmp_path / "a.npz"), "--out", str(tmp_path / "o"), "--phase", "A"])
    r = json.load(open(tmp_path / "o" / "P0_PROVENANCE_RESULT.json"))
    assert "assets" not in r and len(r["fingerprint"]) == 20
    hrs = r["fingerprint"]["SOL"]["changes_by_utc_hour"]
    assert sum(hrs) > 0
    # HL-like settled asset changes only once per hour, at minute 2 (2-min publication delay).
    assert r["fingerprint"]["HBAR"]["changes_by_minute_of_hour_top5"][0][0] == 2
