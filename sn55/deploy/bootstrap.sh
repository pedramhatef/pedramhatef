#!/usr/bin/env bash
# Isolated SN55 miner setup. Creates a self-contained workspace under ~/sn55 with its own venv,
# the pinned validator repo, the solver/miner package, and the reference genome. Touches NOTHING
# outside ~/sn55 and ~/niome-data. Does NOT install globally, does NOT touch your pm2/tmux/observers,
# does NOT create a wallet or spend TAO. Idempotent: safe to re-run.
set -euo pipefail
PIN=0c03fef2476b56285a202c76cc0d698b62b86b74
ROOT="$HOME/sn55"; DATA="$HOME/niome-data"
PKG_REMOTE="${PKG_REMOTE:-https://github.com/pedramhatef/pedramhatef.git}"
PKG_BRANCH="${PKG_BRANCH:-claude/upbeat-turing-wmip3o}"
log(){ echo "[sn55-bootstrap] $*"; }

mkdir -p "$ROOT" "$DATA"
cd "$ROOT"

# 1. pinned validator repo (for BaseMinerNeuron / protocol / bittensor wiring)
if [ ! -d subnet-niome ]; then
  log "cloning subnet-niome @ $PIN"
  git clone -q https://github.com/genomesio/subnet-niome.git
fi
git -C subnet-niome fetch -q origin && git -C subnet-niome checkout -q "$PIN"
test "$(git -C subnet-niome rev-parse HEAD)" = "$PIN" || { echo "PIN MISMATCH"; exit 1; }

# 2. our solver/miner package (sn55/ subdir of your repo) -> ~/sn55/pkg
if [ ! -d pkg/.git ]; then
  log "cloning solver package from $PKG_REMOTE ($PKG_BRANCH)"
  git clone -q --branch "$PKG_BRANCH" "$PKG_REMOTE" pkg_full
  ln -sfn "$ROOT/pkg_full/sn55" "$ROOT/pkg"
else
  git -C pkg_full pull -q || true
fi

# 3. isolated venv (never touches system python or your other envs)
if [ ! -d venv ]; then
  log "creating isolated venv (python3.12)"
  python3.12 -m venv venv
fi
./venv/bin/pip -q install --upgrade pip
# The upstream package __init__ transitively imports the validator stages, so the miner needs the
# full dependency set (still ~500MB). Pins match subnet-niome pyproject/uv.lock.
log "installing deps (matches subnet-niome; ~500MB)"
./venv/bin/pip -q install \
  "bittensor==11.0.1" "bio>=1.8.3" "httpx>=0.27.0" "python-dotenv>=1.0.0" \
  "fastapi>=0.115.0" "uvicorn>=0.30.0" "boto3>=1.43.45" "google-auth[requests]>=2.0.0" \
  "scikit-learn==1.9.0" "numpy==2.5.1" "pandas==3.0.5" "biopython==1.87" "scipy==1.18.0" \
  "wandb>=0.28.0" "requests>=2.31.0"

# 4. reference genome, pinned to Ensembl r116 (validator builds against exactly this)
if [ ! -f "$DATA/chr11.fa" ]; then
  log "downloading Ensembl r116 chr11 (~40MB gz -> 135MB)"
  wget -q -O "$DATA/chr11.fa.gz" \
    https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.chromosome.11.fa.gz
  gunzip -f "$DATA/chr11.fa.gz"
fi
SUM=$(sum "$DATA/chr11.fa.gz" 2>/dev/null || true)
log "genome ready at $DATA/chr11.fa"

# 5. offline self-check: build a submission from a synthetic contract (no chain, no spend)
log "offline self-check (synthetic contract)..."
cd "$ROOT/pkg"
CHR11_FA="$DATA/chr11.fa" "$ROOT/venv/bin/python" - <<'PY'
import sys; sys.path.insert(0,".")
from harness import synth
from miner.solve import solve
import os
sub=solve(synth.BASE_CONTRACT, synth.reference(), os.environ["CHR11_FA"])
assert len(sub)>0 and all("guideRNA" in r for r in sub), "self-check failed"
print(f"[sn55-bootstrap] self-check OK: built {len(sub)} valid rows offline")
PY
log "DONE. Workspace ready under $ROOT (isolated). No wallet created, no TAO spent."
echo
echo "Next: create a NEW wallet, fund it, register, then start under pm2 (see RUNBOOK.md)."
