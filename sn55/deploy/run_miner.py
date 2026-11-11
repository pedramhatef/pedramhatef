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

import bittensor as bt
from neurons.miner import Miner as _UpstreamMiner        # official, unmodified
from miner.process_task import process_task as _process_task


class Sn55Miner(_UpstreamMiner):
    process_task = _process_task                          # override just the solver hook

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


if __name__ == "__main__":
    logging.info(f"SN55 miner launcher: repo={NIOME_REPO} pkg={SN55_PKG} chr11={os.environ.get('CHR11_FA')}")
    with Sn55Miner() as miner:
        while True:
            time.sleep(10)
