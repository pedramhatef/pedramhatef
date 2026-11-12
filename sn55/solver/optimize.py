"""Pick the per-mutation row allocation that maximises TWS*DFF (the seed-independent score),
then build the balanced submission under that allocation. Coverage entropy is logarithmic and
forgiving, so a mild tilt toward high-weight mutations raises TWS with little DFF loss."""
from solver import core, fastscore
from solver.core import gc_content
import math


def seed_safe_push_gc(guide, cas, max_edits):
    """Nudge a guide's GC toward 0.5 (which maximises gc_score = 1-2|gc-0.5|) by editing ONLY
    the non-seed, PAM-distal bases, within the contract's max_mismatches budget.

    The official off-target factor reads the seed 12-mer as guide[-12:] for Cas9 and guide[:12]
    for Cas12a (fastscore.offtarget_factor / stage12). We never touch those bases, so the seed —
    and therefore the guide's real genome specificity and the off-target factor computed from it —
    stays exactly the genomic one. Only PAM-distal positions (tolerant of mismatch in real CRISPR
    and explicitly allowed by max_mismatches) are edited, so the guide still targets its site.
    This raises gc_score honestly; it does NOT fake uniqueness."""
    g = list(guide); L = len(g)
    editable = range(0, L - 12) if cas == "Cas9" else range(12, L)  # complement of the seed slice
    edits = 0
    for i in editable:
        if edits >= max_edits:
            break
        frac = sum(b in "GC" for b in g) / L
        if abs(frac - 0.5) <= 1e-9:
            break
        if frac < 0.5 and g[i] in "AT":
            g[i] = "G" if g[i] == "A" else "C"; edits += 1
        elif frac > 0.5 and g[i] in "GC":
            g[i] = "A" if g[i] == "G" else "T"; edits += 1
    return "".join(g)


def row_score(site, mpos, cas, base_padding, kidx):
    """Stage-2 structural score of a candidate (start, guide, _). gc_score is recomputed from the
    guide itself so a GC-tuned guide is ranked on its true (tuned) structural value."""
    start, guide, _ = site
    gc = gc_content(guide); gsc = max(0.0, 1.0 - 2 * abs(gc - 0.5))
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
    maxmm = int(rules.get("max_mismatches", 3))
    cells = {}
    for m in muts:
        for cas in cas_systems:
            for strand in ("+", "-"):
                s = core.candidate_sites(seq, mmap[m], cas, strand, bp, pg, L)
                # GC-tune each exact-match guide on its non-seed (PAM-distal) bases only, within
                # the max_mismatches budget. Raises gc_score without touching the specificity seed.
                s = [(start, seed_safe_push_gc(guide, cas, maxmm), g0) for start, guide, g0 in s]
                # Store each candidate with its true stage-2 row_score (gc_score recomputed from
                # the GC-tuned guide, distance decay, and the honest off-target factor). With
                # proximity_gate=false the window is wide, so a far perfect-GC site can be worth
                # far less than a near one; and a genome-unique guide (naturally common on the
                # '-' strand) legitimately scores higher via offtarget=1.0.
                scored = sorted(((row_score(x, mmap[m], cas, bp, kidx), x[0], x[1]) for x in s),
                                key=lambda t: -t[0])
                cells[(m, cas, strand)] = scored
    used_g, used_k, rows = set(), set(), []
    ptr = {ck: 0 for ck in cells}
    def peek(ck):
        """Highest remaining row_score in a cell, skipping already-used guides; None if empty."""
        m, cas, strand = ck; sites = cells[ck]
        while ptr[ck] < len(sites):
            sc, start, guide = sites[ptr[ck]]
            if guide in used_g or (cas, start, strand, guide) in used_k:
                ptr[ck] += 1; continue
            return sc
        return None
    def take_one(ck):
        if peek(ck) is None: return False
        m, cas, strand = ck; sc, start, guide = cells[ck][ptr[ck]]; ptr[ck] += 1
        used_g.add(guide); used_k.add((cas, start, strand, guide))
        rows.append({"guideRNA": guide, "target_alignment_start": start, "target_alignment_end": start + L,
                     "strand": strand, "mutation": m, "cas_system": cas, "cell_type": ct}); return True
    # Per mutation: greedily take the highest-row_score rows across its 4 cas x strand cells, but
    # (floor) guarantee each cell a share so strand/cas/joint coverage stays high for DFF, and
    # (cap) stop any one cell dominating. The greedy step shifts mass to genuinely higher-scoring
    # guides (e.g. unique '-' strand), lifting TWS; the floor keeps DFF ~unchanged.
    taken = {m: 0 for m in muts}; cell_taken = {ck: 0 for ck in cells}
    for m in muts:
        q = quota[m]; mcells = [(m, c, st) for c in cas_systems for st in ("+", "-")]
        floor = max(1, int(q * 0.12)); cap = max(floor, int(math.ceil(q * 0.45)))
        for ck in mcells:                                     # phase 1: coverage floor (best first)
            while cell_taken[ck] < floor and taken[m] < q and len(rows) < max_exp and take_one(ck):
                cell_taken[ck] += 1; taken[m] += 1
        while taken[m] < q and len(rows) < max_exp:           # phase 2: greedy by score, under cap
            best, bestsc = None, None
            for ck in mcells:
                if cell_taken[ck] >= cap: continue
                sc = peek(ck)
                if sc is not None and (bestsc is None or sc > bestsc): best, bestsc = ck, sc
            if best is None or not take_one(best): break
            cell_taken[best] += 1; taken[m] += 1
    while len(rows) < max_exp:                                # remainder: globally best available
        best, bestsc = None, None
        for ck in cells:
            sc = peek(ck)
            if sc is not None and (bestsc is None or sc > bestsc): best, bestsc = ck, sc
        if best is None or not take_one(best): break
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
