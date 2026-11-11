"""Poll SN55 every 5 min; print UID96 status + rewarded set; shout when scoring resumes."""
import time, bittensor as bt
UID, INTERVAL = 96, 300
s = bt.Subtensor('finney'); last = None
print("monitoring SN55 for UID96 scoring / rewarded-set change ... (Ctrl-C to stop)", flush=True)
while True:
    try:
        blk = s.block; m = s.subnets.metagraph(55); n = m.neurons[UID]
        rew = tuple(sorted(x.uid for x in m.neurons if x.incentive > 0 and x.uid != 119))
        v119 = blk - m.neurons[119].last_update
        tag = ""
        if n.incentive > 0:
            tag = f"  <<<<< UID96 IS SCORED! incentive={n.incentive:.4f} emission={n.emission}"
        elif last is not None and rew != last:
            tag = "  <<<<< REWARDED SET CHANGED — scoring resumed, your miner is now in play"
        print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} blk={blk} uid96_inc={n.incentive:.4f} "
              f"119_age={v119}blk rewarded={rew}{tag}", flush=True)
        last = rew
    except Exception as e:
        print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} err {e}", flush=True)
    time.sleep(INTERVAL)
