"""Legitimate SN55 solver.

Builds a submission of real, reference-matching CRISPR guide designs around the HBB locus,
balanced across the (mutation x Cas x strand) grid so the validator's coverage term is high,
with distinct guides and one physical cut site per row. No metric-gaming: guides are taken
from the genome and are valid by construction (0 mismatches), PAMs are real, and off-target
uniqueness is whatever the sequence gives.

Scoring target (subnet-niome @0c03fef): final = TWS * CF * DFF
  TWS = sum over valid rows of (0.625*gc_score + 0.375*dist_score) * offtarget_factor * mut_weight
  DFF = geomean(mutation, cas, strand, joint coverage entropy ratios; kmer diversity; distinct-guide)
  CF  = cross-validation consistency (seed-dependent, ~constant ~0.09 in practice; measured, not tuned)
"""
from Bio.Seq import Seq

COMP = str.maketrans("ACGT", "TGCA")
def revc(s): return s.translate(COMP)[::-1]
def gc_content(s): return sum(c in "GC" for c in s) / max(1, len(s))
def gc_score(g): return max(0.0, 1.0 - abs(gc_content(g) - 0.5) * 2)

_GENOME = {}
def load_genome(path):
    if path not in _GENOME:
        from Bio import SeqIO
        _GENOME[path] = str(next(SeqIO.parse(path, "fasta")).seq)
    return _GENOME[path]


def pam_ok(seq, start, L, cas, strand):
    """Mirror of stage1.check_pam (no out-of-bounds here; caller guarantees range)."""
    if cas == "Cas9":
        if strand == "+":
            pam = seq[start+L:start+L+3];  return len(pam) == 3 and pam[1:] == "GG"
        pam = revc(seq[start-3:start]);    return len(pam) == 3 and pam[1:] == "GG"
    if cas == "Cas12a":
        if strand == "+":
            pam = seq[start-4:start];      return len(pam) == 4 and pam[:3] == "TTT"
        pam = revc(seq[start+L:start+L+4]); return len(pam) == 4 and pam[:3] == "TTT"
    return False


def candidate_sites(seq, mpos, cas, strand, base_padding, proximity_gate, L=20, radius=None):
    """All reference-matched guides with a valid PAM near mpos for one (cas,strand).

    Returns list of (start, guide, gc_score). The guide is the exact protospacer read off
    the reference, so stage1 Hamming = 0 (always within max_mismatches)."""
    r = base_padding if proximity_gate else (radius or 2000)
    lo, hi = max(4, mpos - r), min(len(seq) - L - 4, mpos + r)
    out = []
    for start in range(lo, hi + 1):
        if not pam_ok(seq, start, L, cas, strand):
            continue
        proto = seq[start:start+L]
        if set(proto) - set("ACGT"):
            continue
        guide = proto if strand == "+" else revc(proto)
        if len(guide) != L:
            continue
        out.append((start, guide, gc_score(guide)))
    return out


def build_submission(seq, contract, reference, strategy="balanced", L=20):
    """Return a list of experiment dicts.

    strategy:
      naive   - fill rows for the single highest-weight mutation, one cas/strand (coverage trap)
      perfmut - greedy: best weighted rows ignoring coverage balance
      balanced- round-robin across the full (mutation x cas x strand) grid (the real solver)
    """
    rules = contract["rules"]
    muts = contract["active_mutations"]
    cas_systems = rules.get("cas_systems", ["Cas9", "Cas12a"])
    max_exp = rules.get("max_experiments", 250)
    bp = rules["base_padding"]; pg = rules.get("proximity_gate", False)
    mmap = reference["mutation_map"]; ct = contract.get("cell_type")

    # Enumerate candidate sites per (mutation, cas, strand) cell.
    cells = {}
    for m in muts:
        mpos = mmap[m]
        for cas in cas_systems:
            for strand in ("+", "-"):
                sites = candidate_sites(seq, mpos, cas, strand, bp, pg, L)
                # best gc first, then nearest to the target
                sites.sort(key=lambda s: (-s[2], abs(s[0] - mpos)))
                cells[(m, cas, strand)] = {"mpos": mpos, "sites": sites}

    used_guides = set()
    used_keys = set()   # (cas, start, strand, guide) - validator dedup key omits mutation
    rows = []

    def take(cell_key, budget):
        m, cas, strand = cell_key
        info = cells[cell_key]; mpos = info["mpos"]; added = 0
        for start, guide, gsc in info["sites"]:
            if added >= budget:
                break
            key = (cas, start, strand, guide)
            if guide in used_guides or key in used_keys:
                continue
            used_guides.add(guide); used_keys.add(key)
            rows.append({"guideRNA": guide, "target_alignment_start": start,
                         "target_alignment_end": start + L, "strand": strand,
                         "mutation": m, "cas_system": cas, "cell_type": ct})
            added += 1
        return added

    if strategy == "naive":
        m = max(muts, key=lambda x: contract["mutation_weights"].get(x, 1.0))
        take((m, cas_systems[0], "+"), max_exp)
    elif strategy == "perfmut":
        order = sorted(cells, key=lambda k: -contract["mutation_weights"].get(k[0], 1.0))
        for ck in order:
            if len(rows) >= max_exp: break
            take(ck, max_exp - len(rows))
    else:  # balanced round-robin across all grid cells
        order = list(cells)
        i = 0
        while len(rows) < max_exp:
            progressed = False
            for ck in order:
                if len(rows) >= max_exp: break
                if take(ck, 1): progressed = True
            if not progressed:
                break
            i += 1

    for idx, r in enumerate(rows):
        r_full = {"experiment_id": f"exp_{idx:05d}"}; r_full.update(r); rows[idx] = r_full
    return rows
