import asyncio
import types

import pytest

from cliqueminer import codec, engine
from cliqueminer.handler import RequestHandler
from cliqueminer.solver import native_available
from tests.test_codec import random_graph, ref_encode


def make_synapse(n=120, p=0.8, seed=1, timeout=6.0):
    return types.SimpleNamespace(encoded_matrix=ref_encode(random_graph(n, p, seed)), number_of_nodes=n, timeout=timeout, uuid="t", maximum_clique=[])


def test_handle_fills_valid_answer():
    h = RequestHandler(engine.EngineConfig(threads=2, max_budget=0.3))
    syn, ans = asyncio.run(h.handle(make_synapse()))
    assert ans is not None and syn.maximum_clique == ans.clique
    assert all(isinstance(v, int) for v in syn.maximum_clique)
    assert codec.is_maximal_clique(codec.decode_matrix(syn.encoded_matrix), syn.maximum_clique)


def test_handle_survives_garbage_payload():
    h = RequestHandler(engine.EngineConfig(threads=1, max_budget=0.2))
    syn = make_synapse()
    syn.encoded_matrix = "not a graph"
    syn, ans = asyncio.run(h.handle(syn))
    assert ans is None and syn.maximum_clique == [] and h.stats["errors"] == 1


@pytest.mark.skipif(not native_available(), reason="native solver not built")
def test_overlapping_requests_share_cores_and_all_answer():
    h = RequestHandler(engine.EngineConfig(threads=4, max_budget=0.4))

    async def go():
        return await asyncio.gather(*[h.handle(make_synapse(seed=s)) for s in range(4)])

    out = asyncio.run(go())
    assert h._inflight == 0 and h.stats["requests"] == 4
    for syn, ans in out:
        assert ans is not None and codec.is_maximal_clique(codec.decode_matrix(syn.encoded_matrix), syn.maximum_clique)


def test_answers_are_not_deterministic_across_requests():
    # Returning the same clique for the same graph would make the answer predictable (and collide with any copy of this miner).
    h = RequestHandler(engine.EngineConfig(threads=2, max_budget=0.3))
    syn = make_synapse(n=200, p=0.8, seed=3)
    seen = {tuple(asyncio.run(h.handle(make_synapse(n=200, p=0.8, seed=3)))[0].maximum_clique) for _ in range(6)}
    assert len(seen) > 1
