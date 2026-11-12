"""Pick the per-mutation row allocation that maximises TWS*DFF (the seed-independent score),
then build the balanced submission under that allocation. Coverage entropy is logarithmic and
forgiving, so a mild tilt toward high-weight mutations raises TWS with little DFF loss."""
from solver import core, fastscore
import math


def row_score(site, mpos, cas, base_padding, kidx):
    """Stage-2 structural score of a candidate (start, guide, gc_score), mutation-weight excluded."""
    start, guide, gsc = site
    dsc = math.exp(-abs(start - mpos) / base_padding)
    ot = fastscore.offtarget_factor(guide, cas, kidx) if kidx is not None else 1.0
    return (0.625 * gsc + 0.375 * dsc) * ot

def build_tilted(seq, contract, reference, tilt, L=20, kidx=None):
    """tilt=0 -> equal rows per mutation; tilt=1 -> rows ~ proportional to mutation_weight."""
    import math
    rules = contract["rules"]; muts = contract["active_mutations"]
    cas_systems = rules.get("cas_systems", ["Cas9", "Cas12a"]); max_exp = rules.get("max_experiments", 250)
    mw = contract.get("mutation_weights", {})
    w = {m: mw.get(m, 1.0) ** tilt for m in muts}; sw = sum(w.values())
    quota = {m: max(1, round(max_exp * w[m] / sw)) for m in muts}
    bp = rules["base_padding"]; pg = rules.get("proximity_gate", False); mmap = reference["mutation_map"]; ct = contract.get("cell_type")
    cells = {}
    for m in muts:
        for cas in cas_systems:
            for strand in ("+", "-"):
                s = core.candidate_sites(seq, mmap[m], cas, strand, bp, pg, L)
                # Rank by the row's actual stage-2 structural score, not GC alone. With
                # proximity_gate=false the candidate window is wide and dist_score decays as
                # exp(-d/base_padding), so a far perfect-GC site can be worth far less than a
                # near one. (The live contract exposed this: base_padding=400, gate off.)
                s.sort(key=lambda x: -row_score(x, mmap[m], cas, bp, kidx))
                cells[(m, cas, strand)] = s
    used_g, used_k, rows = set(), set(), []
    # Within each mutation, round-robin its cas x strand cells so coverage stays balanced.
    sub_cells = {m: [(m, c, st) for c in cas_systems for st in ("+", "-")] for m in muts}
    ptr = {ck: 0 for ck in cells}
    def take_one(ck):
        m, cas, strand = ck; sites = cells[ck]
        while ptr[ck] < len(sites):
            start, guide, gsc = sites[ptr[ck]]; ptr[ck] += 1
            key = (cas, start, strand, guide)
            if guide in used_g or key in used_k: continue
            used_g.add(guide); used_k.add(key)
            rows.append({"guideRNA": guide, "target_alignment_start": start, "target_alignment_end": start + L,
                         "strand": strand, "mutation": m, "cas_system": cas, "cell_type": ct}); return True
        return False
    taken = {m: 0 for m in muts}
    cellrot = {m: 0 for m in muts}   # rotate each mutation's 4 cas x strand cells evenly
    progressing = True
    while len(rows) < max_exp and progressing:
        progressing = False
        for m in muts:
            if taken[m] >= quota[m] or len(rows) >= max_exp: continue
            cells_m = sub_cells[m]
            for j in range(len(cells_m)):
                ck = cells_m[(cellrot[m] + j) % len(cells_m)]
                if take_one(ck):
                    cellrot[m] = (cellrot[m] + j + 1) % len(cells_m)
                    taken[m] += 1; progressing = True; break
    # Fill any remainder (quota rounding / exhausted cells) from whatever is left, keep balance.
    while len(rows) < max_exp:
        prog = False
        for ck in cells:
            if len(rows) >= max_exp: break
            if take_one(ck): prog = True
        if not prog: break
    for idx, r in enumerate(rows):
        rows[idx] = {"experiment_id": f"exp_{idx:05d}", **r}
    return rows

def optimize(seq, contract, reference, tilts=(0.0, 0.5, 1.0, 1.5, 2.0, 3.0), kidx=None):
    best = None
    for t in tilts:
        sub = build_tilted(seq, contract, reference, t, kidx=kidx)
        fr = fastscore.score_fast(seq, sub, contract, reference, kidx=kidx)
        if best is None or fr["final_noCF"] > best[1]["final_noCF"]:
            best = (t, fr, sub)
    return best
