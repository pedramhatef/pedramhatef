"""Pre-round prediction for one of our submissions.

Two parts:
  * base  = TWS * DFF  — the seed-INDEPENDENT, fully controllable part of final_score. Computed
    anywhere via the fast scorer (solver.fastscore), which mirrors the official stage1/2/5.
  * CF distribution — the seed-DEPENDENT lottery term. The round_seed is revealed only after the
    round closes, so ahead of time we SAMPLE it: run the official stage3/4 over many seeds and
    report the CF distribution. final_score = base * CF, so this yields a final-score distribution
    and the probability of clearing a given leaderboard cutoff.

CF sampling needs the pinned official stages (set NIOME_REPO; true on the box). Off-box it is
skipped and only `base` is returned. This module never touches wallets or chain state.
"""
import os, sys, json, tempfile, statistics


def predict_base(submission, contract, reference, seq, kidx=None):
    from solver import fastscore, core
    # A genome sequence is a huge string; a path is a short, existing filename.
    if isinstance(seq, str) and len(seq) < 4096 and os.path.exists(seq):
        seq = core.load_genome(seq)
    fr = fastscore.score_fast(seq, submission, contract, reference, kidx=kidx)
    fr["base"] = fr["TWS"] * fr["DFF"]
    return fr


def sample_cf(submission, contract, reference, seeds, chr11_path, niome_repo=None, workdir=None):
    """Run the official stage12 once, then stage3/4 per seed -> list of CF values.
    Returns [] if the official stages cannot be imported (e.g. off the box)."""
    niome_repo = niome_repo or os.environ.get("NIOME_REPO", os.path.expanduser("~/sn55/subnet-niome"))
    if niome_repo and niome_repo not in sys.path:
        sys.path.insert(0, niome_repo)
    try:
        from niome_subnet.genomics.validation.stage12 import run_stage12
        from niome_subnet.genomics.validation.stage3 import run_stage3
        from niome_subnet.genomics.validation.stage4 import run_stage4
    except Exception:
        return []
    cleanup = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="sn55cf_")
    d = os.path.join(workdir, "data"); os.makedirs(d, exist_ok=True)
    link = os.path.join(d, "chr11.fa")
    if not (os.path.islink(link) or os.path.exists(link)):
        os.symlink(os.path.abspath(chr11_path), link)
    json.dump(contract, open(os.path.join(d, "contract.json"), "w"))
    json.dump(reference, open(os.path.join(d, "hbb_reference.json"), "w"))
    json.dump(submission, open(os.path.join(d, "submission.json"), "w"))
    cell_types = reference.get("_cell_types") or {contract.get("cell_type", "CD34+_HSPC"): {"accessibility": 0.9}}
    os.environ.setdefault("BT_NO_PARSE_CLI_ARGS", "true")
    cfs, old = [], os.getcwd()
    try:
        os.chdir(workdir)
        run_stage12(cell_types)
        for s in seeds:
            run_stage3(seed=s)
            cfs.append(run_stage4(seed=s)["consistency_factor"])
    finally:
        os.chdir(old)
        if cleanup:
            import shutil; shutil.rmtree(workdir, ignore_errors=True)
    return cfs


def _pct(xs, p):
    if not xs: return None
    xs = sorted(xs); k = (len(xs) - 1) * p / 100.0
    lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def predict(submission, contract, reference, chr11_path, kidx=None, cf_seeds=range(1, 17)):
    from solver import core
    seq = core.load_genome(chr11_path)
    base = predict_base(submission, contract, reference, seq, kidx=kidx)
    cfs = sample_cf(submission, contract, reference, list(cf_seeds), chr11_path)
    out = {"n_valid": base["n_valid"], "TWS": round(base["TWS"], 2), "DFF": round(base["DFF"], 4),
           "base": round(base["base"], 2), "n_cf_seeds": len(cfs)}
    if cfs:
        out["cf"] = {"mean": round(statistics.mean(cfs), 4), "std": round(statistics.pstdev(cfs), 4),
                     "p10": round(_pct(cfs, 10), 4), "p50": round(_pct(cfs, 50), 4),
                     "p90": round(_pct(cfs, 90), 4), "max": round(max(cfs), 4)}
        out["final"] = {"mean": round(base["base"] * out["cf"]["mean"], 2),
                        "p50": round(base["base"] * out["cf"]["p50"], 2),
                        "p90": round(base["base"] * out["cf"]["p90"], 2)}
        out["_cf_samples"] = [round(c, 4) for c in cfs]
    return out


def prob_clear(base, cf_samples, cutoff):
    """Empirical P(final >= cutoff) = P(CF >= cutoff/base) from the sampled CF distribution."""
    if not cf_samples or base <= 0: return None
    need = cutoff / base
    return sum(1 for c in cf_samples if c >= need) / len(cf_samples)


if __name__ == "__main__":
    # predict.py contract.json reference.json submission.json chr11.fa
    c = json.load(open(sys.argv[1])); r = json.load(open(sys.argv[2])); sub = json.load(open(sys.argv[3]))
    print(json.dumps(predict(sub, c, r, sys.argv[4]), indent=1))
