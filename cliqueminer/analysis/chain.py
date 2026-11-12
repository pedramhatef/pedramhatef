"""On-chain snapshot of a subnet's miners (needs the `bittensor` package and access to a Finney endpoint).

    python -m analysis.chain --netuid 83 --out data/metagraph.json
"""
from __future__ import annotations

import argparse
import collections
import json


def snapshot(netuid: int = 83, network: str = "finney") -> dict:
    import bittensor as bt

    st = bt.Subtensor(network=network)
    block = st.get_current_block()
    mg = st.metagraph(netuid)
    neurons = []
    for u in range(int(mg.n)):
        neurons.append({"uid": u, "hotkey": mg.hotkeys[u], "coldkey": mg.coldkeys[u], "incentive": float(mg.incentive[u]),
                        "emission": float(mg.emission[u]), "stake": float(mg.stake[u]), "validator_trust": float(mg.validator_trust[u]),
                        "validator_permit": bool(mg.validator_permit[u]), "dividends": float(mg.dividends[u]),
                        "registered_at_block": int(mg.block_at_registration[u]), "ip": mg.axons[u].ip})
    extra = {}
    for name, fn in (("registration_cost", lambda: str(st.recycle(netuid))), ("hyperparameters", lambda: str(st.get_subnet_hyperparameters(netuid))),
                     ("subnet", lambda: str(st.subnet(netuid)))):
        try:
            extra[name] = fn()
        except Exception as e:  # noqa: BLE001 - optional fields differ between SDK versions
            extra[name] = f"unavailable: {type(e).__name__}"
    return {"netuid": netuid, "block": block, "neurons": neurons, "extra": extra}


def summarize(snap: dict) -> dict:
    block = snap["block"]
    miners = [x for x in snap["neurons"] if x["validator_trust"] <= 0]
    by_incentive = sorted(miners, key=lambda x: -x["incentive"])
    total = sum(x["incentive"] for x in miners) or 1.0
    per_ck = collections.defaultdict(list)
    for x in miners:
        per_ck[x["coldkey"]].append(x)
    ops = []
    for ck, xs in sorted(per_ck.items(), key=lambda kv: -sum(x["incentive"] for x in kv[1])):
        ages = sorted((block - x["registered_at_block"]) / 7200 for x in xs)
        ops.append({"coldkey": ck, "miners": len(xs), "ips": len({x["ip"] for x in xs}), "incentive_share": sum(x["incentive"] for x in xs) / total,
                    "in_top20": sum(1 for x in by_incentive[:20] if x["coldkey"] == ck), "median_age_days": ages[len(ages) // 2]})
    return {"block": block, "miners": len(miners), "validators": len(snap["neurons"]) - len(miners),
            "incentive_rank1": by_incentive[0]["incentive"], "incentive_rank20": by_incentive[19]["incentive"] if len(by_incentive) >= 20 else None,
            "incentive_median": by_incentive[len(by_incentive) // 2]["incentive"], "operators": ops, "registration_cost": snap["extra"].get("registration_cost")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--netuid", type=int, default=83)
    ap.add_argument("--network", default="finney")
    ap.add_argument("--out", default="data/metagraph.json")
    a = ap.parse_args()
    snap = snapshot(a.netuid, a.network)
    json.dump(snap, open(a.out, "w"))
    s = summarize(snap)
    print(f"block {s['block']}: {s['miners']} miners, {s['validators']} validators; registration cost {s['registration_cost']}")
    print(f"incentive: rank 1 {s['incentive_rank1']:.5f}, rank 20 {s['incentive_rank20']:.5f}, median {s['incentive_median']:.5f}")
    print(f"{'coldkey':12s} {'miners':>6s} {'IPs':>4s} {'share':>7s} {'top20':>6s} {'age(d)':>7s}")
    for o in s["operators"]:
        print(f"{o['coldkey'][:10]:12s} {o['miners']:6d} {o['ips']:4d} {o['incentive_share']:7.1%} {o['in_top20']:6d} {o['median_age_days']:7.1f}")


if __name__ == "__main__":
    main()
