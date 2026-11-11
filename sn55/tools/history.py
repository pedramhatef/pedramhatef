"""Read-only 30-day crawl of SN55 owner-validator weights + incentive + UID identity. Output JSONL."""
import bittensor as bt, json, sys, time, concurrent.futures as cf, threading
SM = bt.storage.SubtensorModule
OUT = sys.argv[1]; DAYS = float(sys.argv[2]); STEP = int(sys.argv[3])
loc = threading.local()
def sub():
    if not hasattr(loc, 's'): loc.s = bt.Subtensor('archive')
    return loc.s
head = bt.Subtensor('archive').block
blocks = list(range(head, head - int(DAYS * 7200), -STEP))
def one(b):
    for attempt in range(4):
        try:
            s = sub()
            inc = s.query(SM.Incentive, [55], block=b)
            lu = s.query(SM.LastUpdate, [55], block=b)
            reg = s.query(SM.BlockAtRegistration, [55], block=b)
            w119 = s.query(SM.Weights, [55, 119], block=b)
            rewarded = [u for u, x in enumerate(inc) if x > 0]
            hk = {u: str(s.query(SM.Keys, [55, u], block=b)) for u in rewarded}
            ck = {u: str(s.query(SM.Owner, [hk[u]], block=b)) for u in rewarded}
            return {"block": b, "incentive": {u: inc[u] for u in rewarded}, "hotkey": hk, "coldkey": ck,
                    "reg": {u: reg[u] for u in rewarded}, "lastupdate_119": lu[119] if len(lu) > 119 else None,
                    "lastupdate_142": lu[142] if len(lu) > 142 else None, "w119": str(w119),
                    "n_reg_last_day": sum(1 for r in reg if b - r < 7200)}
        except Exception as e:
            err = str(e)[:200]; loc.__dict__.pop('s', None); time.sleep(2)
    return {"block": b, "error": err}
with open(OUT, "w") as f, cf.ThreadPoolExecutor(6) as ex:
    for r in ex.map(one, blocks):
        f.write(json.dumps(r) + "\n"); f.flush()
print("done", len(blocks))
