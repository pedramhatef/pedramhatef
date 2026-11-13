#!/usr/bin/env bash
# setup_minerbox.sh — hardened setup of an SN89 (InfiniteQuant) LF miner box.
#
# Run as an UNPRIVILEGED user on a dedicated Linux x86_64 VM (glibc >= 2.34,
# e.g. Ubuntu 22.04+). The box holds HOTKEYS ONLY: never put your coldkey,
# exchange API keys, or your perp bots on it.
#
# What it does (idempotent):
#   1. checks platform (x86_64, glibc >= 2.34, Python 3.10 available, NTP sync)
#   2. clones the subnet repo and pins it to a reviewed commit, then removes the
#      git remote so nothing can auto-update it (do NOT use the repo's run.sh,
#      which pulls and pip-installs unsigned upstream code every 30 minutes)
#   3. builds a Python 3.10 venv, checks the bittensor wheel hash, installs the
#      pinned requirements (incl. the vendored timelock wheels) from the repo root
#   4. runs the repo's timelock format check (must report the 245-byte format)
#   5. creates ~/.sn89.env (mode 600) with a random local intake token
#
# Usage:  bash setup_minerbox.sh            # pin = commit reviewed 2026-11-12
#         SN89_PIN=<sha> bash setup_minerbox.sh   # after you've reviewed a newer diff
set -euo pipefail

PIN="${SN89_PIN:-2e48ae814ad1ee811c57ebc126f0d4a0f9445beb}"
DIR="${SN89_DIR:-$HOME/sn89}"
REPO_URL="https://github.com/DeltaCompute24/InfiniteQuant-Subnet"
BT_VERSION="10.3.0"
BT_WHEEL_SHA256="28efad138130227f19ccee6369264495102f6d087b78fe3db6c19a234da0d2ef"
ENV_FILE="$HOME/.sn89.env"

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as an unprivileged user, not root"

# ---------------------------------------------------------------- 1. platform
say "Checking platform"
[ "$(uname -m)" = "x86_64" ] || die "x86_64 required: the vendored timelock wheel is cp310-manylinux_2_34_x86_64"
glibc="$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$' || true)"
[ -n "$glibc" ] || die "could not read glibc version"
if [ "$(printf '%s\n2.34\n' "$glibc" | sort -V | head -1)" != "2.34" ]; then
  die "glibc $glibc < 2.34: the vendored timelock wheel will not install"
fi
echo "  x86_64, glibc $glibc"

PY=""
if command -v python3.10 >/dev/null 2>&1; then
  PY="python3.10"
elif command -v uv >/dev/null 2>&1; then
  say "python3.10 not found; provisioning it with uv"
  uv python install 3.10
  PY="$(uv python find 3.10)"
else
  die "need Python 3.10: 'sudo apt-get install -y python3.10 python3.10-venv' (Ubuntu 22.04) or install uv"
fi
"$PY" -c 'import sys; assert sys.version_info[:2] == (3, 10), sys.version' \
  || die "$PY is not Python 3.10"
echo "  python: $("$PY" --version)"

if command -v timedatectl >/dev/null 2>&1; then
  if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)" != "yes" ]; then
    warn "clock not NTP-synchronized. Reveals must land within +/-10 min of the drand round: 'sudo apt-get install -y chrony'"
  else
    echo "  NTP: synchronized"
  fi
fi

# ------------------------------------------------------------ 2. clone + pin
say "Cloning and pinning to $PIN"
if [ ! -d "$DIR/.git" ]; then
  git clone --quiet "$REPO_URL" "$DIR"
fi
cd "$DIR"
if ! git cat-file -e "$PIN^{commit}" 2>/dev/null; then
  git fetch --quiet "$REPO_URL" "$PIN" || git fetch --quiet "$REPO_URL" master
fi
git checkout --quiet --detach "$PIN"
git remote remove origin 2>/dev/null || true
[ "$(git rev-parse HEAD)" = "$PIN" ] || die "HEAD is not the pinned commit"
[ -z "$(git status --porcelain)" ] || warn "working tree has local changes"
echo "  HEAD $(git log -1 --format='%h %ci %s')"

# --------------------------------------------------------------- 3. venv
say "Building Python 3.10 venv"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --quiet --upgrade pip

say "Verifying bittensor==$BT_VERSION wheel hash"
tmpd="$(mktemp -d)"
trap 'rm -rf "$tmpd"' EXIT
python -m pip download --quiet --no-deps -d "$tmpd" "bittensor==$BT_VERSION"
whl="$(ls "$tmpd"/bittensor-"$BT_VERSION"-*.whl)"
got="$(sha256sum "$whl" | cut -d' ' -f1)"
[ "$got" = "$BT_WHEEL_SHA256" ] || die "bittensor wheel hash mismatch: $got"
echo "  sha256 ok"

say "Installing pinned requirements (from the repo root: the timelock wheels are vendored by relative path)"
python -m pip install --quiet -r requirements.txt
python -m pip freeze > "$HOME/sn89-freeze-${PIN:0:7}.txt"
echo "  frozen to ~/sn89-freeze-${PIN:0:7}.txt"

# ------------------------------------------------------- 4. timelock format
say "Checking timelock format (must be the 245-byte consensus W_time)"
python tools/check_timelock.py || die "timelock check failed: every call would void"

# ------------------------------------------------------------- 5. env file
say "Preparing $ENV_FILE"
umask 077
if [ ! -f "$ENV_FILE" ]; then
  printf 'SN89_FEED_TOKEN=\nSN89_INTAKE_TOKEN=%s\n' "$(python -c 'import secrets;print(secrets.token_hex(24))')" > "$ENV_FILE"
  echo "  created; paste the relay token from @Iqsignals2026_bot into SN89_FEED_TOKEN"
else
  echo "  exists; left unchanged"
fi
chmod 600 "$ENV_FILE"
for v in SN89_R2_ACCESS_KEY_ID SN89_R2_SECRET_ACCESS_KEY SN89_BLOB_DIR SN89_OWNER_WEBHOOK; do
  if grep -q "^$v=" "$ENV_FILE" || [ -n "${!v:-}" ]; then
    warn "$v is set. Self-hosted blob storage is never fetched by the mainnet validator: every call would become a forfeit LOSS. Remove it."
  fi
done

say "Done. Next:"
cat <<EOF
  1. Copy ONLY your hotkey files + coldkeypub.txt to ~/.bittensor/wallets/<wallet>/ on this box.
  2. Put the relay token in $ENV_FILE (SN89_FEED_TOKEN=...).
  3. Start the local intake (localhost only, bearer token):
       cd $DIR && . .venv/bin/activate && set -a && . $ENV_FILE && set +a && \\
       python neurons/miner.py --wallet.name <wallet> --wallet.hotkey <hotkey> serve --host 127.0.0.1 --port 8089
  4. Drive it from Fable with sn89-kit/miner/fable_intake.py (enforces your conviction gate + limits, logs every receipt).
  Never run: run.sh, follow, limit, positions, watch-positions, submit-closers, register-x.
EOF
