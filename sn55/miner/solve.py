"""Standalone SN55 submission builder: contract + reference + genome -> submission JSON.

Pure CPU, no GPU, no network beyond fetching the task inputs. Deterministic. Builds a fully
valid, coverage-balanced, weight-tilted set of reference-matched HBB guide designs. Import this
from the miner's process_task, or run offline:  python -m miner.solve contract.json hbb_ref.json chr11.fa out.json
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from solver import core, fastscore, optimize


def solve(contract: dict, reference: dict, chr11_path: str) -> list:
    seq = core.load_genome(chr11_path)
    gr = reference["gene_region"]; flank = 50000
    kidx = fastscore.build_kmer_index(seq[max(0, gr["start"] - flank):gr["end"] + flank], 12)
    tilt, fr, sub = optimize.optimize(seq, contract, reference, kidx=kidx)
    return sub


if __name__ == "__main__":
    contract = json.load(open(sys.argv[1])); reference = json.load(open(sys.argv[2]))
    sub = solve(contract, reference, sys.argv[3])
    json.dump(sub, open(sys.argv[4], "w"))
    print(f"wrote {len(sub)} rows to {sys.argv[4]}")
