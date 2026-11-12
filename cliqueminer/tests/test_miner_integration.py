"""Exercise the Bittensor-facing layer against the real CliqueAI protocol class, without touching the chain.

Skipped unless both `bittensor` and the CliqueAI repository are importable
(e.g. PYTHONPATH=/path/to/CliqueAI pytest tests/test_miner_integration.py).
"""
import asyncio
import json
import os
import types

import numpy as np
import pytest

bt = pytest.importorskip("bittensor")
protocol = pytest.importorskip("CliqueAI.protocol")

from cliqueminer import codec, engine, policy  # noqa: E402
from cliqueminer.handler import RequestHandler  # noqa: E402
from cliqueminer.miner import CliqueMiner  # noqa: E402

CLIQUEAI = os.path.dirname(os.path.dirname(os.path.abspath(protocol.__file__)))


def stub_miner(validator_permit=(True, False), stake=(1000.0, 5.0), force_permit=True, min_stake=0.0):
    """A CliqueMiner without wallet/subtensor/axon: only what the request path touches."""
    m = CliqueMiner.__new__(CliqueMiner)
    m.handler = RequestHandler(engine.EngineConfig(threads=2, max_budget=0.4), policy.PolicyConfig())
    m.metagraph = types.SimpleNamespace(hotkeys=["VALI", "MINER"], validator_permit=list(validator_permit), S=list(stake), validator_trust=[1.0, 0.0])
    m.config = types.SimpleNamespace(blacklist=types.SimpleNamespace(force_validator_permit=force_permit, allow_non_registered=False),
                                     miner=types.SimpleNamespace(min_validator_stake=min_stake))
    return m


def synapse_from_repo(name):
    data = json.load(open(os.path.join(CLIQUEAI, "test_data", name)))
    return protocol.MaximumCliqueOfLambdaGraph.model_validate(data)


@pytest.mark.parametrize("name", ["sample.json", "general_0.1.json", "general_0.2.json", "general_0.4.json"])
def test_forward_fills_a_valid_answer_on_the_real_synapse_class(name):
    syn = synapse_from_repo(name)
    out = asyncio.run(stub_miner().forward_graph(syn))
    assert out is syn and isinstance(out.maximum_clique, list) and all(type(v) is int for v in out.maximum_clique)
    assert codec.is_maximal_clique(codec.decode_matrix(syn.encoded_matrix), out.maximum_clique)
    # the reply must survive the serialisation the axon applies and what the validator reads back
    again = protocol.MaximumCliqueOfLambdaGraph.model_validate(json.loads(out.model_dump_json()))
    assert again.deserialize() == out.maximum_clique


def test_reply_stays_under_validator_size_limit():
    syn = synapse_from_repo("general_0.4.json")
    out = asyncio.run(stub_miner().forward_graph(syn))
    assert len(json.dumps(out.model_dump()).encode()) < 100 * 1024       # AxonRequester.MAX_CONTENT_LENGTH


def with_caller(hotkey):
    syn = synapse_from_repo("sample.json")
    syn.dendrite = bt.TerminalInfo(hotkey=hotkey) if hotkey is not None else None
    return syn


def test_blacklist_rules():
    m = stub_miner()
    assert asyncio.run(m.blacklist_graph(with_caller("VALI"))) == (False, "ok")
    assert asyncio.run(m.blacklist_graph(with_caller("MINER")))[0] is True          # registered but no validator permit
    assert asyncio.run(m.blacklist_graph(with_caller("STRANGER")))[0] is True       # base class would raise ValueError here
    assert asyncio.run(m.blacklist_graph(with_caller(None)))[0] is True
    assert asyncio.run(stub_miner(min_stake=5000.0).blacklist_graph(with_caller("VALI")))[0] is True
    assert asyncio.run(stub_miner(force_permit=False).blacklist_graph(with_caller("MINER")))[0] is False


def test_priority_is_stake_and_safe_for_unknown_callers():
    m = stub_miner()
    assert asyncio.run(m.priority_graph(with_caller("VALI"))) == 1000.0
    assert asyncio.run(m.priority_graph(with_caller("STRANGER"))) == 0.0


def test_eligible_miner_count_follows_metagraph():
    m = stub_miner()
    m._refresh_eligible()
    assert m.handler.pcfg.eligible_miners == 1                                       # one uid without validator trust


def test_axon_attach_accepts_the_handlers(tmp_path):
    """bt.Axon.attach() inspects the handlers' signatures; this fails if their annotations are not real classes."""
    wallet = bt.Wallet(name="t", hotkey="t", path=str(tmp_path))
    wallet.create_new_coldkey(use_password=False, overwrite=True, suppress=True)
    wallet.create_new_hotkey(use_password=False, overwrite=True, suppress=True)
    axon = bt.Axon(wallet=wallet, port=18091, ip="127.0.0.1", external_ip="127.0.0.1", external_port=18091)
    m = stub_miner()
    axon.attach(forward_fn=m.forward_graph, blacklist_fn=m.blacklist_graph, priority_fn=m.priority_graph)
    assert "MaximumCliqueOfLambdaGraph" in axon.forward_class_types
