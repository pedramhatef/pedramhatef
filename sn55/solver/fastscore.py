"""Fast re-implementation of the deterministic (seed-independent) part of the SN55 score:
stage1 validity, stage2 structural/weighted score (TWS), and stage5 distribution fidelity (DFF).

It mirrors subnet-niome @0c03fef exactly and is used only to drive the optimizer; every reported
score is re-checked with the UNMODIFIED official scorer (harness/official.py). CF is seed-dependent
and is NOT modelled here."""
import math
from collections import Counter, defaultdict
from solver.core import revc, gc_content

REGION_ENERGY_OFFSETS = {"5UTR_or_upstream":0.05,"exon1":0.03,"exon2":0.03,"exon3":0.03,
                         "intron1":-0.03,"intron2":-0.03,"3UTR":0.02}

def _hamming(a,b): return sum(x!=y for x,y in zip(a,b))

def build_kmer_index(seq, k=12):
    idx = defaultdict(int)
    for i in range(len(seq)-k):
        idx[seq[i:i+k]] += 1
    return idx

def offtarget_factor(guide, cas, kidx):
    seed = guide[-12:] if cas=="Cas9" else guide[:12]
    hits = kidx.get(seed, 0)
    return 1.0 if hits==0 else 0.7 if hits<=5 else 0.4 if hits<=20 else 0.1

def _entropy(counts):
    tot=sum(counts)
    if tot<=0: return 0.0
    h=0.0
    for c in counts:
        if c>0:
            p=c/tot; h-=p*math.log2(p)
    return h

def _cov_ratio(counter, support):
    support=list(support); n=len(support)
    if n<=1: return 1.0
    counts=[counter.get(c,0) for c in support]
    hmax=math.log2(n)
    return _entropy(counts)/hmax if hmax>0 else 1.0

def _kmer_div(guides,k=12):
    pool=Counter(); tot=0
    for g in guides:
        for i in range(len(g)-k+1):
            pool[g[i:i+k]]+=1; tot+=1
    if tot<=1: return 0.0
    hmax=math.log2(tot)
    return _entropy(list(pool.values()))/hmax if hmax>0 else 0.0

def score_fast(seq, submission, contract, reference, kidx=None):
    """Return dict with n_valid, TWS, DFF (CF omitted). Matches stage1/2/5 logic."""
    rules=contract["rules"]; mmap=reference["mutation_map"]
    active=contract["active_mutations"]; mw=contract.get("mutation_weights",{})
    ct=contract.get("cell_type"); bp=rules["base_padding"]; maxmm=rules["max_mismatches"]
    pg=rules.get("proximity_gate",False); max_exp=rules.get("max_experiments")
    cas_systems=rules.get("cas_systems",["Cas9","Cas12a"])
    if kidx is None:
        gr=reference["gene_region"]; flank=50000
        a=max(0,gr["start"]-flank); b=min(len(seq),gr["end"]+flank)
        kidx=build_kmer_index(seq[a:b],12)
    # truncate_submission: cap rows + unique experiment_id
    kept=[]; seen_ids=set()
    for e in submission:
        if max_exp is not None and len(kept)>=max_exp: break
        eid=e.get("experiment_id")
        if not isinstance(eid,str) or not eid.strip() or eid in seen_ids: continue
        seen_ids.add(eid); kept.append(e)
    valid=[]; seen_keys=set()
    for e in kept:
        g=e["guideRNA"]; cas=e["cas_system"]; start=e["target_alignment_start"]; m=e["mutation"]; strand=e.get("strand")
        if m not in active: continue
        if ct is not None and e.get("cell_type")!=ct: continue
        if len(g) not in (20,23): continue
        if e.get("target_alignment_end")!=start+len(g): continue
        if start<0 or start+len(g)>=len(seq): continue
        # PAM
        from solver.core import pam_ok
        if start<4 or start+len(g)+4>len(seq): continue
        if not pam_ok(seq,start,len(g),cas,strand): continue
        target=seq[start:start+len(g)]
        mm=_hamming(g,target) if strand=="+" else (_hamming(revc(g),target) if strand=="-" else 999)
        if mm>maxmm: continue
        if pg and abs(start-mmap[m])>bp: continue
        key=(cas,start,strand,g)
        if key in seen_keys: continue
        seen_keys.add(key)
        gc=gc_content(g); gsc=max(0.0,1.0-abs(gc-0.5)*2)
        dist=abs(start-mmap[m]); dsc=math.exp(-dist/bp)
        struct=(0.625*gsc+0.375*dsc)*offtarget_factor(g,cas,kidx)
        weighted=struct*mw.get(m,1.0)
        valid.append((e,weighted))
    TWS=sum(w for _,w in valid)
    ve=[e for e,_ in valid]
    if not ve: return {"n_valid":0,"TWS":0.0,"DFF":0.0,"final_noCF":0.0}
    mc=Counter(e["mutation"] for e in ve); cc=Counter(e["cas_system"] for e in ve)
    sc=Counter(e.get("strand") for e in ve)
    jc=Counter((e["mutation"],e["cas_system"],e.get("strand")) for e in ve)
    jsup=[(m,c,s) for m in active for c in cas_systems for s in ("+","-")]
    guides=[e["guideRNA"] for e in ve]
    dff=1.0
    for r in [_cov_ratio(mc,active),_cov_ratio(cc,cas_systems),_cov_ratio(sc,["+","-"]),
              _cov_ratio(jc,jsup),_kmer_div(guides,12),
              (len(set(guides))/len(guides) if guides else 0.0)]:
        dff*=max(r,1e-9)
    dff=dff**(1/6)
    return {"n_valid":len(ve),"TWS":TWS,"DFF":dff,"final_noCF":TWS*dff}
