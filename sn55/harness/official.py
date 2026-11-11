"""Run the UNMODIFIED official SN55 scorer (genomesio/subnet-niome) on one submission.

The official stages read/write fixed relative paths under ./data, so each call runs inside a
dedicated work directory. The package is imported from a pinned checkout; we refuse to run if
the checkout is not at the pinned commit.
"""
import json, os, subprocess, sys, time, resource, contextlib

PINNED_SHA = "0c03fef2476b56285a202c76cc0d698b62b86b74"
REPO = os.environ.get("NIOME_REPO", os.path.expanduser("~/subnet-niome"))
CHR11 = os.environ.get("CHR11_FA", os.path.join(os.path.dirname(__file__), "..", "data", "chr11.fa"))


def _check_repo():
    sha = subprocess.check_output(["git", "-C", REPO, "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", REPO, "status", "--porcelain"], text=True).strip()
    if sha != PINNED_SHA or dirty:
        raise RuntimeError(f"official repo at {sha} dirty={bool(dirty)}; expected clean {PINNED_SHA}")


_checked = False


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd(); os.chdir(path)
    try: yield
    finally: os.chdir(old)


def official_score(submission, contract, reference, cell_types, seeds, workdir):
    global _checked
    if not _checked:
        _check_repo(); _checked = True
        if REPO not in sys.path: sys.path.insert(0, REPO)
    os.environ.setdefault("BT_NO_PARSE_CLI_ARGS", "true")
    from niome_subnet.genomics.validation import benchmark_submission
    d = os.path.join(workdir, "data"); os.makedirs(d, exist_ok=True)
    link = os.path.join(d, "chr11.fa")
    want = os.path.abspath(CHR11)
    if os.path.islink(link) or os.path.exists(link):
        if not (os.path.islink(link) and os.readlink(link) == want):
            os.remove(link); os.symlink(want, link)
    else:
        os.symlink(want, link)
    json.dump(contract, open(os.path.join(d, "contract.json"), "w"))
    json.dump(reference, open(os.path.join(d, "hbb_reference.json"), "w"))
    json.dump(submission, open(os.path.join(d, "submission.json"), "w"))
    t0 = time.time()
    with _cwd(workdir):
        ms = benchmark_submission(cell_types, 0, list(seeds))
    return {"final_score": ms.final_score, "breakdown": ms.breakdown, "seconds": time.time() - t0,
            "maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
