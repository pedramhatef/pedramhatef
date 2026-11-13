#!/usr/bin/env bash
# sn89_watch.sh — daily SN89 upstream rule watch. Review-only: it never changes
# your pinned checkout and never installs anything.
#
# SN89's owner ships consensus changes with as little as 44 minutes' notice
# (about 23 payout-relevant changes in the 60 days to 2026-11-12). Run this daily
# (cron) and read every flagged diff before you re-pin.
#
#   bash sn89_watch.sh                 # repo at ~/sn89, pinned at its HEAD
#   SN89_DIR=/path bash sn89_watch.sh
# Exit: 0 nothing new, 1 new commits (review), 2 MINER-BREAKING change (act now).
set -euo pipefail

DIR="${SN89_DIR:-$HOME/sn89}"
URL="https://github.com/DeltaCompute24/InfiniteQuant-Subnet"
cd "$DIR"
PIN="$(git rev-parse HEAD)"

git fetch --quiet "$URL" master
NEW="$(git rev-parse FETCH_HEAD)"
if [ "$PIN" = "$NEW" ]; then
  echo "No upstream changes since your pin ${PIN:0:7}."
  exit 0
fi

echo "== $(git rev-list --count "$PIN..$NEW") new upstream commit(s) since your pin ${PIN:0:7} =="
git log --format='%h %ad %s' --date=format:'%m-%d %H:%M' "$PIN..$NEW"
echo
echo "== files changed =="
git diff --stat "$PIN" "$NEW" | tail -40
echo

rc=1

# Miner-side consensus: if these drift, a pinned miner voids, forfeits or takes strikes.
MINER_FILES="sn89_signals/crypto.py sn89_signals/bucket.py sn89_signals/chain.py sn89_signals/schema.py neurons/miner.py"
MINER_KEYS='OWNER_PK|REVEAL_DELAY|ACCEPTED_REVEAL|ROUND_TOLERANCE|RELAY|R2_PUBLIC_BASE|R2_EXTRA_BASES|SUBMISSION_RULES|TLOCK|DRAND'
hits="$(git diff "$PIN" "$NEW" -- sn89_signals/config.py | grep -E '^[+-][^+-]' | grep -E "$MINER_KEYS" || true)"
files="$(git diff --name-only "$PIN" "$NEW" -- $MINER_FILES)"
if [ -n "$hits" ] || [ -n "$files" ]; then
  rc=2
  echo "!! MINER-BREAKING candidates (an OWNER_PK change makes a pinned miner earn STRIKES; 3 = permanent exclusion):"
  [ -n "$hits" ] && echo "$hits"
  [ -n "$files" ] && echo "   changed: $files"
  echo "   -> stop submitting, read the diff, re-pin with SN89_PIN=<sha> bash setup_minerbox.sh"
  echo
fi

# Economics and scoring: re-price the plan.
ECON_KEYS='LF_BOND|COMP_WEIGHTS|lf:|agentbook|REFERR|IMMUNITY_S|WIN_CAP|QUALIFY|EMISSION_DECAY|EFFICIENCY|TIER|HIT_RATE_WINDOW|COPY_|KEEPALIVE|POINTS'
econ="$(git diff "$PIN" "$NEW" -- sn89_signals/config.py sn89_signals/scoring.py sn89_signals/replay.py | grep -E '^[+-][^+-]' | grep -E "$ECON_KEYS" || true)"
if [ -n "$econ" ]; then
  echo "!! scoring / emission / bond changes (re-check the plan's numbers and kill criteria):"
  echo "$econ" | head -60
  echo
fi

# Board: delisted pairs void as invalid_payload; band changes alter wash and edge.
board="$(git diff --name-only "$PIN" "$NEW" -- data/)"
if [ -n "$board" ]; then
  echo "!! board files changed ($board): re-run the backtest with the new bands"
  git diff "$PIN" "$NEW" -- data/signals-bands.json | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' | head -40
  echo
fi

echo "Upstream is ${NEW:0:7}. Your miner stays on ${PIN:0:7} until you re-pin."
exit "$rc"
