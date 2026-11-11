"""Benchmark competing solver strategies with the UNMODIFIED official scorer across a contract
suite and held-out seeds. Pre-registered metrics: validity %, TWS, DFF, CF(mean/std over seeds),
final(median/p10/std), solver wall time, peak RSS. Strategies:
  reference_stub - the shipped miner process_task() is `pass` -> empty submission (control)
  simple_balanced- core.py balanced round-robin, exact-match guides (the obvious competent build)
  optimized      - tilt-swept weighted-balanced allocation (our solver)
"""
import sys, os, json, time, statistics as st, resource, random
sys.path.insert(0, os.path.expanduser("~/pedramhatef/sn55") if os.path.isdir(os.path.expanduser("~/pedramhatef/sn55")) else "/home/user/pedramhatef/sn55")
from harness import synth, official
from solver import core, fastscore, optimize

CHR11 = os.environ["CHR11_FA"]
seq = core.load_genome(CHR11)

def build(strategy, C, ref, kidx):
    t = time.time()
    if strategy == "reference_stub": sub = []
    elif strategy == "simple_balanced": sub = core.build_submission(seq, C, ref, "balanced")
    elif strategy == "optimized": sub = optimize.optimize(seq, C, ref, kidx=kidx)[2]
    return sub, time.time() - t

def run(n_contracts=8, seeds_per=8, seed0=4321):
    suite = synth.contract_suite(n_contracts); ref = synth.reference()
    rng = random.Random(seed0)
    results = {}
    for strat in ("reference_stub", "simple_balanced", "optimized"):
        rows = []
        for ci, C in enumerate(suite):
            gr = ref["gene_region"]; kidx = fastscore.build_kmer_index(seq[max(0, gr["start"]-50000):gr["end"]+50000], 12)
            sub, bt_ = build(strat, C, ref, kidx)
            seeds = [rng.randint(0, 1000000) for _ in range(seeds_per)]
            finals, cfs = [], []
            for s in seeds:
                r = official.official_score(sub, C, ref, synth.CELL_TYPES, [s], f"/tmp/wbench")
                finals.append(r["final_score"]); cfs.append(r["breakdown"]["consistency_factor"])
            b = r["breakdown"]
            rows.append({"contract": ci, "n_rows": len(sub), "n_valid": b["n_valid_experiments"],
                         "TWS": b["total_weighted_score"], "DFF": b["distribution_fidelity_score"],
                         "CF_mean": st.mean(cfs), "CF_std": st.pstdev(cfs),
                         "final_median": st.median(finals), "final_p10": sorted(finals)[max(0,len(finals)//10)],
                         "final_std": st.pstdev(finals), "build_s": bt_})
        results[strat] = rows
    results["_meta"] = {"maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                        "n_contracts": n_contracts, "seeds_per": seeds_per}
    return results

if __name__ == "__main__":
    res = run()
    json.dump(res, open("/home/user/pedramhatef/sn55/evidence/bench.json", "w"), indent=1)
    import statistics as st
    print(f"{'strategy':>16} {'rows':>5} {'valid%':>6} {'TWS':>7} {'DFF':>6} {'CFmean':>6} {'finalMed':>8} {'finalP10':>8} {'buildms':>7}")
    for strat in ("reference_stub","simple_balanced","optimized"):
        R=res[strat]
        def agg(k): return st.mean(r[k] for r in R)
        vr = st.mean((r["n_valid"]/r["n_rows"]*100 if r["n_rows"] else 0) for r in R)
        print(f"{strat:>16} {agg('n_rows'):>5.0f} {vr:>6.1f} {agg('TWS'):>7.1f} {agg('DFF'):>6.3f} {agg('CF_mean'):>6.3f} {agg('final_median'):>8.2f} {agg('final_p10'):>8.2f} {agg('build_s')*1000:>7.1f}")
    print("peak RSS MB", round(res["_meta"]["maxrss_mb"]))
