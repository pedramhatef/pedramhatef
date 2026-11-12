"""Request handling that does not depend on bittensor: turns a synapse-like object into an answer under a deadline.

Split from miner.py so it can be exercised offline (tests/test_handler.py) with real validator payloads.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from . import engine, policy

log = logging.getLogger("cliqueminer")


class RequestHandler:
    def __init__(self, ecfg: engine.EngineConfig | None = None, pcfg: policy.PolicyConfig | None = None, max_parallel: int = 4):
        self.ecfg = ecfg or engine.EngineConfig()
        self.pcfg = pcfg or policy.PolicyConfig()
        self._pool = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="clique")
        self._inflight = 0
        self._lock = threading.Lock()
        self.stats = {"requests": 0, "fallbacks": 0, "errors": 0, "late": 0}
        self.last_timeout: float | None = None       # time limit carried by the most recent request (for monitoring)

    def set_eligible_miners(self, n: int) -> None:
        if n > 0:
            self.pcfg = replace(self.pcfg, eligible_miners=int(n))

    def _threads_for_request(self) -> int:
        # Validators query independently, so two graphs can overlap. Share the cores instead of oversubscribing them.
        with self._lock:
            self._inflight += 1
            return max(1, self.ecfg.threads // self._inflight)

    def _done(self) -> None:
        with self._lock:
            self._inflight -= 1

    def solve_sync(self, encoded: str, timeout: float | None, received_at: float | None = None) -> engine.Answer:
        threads = self._threads_for_request()
        try:
            seed = int.from_bytes(os.urandom(8), "little")      # fresh randomness per request: answers must not be predictable
            return engine.solve(encoded, timeout, seed, replace(self.ecfg, threads=threads), self.pcfg, received_at)
        finally:
            self._done()

    async def handle(self, synapse):
        """Fill synapse.maximum_clique. On any failure the field is left empty (scored 0, same as no reply) rather than
        raising inside the axon."""
        received_at = time.monotonic()
        self.stats["requests"] += 1
        timeout = getattr(synapse, "timeout", None)
        self.last_timeout = timeout
        try:
            ans = await asyncio.get_running_loop().run_in_executor(self._pool, self.solve_sync, synapse.encoded_matrix, timeout, received_at)
        except Exception as e:                                   # noqa: BLE001
            self.stats["errors"] += 1
            log.error("request failed: %s", e)
            synapse.maximum_clique = []
            return synapse, None
        synapse.maximum_clique = ans.clique
        if not ans.native:
            self.stats["fallbacks"] += 1
        took = time.monotonic() - received_at
        if timeout and took > float(timeout) - 0.5:
            self.stats["late"] += 1
        return synapse, ans
