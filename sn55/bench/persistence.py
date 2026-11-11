import json,collections,statistics as st
rows=[json.loads(l) for l in open('evidence/history_30d.jsonl') if l.strip()]
rows=[r for r in rows if 'error' not in r]
rows.sort(key=lambda r:r['block'])
print("samples",len(rows),"blocks",rows[0]['block'],"->",rows[-1]['block'],"span_days",round((rows[-1]['block']-rows[0]['block'])/7200,1))
# occupancy: per sample, the set of rewarded miner UIDs (exclude owner 119)
def miners(r): return {int(u) for u,v in r['incentive'].items() if v>0 and int(u)!=119}
# rank1 by incentive each sample
def rank1(r):
    d={int(u):v for u,v in r['incentive'].items() if int(u)!=119}
    return max(d,key=d.get) if d else None
# coldkey of rank1
def ck_of(r,u): return r['coldkey'].get(str(u),'?')[:10]
import datetime
# how many distinct UIDs ever held a top-10 slot
ever=collections.Counter()
for r in rows:
    for u in miners(r): ever[u]+=1
print("distinct miner UIDs ever rewarded in 30d:",len(ever))
print("most persistent UIDs (samples present / %):",[(u,c,round(c/len(rows)*100)) for u,c in ever.most_common(14)])
# rank1 churn
r1=[rank1(r) for r in rows]
from itertools import groupby
runs=[(k,len(list(g))) for k,g in groupby(r1)]
print("rank1 UID sequence changes:",sum(1 for i in range(1,len(r1)) if r1[i]!=r1[i-1]),"times over",len(r1),"samples")
print("rank1 holders:",collections.Counter(r1).most_common(8))
# coldkey-level: distinct operators ever in top10
ck_ever=collections.Counter()
for r in rows:
    cks=set()
    for u in miners(r): cks.add(r['coldkey'].get(str(u),'?'))
    for c in cks: ck_ever[c]+=1
print("distinct operator coldkeys ever in top10 (30d):",len(ck_ever))
print("operator presence %:",[(c[:8],round(n/len(rows)*100)) for c,n in ck_ever.most_common(10)])
# registrations per day pressure
reglast=[r.get('n_reg_last_day') for r in rows if r.get('n_reg_last_day') is not None]
if reglast: print("UIDs registered in trailing 24h: mean %.0f min %d max %d"%(st.mean(reglast),min(reglast),max(reglast)))
# window persistence: Jaccard of miner set now vs 7/14/30d ago
def at_days_ago(days):
    tgt=rows[-1]['block']-days*7200
    return min(rows,key=lambda r:abs(r['block']-tgt))
now=miners(rows[-1])
for d in (1,7,14,30):
    past=miners(at_days_ago(d)); inter=now&past
    print(f" miner-set overlap now vs {d}d ago: {len(inter)}/{len(now)} kept; Jaccard {len(inter)/max(1,len(now|past)):.2f}")
