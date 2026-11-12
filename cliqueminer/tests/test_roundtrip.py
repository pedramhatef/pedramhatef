"""Full local round trip through the real Bittensor stack, driven exactly like the CliqueAI validator does it:
AxonRequester -> HTTP -> bt.Axon (signature, nonce, body-hash checks) -> our handler -> response -> validator scoring.
No chain access. Skipped unless bittensor and the CliqueAI repository are importable."""
import asyncio
import json
import os
import socket
import time

import pytest

bt = pytest.importorskip("bittensor")
protocol = pytest.importorskip("CliqueAI.protocol")
aiohttp = pytest.importorskip("aiohttp")

from CliqueAI.graph.model import LambdaGraph  # noqa: E402
from CliqueAI.scoring.clique_scoring import CliqueScoreCalculator  # noqa: E402
from CliqueAI.transport.axon_requester import AxonRequester  # noqa: E402

from cliqueminer import codec  # noqa: E402
from tests.test_miner_integration import stub_miner  # noqa: E402

CLIQUEAI = os.path.dirname(os.path.dirname(os.path.abspath(protocol.__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_wallet(tmp_path, name):
    w = bt.Wallet(name=name, hotkey=name, path=str(tmp_path))
    w.create_new_coldkey(use_password=False, overwrite=True, suppress=True)
    w.create_new_hotkey(use_password=False, overwrite=True, suppress=True)
    return w


@pytest.mark.parametrize("payload,time_limit", [("general_0.4.json", 6.0), ("general_0.2.json", 7.5)])
def test_validator_style_request_gets_a_scored_answer(tmp_path, monkeypatch, payload, time_limit):
    monkeypatch.setattr(bt.utils.networking, "get_external_ip", lambda: "127.0.0.1", raising=False)
    miner_wallet, vali_wallet = make_wallet(tmp_path, "miner"), make_wallet(tmp_path, "vali")
    port = free_port()
    axon = bt.Axon(wallet=miner_wallet, port=port, ip="127.0.0.1", external_ip="127.0.0.1", external_port=port)
    m = stub_miner()
    m.metagraph.hotkeys = [vali_wallet.hotkey.ss58_address, miner_wallet.hotkey.ss58_address]
    axon.attach(forward_fn=m.forward_graph, blacklist_fn=m.blacklist_graph, priority_fn=m.priority_graph)
    axon.start()
    try:
        time.sleep(1.0)
        data = json.load(open(os.path.join(CLIQUEAI, "test_data", payload)))
        adj = codec.decode_matrix(data["encoded_matrix"])
        n = adj.shape[0]
        adjacency_list = [[int(j) for j in adj[i].nonzero()[0]] for i in range(n)]
        # built exactly as CliqueAI/validator.py builds it (it also passes adjacency_list, which is not a protocol field)
        synapse = protocol.MaximumCliqueOfLambdaGraph(uuid="test", label="general", number_of_nodes=n, adjacency_list=adjacency_list,
                                                      encoded_matrix=data["encoded_matrix"], timeout=time_limit)
        dendrite = bt.Dendrite(wallet=vali_wallet)

        async def go():
            async with aiohttp.ClientSession() as session:
                t0 = time.monotonic()
                out = await AxonRequester(session=session, dendrite=dendrite).forward(synapse=synapse, axons=[axon.info()])
                return out, time.monotonic() - t0

        responses, took = asyncio.run(go())
    finally:
        axon.stop()
    answer = responses[0].deserialize()
    assert responses[0].dendrite.status_code == 200, responses[0].dendrite.status_message
    assert took < time_limit - 1.0                         # replied with margin inside the validator's timeout
    assert m.handler.last_timeout == pytest.approx(time_limit)   # the validator's time limit reaches the miner
    graph = LambdaGraph(uuid="test", label="general", number_of_nodes=n, adjacency_list=adjacency_list)
    calc = CliqueScoreCalculator(graph=graph, difficulty=0.8, responses=[answer])
    assert calc.is_valid_maximum_clique(answer)            # the validator's own check
    assert calc.get_scores()[-1][0] == pytest.approx(2.8)  # sole responder: omega_n = delta_n = 1
