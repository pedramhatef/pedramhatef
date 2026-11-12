"""Bittensor miner for subnet 83 (CliqueAI).

Run from an environment where the CliqueAI repository is importable (it provides the wire protocol and the base neuron):

    pip install -e /path/to/CliqueAI            # https://github.com/toptensor/CliqueAI
    make -C solver                              # builds solver/libcliquesolver.so
    python -m cliqueminer.miner --netuid 83 --subtensor.network finney \
        --wallet.name <coldkey> --wallet.hotkey <hotkey> --axon.ip <public-ip> --axon.port <port> \
        --neuron.autoupdate 0 --blacklist.force_validator_permit --logging.info

`--neuron.autoupdate 0` matters: the upstream auto-updater runs `git pull` in the current directory and restarts.
"""
# NOTE: no `from __future__ import annotations` in this module. bt.Axon.attach() reads the handler's first-parameter
# annotation and requires it to be the Synapse *class*; postponed evaluation would hand it a string and attach() fails.
import os
import time
import typing

import bittensor as bt
from CliqueAI.protocol import MaximumCliqueOfLambdaGraph
from common.base.miner import BaseMinerNeuron

from . import engine, policy
from .handler import RequestHandler
from .solver import native_available, native_error


class CliqueMiner(BaseMinerNeuron):
    @classmethod
    def add_args(cls, parser):
        super().add_args(parser)
        cpus = os.cpu_count() or 2
        parser.add_argument("--miner.threads", type=int, default=max(1, cpus - 1), help="Solver threads (shared between overlapping requests).")
        parser.add_argument("--miner.budget_fraction", type=float, default=0.5, help="Fraction of the validator time limit spent solving.")
        parser.add_argument("--miner.max_budget", type=float, default=8.0, help="Upper bound on solving time per request, seconds.")
        parser.add_argument("--miner.safety_margin", type=float, default=2.0, help="Seconds reserved for network and reply.")
        parser.add_argument("--miner.policy", type=str, default="mirror", choices=["mirror", "always_best"], help="Answer selection policy.")
        parser.add_argument("--miner.crowd_divisor", type=float, default=8.0, help="mirror policy: go to best-1 when #max cliques < expected_queried/divisor.")
        parser.add_argument("--miner.min_validator_stake", type=float, default=0.0, help="Ignore callers with less stake than this.")

    def __init__(self, config=None):
        super().__init__(config=config)
        m = self.config.miner
        self.handler = RequestHandler(
            engine.EngineConfig(threads=m.threads, budget_fraction=m.budget_fraction, max_budget=m.max_budget, safety_margin=m.safety_margin),
            policy.PolicyConfig(mode=m.policy, crowd_divisor=m.crowd_divisor),
        )
        if not native_available():
            bt.logging.error(f"Native solver not loaded ({native_error()}). Falling back to a greedy heuristic — build it with `make -C solver`.")
        self._refresh_eligible()
        self.axon.attach(forward_fn=self.forward_graph, blacklist_fn=self.blacklist_graph, priority_fn=self.priority_graph)

    def _refresh_eligible(self) -> None:
        # Same filter the validator's MinerSelector applies before sampling: every uid without validator trust.
        try:
            self.handler.set_eligible_miners(int(sum(1 for vt in self.metagraph.validator_trust if float(vt) <= 0)))
        except Exception as e:                                   # noqa: BLE001
            bt.logging.warning(f"could not refresh eligible-miner count: {e}")

    def resync_metagraph(self):
        super().resync_metagraph()
        self._refresh_eligible()

    async def forward_graph(self, synapse: MaximumCliqueOfLambdaGraph) -> MaximumCliqueOfLambdaGraph:
        t0 = time.monotonic()
        synapse, ans = await self.handler.handle(synapse)
        if ans is None:
            bt.logging.error(f"uuid={synapse.uuid} n={synapse.number_of_nodes}: no answer produced")
        else:
            bt.logging.info(
                f"uuid={synapse.uuid} n={synapse.number_of_nodes} timeout={synapse.timeout} -> size={len(ans.clique)} best={ans.best_size} "
                f"max_cliques={ans.n_max} sub_cliques={ans.n_sub} used_sub={ans.used_sub} native={ans.native} "
                f"solve={ans.solve_seconds:.2f}s total={time.monotonic() - t0:.2f}s {ans.note}"
            )
        return synapse

    async def blacklist_graph(self, synapse: MaximumCliqueOfLambdaGraph) -> typing.Tuple[bool, str]:
        # The base implementation calls hotkeys.index() before checking membership and raises for unknown callers.
        hotkey = getattr(getattr(synapse, "dendrite", None), "hotkey", None)
        if hotkey is None:
            return True, "missing dendrite hotkey"
        if hotkey not in self.metagraph.hotkeys:
            return (False, "unregistered caller allowed by config") if self.config.blacklist.allow_non_registered else (True, "unrecognized hotkey")
        uid = self.metagraph.hotkeys.index(hotkey)
        if self.config.blacklist.force_validator_permit and not bool(self.metagraph.validator_permit[uid]):
            return True, "caller has no validator permit"
        if float(self.metagraph.S[uid]) < self.config.miner.min_validator_stake:
            return True, "caller stake below minimum"
        return False, "ok"

    async def priority_graph(self, synapse: MaximumCliqueOfLambdaGraph) -> float:
        hotkey = getattr(getattr(synapse, "dendrite", None), "hotkey", None)
        if hotkey is None or hotkey not in self.metagraph.hotkeys:
            return 0.0
        return float(self.metagraph.S[self.metagraph.hotkeys.index(hotkey)])


if __name__ == "__main__":
    with CliqueMiner() as miner:
        bt.logging.info("cliqueminer started.")
        while not miner.should_exit:
            time.sleep(5)
            if miner.step and miner.step % 50 == 0:
                bt.logging.info(f"stats: {miner.handler.stats}")
