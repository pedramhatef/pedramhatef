"""SYNTHETIC task inputs for offline SN55 scoring.

The live contract.json / hbb_reference.json are served by the NIOME backend only via
presigned URLs inside a validator->miner synapse, so they are NOT public. Everything here is
reconstructed from the validator's read sites (stage12/3/4/5 at subnet-niome 0c03fef) and
labelled synthetic. Genomic coordinates are real GRCh38 (verified against Ensembl r116
chr11: HbS +strand base T, HbC C, c.1 'CAT'), stored 0-based so seq[pos] is the variant base.
"""
import json, random

# name -> (GRCh38 1-based position, region key used by REGION_ENERGY_OFFSETS)
HBB_VARIANTS = {
    "HbS_rs334":        (5227002, "exon1"),
    "HbC_rs33930165":   (5227003, "exon1"),
    "HbE_rs33950507":   (5226943, "exon1"),
    "IVS_I_1":          (5226929, "intron1"),
    "IVS_I_110":        (5226820, "intron1"),
    "Cd39_nonsense":    (5226774, "exon2"),
    "minus28_promoter": (5227099, "5UTR_or_upstream"),
}
GENE_REGION = (5225464 - 1, 5229395 - 1)


def reference():
    return {"gene_region": {"start": GENE_REGION[0], "end": GENE_REGION[1]},
            "mutation_map": {k: v[0] - 1 for k, v in HBB_VARIANTS.items()}}


# Baseline contract = the prior public reconstruction (mikyoch, Sept 2026) with max_experiments
# raised to 250 (storage.py comment: "at most max_experiments = 250 rows").
BASE_CONTRACT = {
    "seed": "0",  # ignored since 9d9347a: seeds are drawn by the validator at scoring time
    "active_mutations": ["HbS_rs334", "HbC_rs33930165", "IVS_I_110", "Cd39_nonsense"],
    "mutation_weights": {"HbS_rs334": 1.5, "HbC_rs33930165": 1.2, "IVS_I_110": 1.0, "Cd39_nonsense": 1.0},
    "mutation_regions": {"HbS_rs334": "exon1", "HbC_rs33930165": "exon1", "IVS_I_110": "intron1", "Cd39_nonsense": "exon2"},
    "cell_type": "CD34+_HSPC",
    "rules": {"max_mismatches": 3, "base_padding": 1200, "proximity_gate": True,
              "max_experiments": 250, "cas_systems": ["Cas9", "Cas12a"]},
}

CELL_TYPES = {"CD34+_HSPC": {"accessibility": 0.85}, "K562": {"accessibility": 1.0},
              "HEK293T": {"accessibility": 0.95}, "HUDEP-2": {"accessibility": 0.9},
              "low_access": {"accessibility": 0.5}}


def random_contract(rng: random.Random):
    """Sample a contract from a deliberately wide family for robustness testing."""
    names = list(HBB_VARIANTS)
    k = rng.choice([2, 3, 4, 4, 5, 6])
    act = rng.sample(names, k)
    return {
        "seed": "0",
        "active_mutations": act,
        "mutation_weights": {m: rng.choice([1.0, 1.0, 1.2, 1.5, 2.0, 0.8]) for m in act},
        "mutation_regions": {m: HBB_VARIANTS[m][1] for m in act},
        "cell_type": rng.choice(list(CELL_TYPES)),
        "rules": {"max_mismatches": rng.choice([1, 2, 3, 3, 4]),
                  "base_padding": rng.choice([100, 300, 600, 1200, 2000]),
                  "proximity_gate": rng.choice([True, True, False]),
                  "max_experiments": rng.choice([100, 200, 250, 250]),
                  "cas_systems": ["Cas9", "Cas12a"]},
    }


def contract_suite(n=12, seed=2026):
    rng = random.Random(seed)
    return [BASE_CONTRACT] + [random_contract(rng) for _ in range(n - 1)]


if __name__ == "__main__":
    print(json.dumps(reference(), indent=1))
    for c in contract_suite(4):
        print(json.dumps(c))
