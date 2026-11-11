"""Read-only SN55 reward-persistence crawl. Samples the incentive vector + reg/last-update and
the hotkey behind each rewarded UID at past blocks via an archive node. Output JSONL.
Usage: history.py OUT DAYS STEP_BLOCKS"""
import bittensor as bt, json, sys, time, threading, concurrent.futures as cf
SM = bt.storage.SubtensorModule
OUT, DAYS, STEP = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
loc = threading.local()
def sub():
    if not hasattr(loc, "s"): loc.s = bt.Subtensor("archive")
    return loc.s
head = bt.Subtensor("archive").block
blocks = list(range(head, head - int(DAYS * 7200), -STEP))
def one(b):
    for attempt in range(4):
        try:
            s = sub()
            inc = s.query(SM.Incentive, [55], block=b)
            lu = s.query(SM.LastUpdate, [55], block=b)
            rewarded = [u for u, x in enumerate(inc) if x > 0]
            hk = {u: str(s.query(SM.Keys, [55, u], block=b)) for u in rewarded}
            reg = {u: int(s.query(SM.BlockAtRegistration, [55, u], block=b)) for u in rewarded}
            ck = {u: str(s.query(SM.Owner, [hk[u]], block=b)) for u in rewarded}
            return {"block": b, "incentive": {u: inc[u] for u in rewarded}, "hotkey": hk, "coldkey": ck, "reg": reg,
                    "lastupdate_119": lu[119] if len(lu) > 119 else None,
                    "n_reg_last_day": sum(1 for u in range(len(inc)) if b - int(s.query(SM.BlockAtRegistration,[55,u],block=b)) < 7200) if False else None}
        except Exception as e:
            err = str(e)[:150]; loc.__dict__.pop("s", None); time.sleep(2)
    return {"block": b, "error": err}
with open(OUT, "w") as f, cf.ThreadPoolExecutor(6) as ex:
    for r in ex.map(one, blocks):
        f.write(json.dumps(r) + "\n"); f.flush()
print("done", len(blocks))
