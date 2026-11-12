import json
import os
import time

import numpy as np
import pytest

from cliqueminer import codec, engine, policy
from cliqueminer.solver import native_available, solve_native
from tests.test_codec import random_graph, ref_encode

needs_native = pytest.mark.skipif(not native_available(), reason="native solver not built (make -C solver)")
FAST = engine.EngineConfig(threads=2, max_budget=0.5, min_budget=0.2)


def enc(n, p, seed):
    return ref_encode(random_graph(n, p, seed))


@needs_native
@pytest.mark.parametrize("n,p", [(1, 0.5), (2, 0.0), (3, 1.0), (12, 1.0), (30, 0.0), (60, 0.5), (200, 0.8), (300, 0.93)])
def test_answer_is_always_a_valid_maximal_clique(n, p):
    e = enc(n, p, seed=n + 7)
    ans = engine.solve(e, timeout=6.0, seed=1, ecfg=FAST)
    assert codec.is_maximal_clique(codec.decode_matrix(e), ans.clique)
    assert ans.clique == sorted(set(ans.clique))


@needs_native
def test_complete_graph_terminates_quickly():
    t = time.monotonic()
    ans = engine.solve(enc(25, 1.0, 1), timeout=6.0, seed=1, ecfg=engine.EngineConfig(threads=2, max_budget=3.0))
    assert len(ans.clique) == 25 and time.monotonic() - t < 1.0


@needs_native
def test_native_candidates_are_distinct_valid_and_right_sized():
    e = enc(250, 0.8, 3)
    adj = codec.decode_matrix(e)
    res = solve_native(e, 0.5, 2, seed=5)
    assert res.max_cliques and len(set(res.max_cliques)) == len(res.max_cliques)
    assert all(len(c) == res.best and codec.is_maximal_clique(adj, c) for c in res.max_cliques)
    assert len(set(res.sub_cliques)) == len(res.sub_cliques)
    assert all(len(c) == res.best - 1 and codec.is_maximal_clique(adj, c) for c in res.sub_cliques)


@needs_native
def test_finds_planted_clique():
    n, k = 200, 30
    adj = random_graph(n, 0.5, 11)
    planted = list(range(0, 2 * k, 2))
    for a in planted:
        for b in planted:
            if a != b:
                adj[a][b] = 1
    res = solve_native(ref_encode(adj), 0.5, 2, seed=2)
    assert res.best == k and tuple(planted) in res.max_cliques


def test_time_budget_respects_margin():
    cfg = engine.EngineConfig()
    for timeout in (6, 7.5, 10, 15, 30):
        b = engine.time_budget(timeout, cfg)
        assert 0 < b <= timeout - cfg.safety_margin
    assert engine.time_budget(None, cfg) <= cfg.default_timeout - cfg.safety_margin
    assert engine.time_budget(6, cfg, already_elapsed=3.9) <= 0.11       # late arrival shrinks the budget
    assert engine.time_budget(1.0, cfg) == pytest.approx(0.05)           # never negative


@needs_native
def test_respects_wall_clock_budget():
    e = enc(300, 0.9, 9)
    t = time.monotonic()
    engine.solve(e, timeout=6.0, seed=3, ecfg=engine.EngineConfig(threads=2, max_budget=0.6))
    assert time.monotonic() - t < 1.5


def test_fallback_without_native(monkeypatch):
    monkeypatch.setattr(engine, "native_available", lambda: False)
    e = enc(80, 0.7, 4)
    ans = engine.solve(e, timeout=6.0, seed=1, ecfg=FAST)
    assert not ans.native and codec.is_maximal_clique(codec.decode_matrix(e), ans.clique)


def test_fallback_when_native_raises(monkeypatch):
    monkeypatch.setattr(engine, "native_available", lambda: True)
    monkeypatch.setattr(engine, "solve_native", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    e = enc(80, 0.7, 4)
    ans = engine.solve(e, timeout=6.0, seed=1, ecfg=FAST)
    assert not ans.native and "boom" in ans.note and codec.is_maximal_clique(codec.decode_matrix(e), ans.clique)


def test_fallback_when_native_returns_garbage(monkeypatch):
    from cliqueminer.solver import SolveResult
    monkeypatch.setattr(engine, "native_available", lambda: True)
    monkeypatch.setattr(engine, "solve_native", lambda *a, **k: SolveResult(best=3, max_cliques=[(0, 1, 2)], max_hits=[1]))
    e = enc(80, 0.0, 4)                                                   # empty graph: (0,1,2) is not a clique
    ans = engine.solve(e, timeout=6.0, seed=1, ecfg=FAST)
    assert not ans.native and codec.is_maximal_clique(codec.decode_matrix(e), ans.clique)


def test_policy_size_rule():
    cfg = policy.PolicyConfig(eligible_miners=248)
    mx = [(1, 2, 3)] * 5
    sub = [(4, 5)] * 9
    assert [policy.sub_threshold(n, cfg) for n in (295, 495, 695, 895)] == [12, 8, 5, 3]
    assert policy.use_sub_size(11, 9, 300, cfg) and not policy.use_sub_size(12, 9, 300, cfg)
    assert policy.use_sub_size(7, 9, 495, cfg) and not policy.use_sub_size(8, 9, 495, cfg)
    assert policy.use_sub_size(2, 9, 900, cfg) and not policy.use_sub_size(3, 9, 900, cfg)
    assert not policy.use_sub_size(1, 0, 300, cfg)        # nothing smaller to fall back to
    assert not policy.use_sub_size(1, 9, 300, policy.PolicyConfig(mode="always_best"))
    # sizes outside the known tiers fall back to expected_queried / divisor
    assert policy.sub_threshold(100, cfg) == pytest.approx(policy.expected_queried(100, 248) / 8.5)
    assert policy.sub_threshold(1200, cfg) == pytest.approx(policy.expected_queried(1200, 248) / 8.5)
    rng = np.random.default_rng(0)
    assert policy.choose_answer(mx, sub, 300, rng, cfg) == (4, 5)
    assert policy.choose_answer(mx, sub, 900, rng, cfg) == (1, 2, 3)
    order = list(policy.candidate_order([(1,), (2,), (5,)], [(3,)], 900, rng, cfg))     # 3 max cliques: best size first
    assert sorted(order[:3]) == [(1,), (2,), (5,)] and order[3] == (3,)
    order = list(policy.candidate_order([(1,), (2,)], [(3,)], 900, rng, cfg))            # 2 < 3: best-1 first, max cliques as backup
    assert order[0] == (3,) and sorted(order[1:]) == [(1,), (2,)]


def test_selection_probabilities_match_validator_formula():
    got = [round(policy.selection_probability(d), 3) for _, d in policy.TIERS]
    assert got == [0.317, 0.245, 0.166, 0.078]


@needs_native
def test_repo_sample_payloads():
    base = os.environ.get("CLIQUEAI_REPO", "/home/user/toptensor/cliqueai")
    files = [os.path.join(base, "test_data", f) for f in ("sample.json", "general_0.1.json", "general_0.2.json", "general_0.4.json")]
    if not all(os.path.exists(f) for f in files):
        pytest.skip("CliqueAI test data not available")
    for f in files:
        d = json.load(open(f))
        ans = engine.solve(d["encoded_matrix"], d.get("timeout"), seed=1, ecfg=FAST)
        adj = codec.decode_matrix(d["encoded_matrix"])
        assert adj.shape[0] == d["number_of_nodes"] and codec.is_maximal_clique(adj, ans.clique)
