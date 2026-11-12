"""Isolated SN55 miner launcher.

Subclasses the official Miner (genomesio/subnet-niome) and overrides ONLY process_task, so the
pinned upstream repo is never edited. Wallet/axon/chain wiring is 100% the upstream code.

Env:
  NIOME_REPO   path to the subnet-niome checkout pinned at 0c03fef   (default ~/sn55/subnet-niome)
  SN55_PKG     path to this repo's package root holding solver/ & miner/ (default ~/sn55/pkg)
  CHR11_FA     path to Ensembl r116 chr11.fa
Args: the upstream btcli-style args, e.g.
  --netuid 55 --subtensor.network finney --wallet.name X --wallet.hotkey Y --axon.port 8191
"""
import os, sys, time, logging

NIOME_REPO = os.environ.get("NIOME_REPO", os.path.expanduser("~/sn55/subnet-niome"))
SN55_PKG = os.environ.get("SN55_PKG", os.path.expanduser("~/sn55/pkg"))
for p in (SN55_PKG, NIOME_REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("BT_NO_PARSE_CLI_ARGS", "false")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import threading
import bittensor as bt
import uvicorn
from neurons.miner import Miner as _UpstreamMiner        # official, unmodified
from miner.process_task import process_task as _process_task

SYNC_INTERVAL_S = int(os.environ.get("NIOME_SYNC_INTERVAL_S", "60"))


class Sn55Miner(_UpstreamMiner):
    process_task = _process_task                          # override just the solver hook

    def __init__(self, config=None):
        super().__init__(config=config)
        # The upstream --axon.port flag does not register under bittensor 11, so set the port
        # from NIOME_AXON_PORT (default 8091). Applied after super().__init__ so both the uvicorn
        # bind and the on-chain ServeAxon use it.
        self.axon_port = int(os.environ.get("NIOME_AXON_PORT", "8091"))

    def _setup_routes(self):
        """Replace the upstream /forward route.

        Upstream calls bt.http_auth.verify() with the default require_receiver=True, but the
        validator (niome_subnet/validator/forward.py::query_miner) calls bt.http_auth.sign()
        WITHOUT receiver_ss58 — so no X-Bittensor-Receiver header is ever sent and every request
        is rejected with `WrongReceiver -> 401`. Verified by direct reproduction against
        bittensor 11.0.1. We accept what validators actually send; signature verification, the
        10s nonce freshness window, replay protection and the validator-permit blacklist all
        still apply. Set NIOME_REQUIRE_RECEIVER=1 to restore the strict behaviour.
        """
        from fastapi import Request, HTTPException
        app = self.app
        miner = self
        require_receiver = os.environ.get("NIOME_REQUIRE_RECEIVER") == "1"
        # Live validators' signed nonces arrive >10s old (their clock runs behind ours, which is
        # NTP-correct), so the stock 10s/2s window rejects every request with StaleRequest.
        # Widen it, backed by a nonce store whose retention covers the window so replay protection
        # is preserved (verify() refuses max_age + allowed_skew > retention).
        max_age = float(os.environ.get("NIOME_AUTH_MAX_AGE", "600"))
        allowed_skew = float(os.environ.get("NIOME_AUTH_SKEW", "300"))
        nonce_store = bt.http_auth.InMemoryNonceStore(retention=max_age + allowed_skew + 60.0)
        logging.info(f"/forward auth window: max_age={max_age}s allowed_skew={allowed_skew}s "
                     f"require_receiver={require_receiver}")

        @app.post("/forward")
        async def handle_forward(request: Request):
            client = getattr(request.client, "host", "?")
            try:
                headers = dict(request.headers)
                body = await request.body()
                try:
                    caller = bt.http_auth.verify(
                        headers, body, method="POST", path="/forward",
                        self_hotkey_ss58=miner.wallet.hotkey.ss58_address,
                        require_receiver=require_receiver,
                        max_age=max_age, allowed_skew=allowed_skew, nonce_store=nonce_store,
                    )
                except bt.http_auth.AuthError as e:
                    logging.warning(f"/forward AUTH FAILED from {client}: {type(e).__name__}: {e}")
                    raise HTTPException(status_code=401, detail=str(e))
                logging.info(f"/forward authenticated caller {caller.hotkey_ss58[:12]}.. from {client}")
                if await miner.blacklist(caller.hotkey_ss58):
                    logging.warning(f"/forward blacklisted {caller.hotkey_ss58[:12]}.. from {client}")
                    raise HTTPException(status_code=403, detail="blacklisted")
                return await miner.forward(body, caller.hotkey_ss58)
            except HTTPException:
                raise
            except Exception as e:
                logging.error(f"/forward error from {client}: {type(e).__name__}: {e}")
                raise HTTPException(status_code=500, detail=str(e))

    def _serve_axon_on_chain(self):
        """Publish the REAL public IP, not gethostname() (which can resolve to 127.0.1.1 on a VPS
        and make the axon unreachable -> zero tasks). Set NIOME_AXON_IP to the box's public IPv4."""
        ip = os.environ.get("NIOME_AXON_IP")
        if not ip:
            import socket
            ip = socket.gethostbyname(socket.gethostname())
            if ip.startswith("127."):
                logging.error(f"axon IP resolved to loopback {ip}; set NIOME_AXON_IP=<public IPv4> "
                              "or the validator cannot reach this miner.")
        try:
            self.subtensor.execute(
                bt.ServeAxon(netuid=self.config.netuid, ip=ip, port=self.axon_port), self.wallet)
            logging.info(f"served axon {ip}:{self.axon_port} on netuid {self.config.netuid}")
        except Exception as e:
            logging.error(f"failed to serve axon: {e}")

    def run(self):
        """Replace the upstream run loop, whose throttle (block - last_update < epoch_length)
        busy-loops for a fresh miner whose last_update never advances. Here the loop simply syncs
        every SYNC_INTERVAL_S seconds; the axon server thread handles /forward independently."""
        self.sync()
        self._serve_axon_on_chain()
        logging.info(f"SN55 miner up: UID {getattr(self, 'uid', '?')}, "
                     f"axon {os.environ.get('NIOME_AXON_IP', '?')}:{self.axon_port}, sync every {SYNC_INTERVAL_S}s")
        # access_log on so every inbound /forward (incl. validator POSTs, with source IP) is visible,
        # to diagnose whether validators are reaching the axon. Set NIOME_QUIET=1 to silence it.
        _acc = os.environ.get("NIOME_QUIET") != "1"
        server = uvicorn.Server(uvicorn.Config(self.app, host="0.0.0.0", port=self.axon_port,
                                               log_level="info" if _acc else "warning", access_log=_acc))
        threading.Thread(target=server.run, daemon=True).start()
        try:
            while not self.should_exit:
                try:
                    import time
                    for _ in range(SYNC_INTERVAL_S):
                        if self.should_exit:
                            break
                        time.sleep(1)
                    if self.should_exit:
                        break
                    self.sync()
                    self.step += 1
                except KeyboardInterrupt:
                    raise
                except Exception as err:
                    logging.warning(f"miner step error (retry in 12s): {err}")
                    import time
                    time.sleep(12)
        except KeyboardInterrupt:
            logging.info("SN55 miner stopped by keyboard interrupt")
        finally:
            server.should_exit = True


if __name__ == "__main__":
    logging.info(f"SN55 miner launcher: repo={NIOME_REPO} pkg={SN55_PKG} chr11={os.environ.get('CHR11_FA')}")
    with Sn55Miner() as miner:
        while True:
            time.sleep(10)
