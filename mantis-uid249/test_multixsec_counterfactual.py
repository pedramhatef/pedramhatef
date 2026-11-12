"""Known-answer tests for multixsec_counterfactual.py against the pinned, unmodified xsec_rank.py.

Set MANTIS_SRC to the pinned checkout (ace548a). Synthetic data only.
"""
import logging
import os
import sys

import numpy as np
import pytest

import multixsec_counterfactual as mc

MANTIS = os.environ.get("MANTIS_SRC")
pytestmark = pytest.mark.skipif(not MANTIS, reason="set MANTIS_SRC to the pinned MANTIS checkout")


@pytest.fixture(scope="module")
def scorer():
    sys.path.insert(0, MANTIS)
    logging.disable(logging.CRITICAL)
    import xsec_rank
    xsec_rank.tqdm = lambda *a, **k: type("N", (), {"update": lambda s, n=1: None, "close": lambda s: None})()
    return xsec_rank


def world(T, H=10, A=33, zero_cols=3, seed=0):
    rng = np.random.default_rng(seed)
    logp = np.cumsum(rng.normal(0, 1e-3, (T, A)), axis=0)
    prices = np.exp(logp) * 100
    fwd = np.zeros((T, A)); fwd[:-240] = logp[240:] - logp[:-240]
    rel = fwd - np.median(fwd, axis=1, keepdims=True)
    X = np.zeros((T, H + zero_cols, A), dtype=np.float32)
    for h in range(H):
        X[:, h, :] = np.tanh(rng.uniform(0, 300) * rel + rng.normal(0, 1, (T, A)))
    funding = np.full((T, A), np.nan); funding[:, :20] = rng.normal(1e-4, 5e-5, (T, 20))
    hks = [f"hk{i}" for i in range(H + zero_cols)]
    return prices, X, hks, funding


def test_candidates_are_causal():
    prices, X, hks, f = world(12000)
    cur = X[:, 0, :].astype(float)
    t = 11000
    p2, f2, c2 = prices.copy(), f.copy(), cur.copy()
    rng = np.random.default_rng(9)
    p2[t + 1:] *= rng.uniform(0.5, 2, p2[t + 1:].shape); f2[t + 1:, :20] = rng.normal(0, 1, (12000 - t - 1, 20))
    c2[t + 1:] = -c2[t + 1:]
    for v in ("V1", "V2", "R", "C1"):
        a = mc.build_candidate(prices[: t + 1], f[: t + 1], cur[: t + 1], v)
        b = mc.build_candidate(p2, f2, c2, v)[: t + 1]
        assert np.array_equal(a, b), v
    r = mc.build_candidate(prices[:50], f[:50], cur[:50], "R")
    z = mc.robust_z(np.where(cur[10] != 0, cur[10], np.nan))
    assert np.allclose(r[10], np.where(np.isfinite(z), np.tanh(z), 0.0), atol=1e-6)   # R is rescale-only
    v1 = mc.build_candidate(prices[:50], f[:50], cur[:50], "V1")
    assert np.all(v1[:, 20:] == 0) and np.all(np.abs(v1) <= 1)


def test_default_score_is_bit_exact_and_drop_is_numerically_close(scorer):
    prices, X, hks, _ = world(17500)
    full = scorer.compute_xsec_rank_salience(
        (X.reshape(X.shape[0], -1).astype(np.float32), {h: i for i, h in enumerate(hks)}), prices, 1200, 5)
    default = mc.score(scorer, X, hks, prices)
    assert full == default                                   # default path: bit-exact with a direct call
    dropped = mc.score(scorer, X, hks, prices, drop_zero=True)
    assert full.keys() == dropped.keys()
    assert max(abs(full[k] - dropped[k]) for k in full) < 1e-5
    assert sorted(full, key=full.get) == sorted(dropped, key=dropped.get)


@pytest.mark.parametrize("T,sl", [(32800, slice(0, None)), (32800, slice(0, 24240)), (32800, slice(8000, 32240))])
def test_segment_replica_matches_scorer(scorer, T, sl):
    prices, X, hks, _ = world(T, H=6, zero_cols=0, seed=2)
    P, Xs = prices[sl], X[sl]
    scorer._QUALITY_TRACE = True; scorer._QUALITY_LOG.clear()
    scorer.compute_xsec_rank_salience((Xs.reshape(Xs.shape[0], -1), {h: i for i, h in enumerate(hks)}), P, 1200, 5)
    scored = [s for (_, s, _, _) in scorer._QUALITY_LOG]
    scorer._QUALITY_TRACE = False
    rep = mc.scorable_segments(Xs.shape[0], sl.start or 0)
    assert len(scored) == len(rep), (scored, rep)
    if sl == slice(0, 24240):
        assert rep == [(16000, 16060, 24000)]
    if sl == slice(8000, 32240):
        assert rep == [(24000, 24060, 32000)]
