#!/usr/bin/env bash
# Keeps the SN55 miner alive during the pilot. Restarts it if it dies. Run under nohup.
set -u
export NIOME_REPO=$HOME/sn55/subnet-niome SN55_PKG=$HOME/sn55/pkg CHR11_FA=$HOME/sn55/data/chr11.fa
export NIOME_CAPTURE_DIR=$HOME/sn55/data/captures NIOME_AXON_IP=89.117.2.184 NIOME_AXON_PORT=8080
cd "$SN55_PKG" || exit 1
echo "[watchdog] started $(date -u)"
while true; do
  if ! pgrep -f "deploy/run_miner.py" >/dev/null 2>&1; then
    echo "[watchdog $(date -u +%H:%M:%SZ)] miner not running -> restarting"
    nohup "$HOME/sn55/venv/bin/python" deploy/run_miner.py --netuid 55 --network finney \
      --wallet niome_cold --wallet-hotkey niome_hot >> "$HOME/sn55/data/miner.log" 2>&1 &
    sleep 10
  fi
  sleep 20
done
