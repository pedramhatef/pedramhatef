import json, sys, re, collections
d = json.load(open(sys.argv[1]))
f = lambda s: float(re.sub(r'[^0-9.]', '', s)) if isinstance(s, str) else float(s or 0)
N = d['neurons']; tempo = d['meta']['tempo']; head = d['metagraph_block']
for n in N:
    n['em'] = f(n['emission']); n['stake'] = f(n['total_stake']); n['ip'] = (n['axon'] or 'None').rsplit(':',1)[0]
tot_em = sum(n['em'] for n in N)
print(f"block {head} utc {d['utc']} tempo {tempo} price {d['meta']['price']:.6f} TAO/alpha")
print(f"sum emission/tempo {tot_em:.3f} alpha -> per day {tot_em*7200/tempo:.1f}")
inc = [n for n in N if n['incentive'] > 0]
vals = [n for n in N if n['validator_permit']]
divs = [n for n in N if n['dividends'] > 0]
print(f"nonzero incentive: {len(inc)}; validator_permit: {len(vals)}; nonzero dividends: {len(divs)}; nonzero emission: {sum(1 for n in N if n['em']>0)}")
print(f"axon None: {sum(1 for n in N if n['axon'] in (None,'None') or str(n['axon']).startswith('0.0.0.0'))}; distinct IPs: {len(set(n['ip'] for n in N))}; distinct coldkeys: {len(set(n['coldkey'] for n in N))}")
print(f"owner hotkey uid: {[n['uid'] for n in N if n['hotkey']==d['meta']['owner_hotkey']]}")
inc_sum = sum(n['incentive'] for n in inc)
print("\nREWARDED (incentive>0):\nuid | hotkey | coldkey | vpermit | incentive | share | em/tempo | alpha/day | regblk | age_d | last_update_age | ip")
for n in sorted(inc, key=lambda n: -n['incentive']):
    print(f"{n['uid']:3d} | {n['hotkey'][:10]}.. | {n['coldkey'][:10]}.. | {n['validator_permit']} | {n['incentive']:.5f} | {n['incentive']/inc_sum*100:6.3f}% | {n['em']:.3f} | {n['em']*7200/tempo:8.1f} | {n['block_at_registration']} | {(head-n['block_at_registration'])/7200:5.1f} | {head-n['last_update']} | {n['ip']}")
print("\nVALIDATORS (permit or dividends>0):")
for n in sorted(set(x['uid'] for x in vals+divs)):
    n = N[n]; print(f"{n['uid']:3d} {n['hotkey'][:10]}.. ck {n['coldkey'][:10]}.. stake {n['stake']:.0f} div {n['dividends']:.4f} trust {n['trust']:.3f} last_upd_age {head-n['last_update']} em/day {n['em']*7200/tempo:.1f} axon {n['axon']}")
ck = collections.Counter(n['coldkey'] for n in N)
print("\nTop coldkeys by #UIDs:", [(k[:10], v) for k, v in ck.most_common(12)])
ipc = collections.Counter(n['ip'] for n in N)
print("Top IPs by #UIDs:", ipc.most_common(10))
inc_ck = collections.Counter(n['coldkey'] for n in inc)
print("Rewarded coldkeys:", [(k[:10], v, ck[k]) for k, v in inc_ck.items()])
ages = sorted((head - n['block_at_registration'])/7200 for n in N)
print("UID age days: min %.2f p25 %.2f median %.2f p75 %.2f max %.1f" % (ages[0], ages[64], ages[128], ages[192], ages[-1]))
print("registered in last 1/3/7 days:", sum(a<1 for a in ages), sum(a<3 for a in ages), sum(a<7 for a in ages))
