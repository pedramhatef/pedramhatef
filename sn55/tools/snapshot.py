"""Read-only SN55 metagraph snapshot -> JSON. Usage: snapshot.py OUT [block]"""
import sys, json, datetime as dt
import bittensor as bt
s = bt.Subtensor('finney')
block = int(sys.argv[2]) if len(sys.argv) > 2 else None
m = s.subnets.metagraph(55, block=block) if block else s.subnets.metagraph(55)
def j(v):
    try: json.dumps(v); return v
    except Exception: return str(v)
out = {"utc": dt.datetime.now(dt.timezone.utc).isoformat(), "head_block": s.block,
       "metagraph_block": j(m.block), "netuid": 55,
       "meta": {k: j(getattr(m, k, None)) for k in ['name','symbol','tempo','max_uids','num_uids','owner_coldkey','owner_hotkey','price','moving_price','last_step','blocks_since_last_step','network_registered_at','mechid','identity']},
       "neurons": []}
for n in m.neurons:
    out["neurons"].append({k: j(getattr(n, k, None)) for k in ['uid','hotkey','coldkey','active','validator_permit','incentive','emission','dividends','trust','consensus','rank','alpha_stake','tao_stake','total_stake','last_update','block_at_registration','pruning_score','axon']})
json.dump(out, open(sys.argv[1], 'w'), indent=1, default=str)
print("ok", out["head_block"], out["metagraph_block"], len(out["neurons"]))
