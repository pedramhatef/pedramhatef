import json,collections,statistics as st
rows=[json.loads(l) for l in open('evidence/history_30d.jsonl') if l.strip()]
rows=[r for r in rows if 'error' not in r and r['incentive']]
rows.sort(key=lambda r:r['block'])
N=len(rows)
def miners_ck(r): return {r['coldkey'][str(u)] for u,v in r['incentive'].items() if int(u)!=119 and v>0}
def miners_hk(r): return {r['hotkey'][str(u)] for u,v in r['incentive'].items() if int(u)!=119 and v>0}
def top4_ck(r):
    d={u:v for u,v in r['incentive'].items() if int(u)!=119 and v>0}
    top=sorted(d,key=d.get,reverse=True)[:4]
    return {r['coldkey'][u] for u in top}
# cumulative set of operators seen; count first-appearances over time (after warmup)
seen=set(); newcomers=[]
for i,r in enumerate(rows):
    cks=miners_ck(r)
    if i>=20:  # warmup
        new=cks-seen
        if new: newcomers.append((r['block'],len(new)))
    seen|=cks
print("samples",N,"span_days",round((rows[-1]['block']-rows[0]['block'])/7200,1))
print("distinct operator coldkeys ever in top10:",len(seen))
print("distinct hotkeys ever in top10:",len(set().union(*[miners_hk(r) for r in rows])))
# presence of each coldkey
pres=collections.Counter()
for r in rows:
    for c in miners_ck(r): pres[c]+=1
print("operator presence (coldkey -> % of samples):",[(c[:8],round(n/N*100)) for c,n in pres.most_common(12)])
# stable core: operators present > 40% of samples
core=[c for c,n in pres.items() if n/N>0.4]
print(f"stable-core operators (>40% present): {len(core)} ->",[c[:8] for c in core])
# top4 stability
t4=collections.Counter()
for r in rows:
    for c in top4_ck(r): t4[c]+=1
print("TOP-4 operator presence:",[(c[:8],round(n/N*100)) for c,n in t4.most_common(8)])
# newcomer rate
print(f"samples (post-warmup) where a never-before-seen operator appeared in top10: {len(newcomers)}/{N-20}")
# how concentrated: in the latest sample, how many distinct operators hold the 10 slots
print("latest sample: distinct operators holding the 10 miner slots:",len(miners_ck(rows[-1])))
print("latest coldkeys:",[c[:8] for c in miners_ck(rows[-1])])
