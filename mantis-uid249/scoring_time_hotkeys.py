"""Registered SN123 hotkeys at the block a validator would SCORE each fold (public chain state, read-only).

Source semantics (Barbariandev/MANTIS@ace548a):
  validator.calc_worker: max_block = block_snapshot - TASK_INTERVAL (500); active_hks = set(metagraph.hotkeys)
  ledger._build_xsec_from_db: rows scored only if block <= max_block - PAYLOAD_MATURITY_BLOCKS
  => data ending at sidx s is first scorable at block ~ 5*s + PAYLOAD_MATURITY_BLOCKS + 500.
PAYLOAD_MATURITY_BLOCKS of the PAYING validator is unverified (default 50400); it is an explicit argument and is recorded.

Chain read: SubtensorModule.Keys (netuid, uid) -> hotkey at that block (bittensor >= 11 SDK; historical blocks
are served by the SDK's default public archive endpoints). No wallet, no extrinsic.

Usage (Paris):
  python -I scoring_time_hotkeys.py --fold F1:<last sidx> --fold F2:<last sidx> --fold FULL:<last sidx> \
     --maturity 50400 --out ACTIVE_HOTKEYS.json
  -> pass ACTIVE_HOTKEYS.json["active"] to multixsec_counterfactual.py --active-json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone

NETUID = 123
TASK_INTERVAL = 500


def hotkeys_at(client, block: int, item) -> dict[int, str]:
    out = {}
    for key, value in client.query_map(item, [NETUID], block=block):
        uid = key[-1] if isinstance(key, (tuple, list)) else key
        uid = int(getattr(uid, "value", uid))
        hk = getattr(value, "value", value)
        out[uid] = str(hk)
    if not out:
        raise RuntimeError(f"STOP: no hotkeys returned for netuid {NETUID} at block {block}")
    return out


def main(argv=None, client=None, item=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fold", action="append", required=True, help="NAME:last_data_sidx")
    ap.add_argument("--maturity", type=int, required=True, help="PAYLOAD_MATURITY_BLOCKS assumed for the paying validator")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if client is None:
        import bittensor as bt
        client = bt.Subtensor(network="finney")
        item = bt.storage.SubtensorModule.Keys
    res = {"utc": datetime.now(timezone.utc).isoformat(), "netuid": NETUID,
           "assumption": {"PAYLOAD_MATURITY_BLOCKS": a.maturity, "TASK_INTERVAL": TASK_INTERVAL,
                          "status": "public-source default; paying validator runtime unverified"},
           "folds": {}, "active": {}}
    for spec in a.fold:
        name, s = spec.split(":")
        block = 5 * int(s) + a.maturity + TASK_INTERVAL
        m = hotkeys_at(client, block, item)
        hks = sorted(m.values())
        res["folds"][name] = {"last_data_sidx": int(s), "scoring_block": block, "n_uids": len(m),
                              "hotkeys_sha256": hashlib.sha256("\n".join(hks).encode()).hexdigest()}
        res["active"][name] = hks
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res["folds"], indent=1))
    return res


if __name__ == "__main__":
    main()
