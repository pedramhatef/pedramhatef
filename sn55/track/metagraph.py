"""Read-only SN55 metagraph snapshot, trimmed to what the tracker needs.

Records our UID's on-chain incentive/emission/rank/trust, the rewarded set (incentive>0) with the
rank-10 cutoff, and the subnet owner. Chain access only; never signs or submits anything.
Importable without bittensor (the snapshot() call needs it; parsing a saved snapshot does not).
"""
import datetime as dt


def _f(v):
    import re
    if isinstance(v, str):
        try:
            return float(re.sub(r"[^0-9.eE+-]", "", v) or 0)
        except Exception:
            return 0.0
    return float(v or 0)


def snapshot(netuid=55, network="finney", our_uid=None, our_hotkey=None):
    """Live metagraph -> dict. Requires bittensor + chain reachability (true on the box)."""
    import bittensor as bt
    s = bt.Subtensor(network)
    m = s.subnets.metagraph(netuid)
    head = s.block
    neurons = []
    for n in m.neurons:
        neurons.append({k: getattr(n, k, None) for k in
                        ["uid", "hotkey", "coldkey", "validator_permit", "incentive", "emission",
                         "dividends", "trust", "consensus", "rank", "total_stake", "last_update",
                         "block_at_registration", "axon"]})
    out = {"utc": dt.datetime.now(dt.timezone.utc).isoformat(), "head_block": head,
           "metagraph_block": int(getattr(m, "block", 0) or 0), "netuid": netuid,
           "tempo": int(getattr(m, "tempo", 0) or 0),
           "owner_hotkey": getattr(m, "owner_hotkey", None), "neurons": neurons}
    return summarize(out, our_uid=our_uid, our_hotkey=our_hotkey)


def summarize(snap, our_uid=None, our_hotkey=None):
    """Attach rewarded-set analytics and locate our UID. Works on a live or saved snapshot."""
    N = snap["neurons"]
    for n in N:
        n["_inc"] = _f(n.get("incentive")); n["_em"] = _f(n.get("emission"))
    rewarded = sorted([n for n in N if n["_inc"] > 0], key=lambda n: -n["_inc"])
    tempo = snap.get("tempo") or 1
    inc_sum = sum(n["_inc"] for n in rewarded) or 1.0
    def axon_ip(n):
        a = n.get("axon");  a = a if isinstance(a, str) else str(a)
        return a.rsplit(":", 1)[0] if a and a != "None" else None
    snap["analytics"] = {
        "n_rewarded": len(rewarded),
        "cut10_incentive": rewarded[9]["_inc"] if len(rewarded) >= 10 else (rewarded[-1]["_inc"] if rewarded else 0.0),
        "cut5_incentive": rewarded[4]["_inc"] if len(rewarded) >= 5 else (rewarded[-1]["_inc"] if rewarded else 0.0),
        "top": [{"uid": n["uid"], "incentive": round(n["_inc"], 6),
                 "share_pct": round(n["_inc"] / inc_sum * 100, 3),
                 "em_per_day": round(n["_em"] * 7200 / tempo, 4),
                 "ip": axon_ip(n)} for n in rewarded[:10]],
    }
    us = None
    for n in N:
        if (our_uid is not None and n.get("uid") == our_uid) or \
           (our_hotkey is not None and n.get("hotkey") == our_hotkey):
            us = n; break
    if us:
        rank = next((i + 1 for i, n in enumerate(rewarded) if n is us), None)
        snap["ours"] = {"uid": us.get("uid"), "incentive": round(us["_inc"], 6),
                        "em_per_day": round(us["_em"] * 7200 / tempo, 4),
                        "trust": _f(us.get("trust")), "consensus": _f(us.get("consensus")),
                        "rank_among_rewarded": rank, "in_top10": bool(rank and rank <= 10),
                        "last_update_age": (snap["head_block"] - int(us.get("last_update") or 0)),
                        "axon": us.get("axon") if isinstance(us.get("axon"), str) else str(us.get("axon"))}
    else:
        snap["ours"] = None
    for n in N:
        n.pop("_inc", None); n.pop("_em", None)
    return snap


if __name__ == "__main__":
    import sys, json
    snap = snapshot(our_uid=int(sys.argv[1]) if len(sys.argv) > 1 else 96)
    print(json.dumps({"utc": snap["utc"], "block": snap["head_block"],
                      "analytics": snap["analytics"], "ours": snap["ours"]}, indent=1, default=str))
