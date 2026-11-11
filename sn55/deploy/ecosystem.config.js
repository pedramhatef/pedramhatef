// pm2 ecosystem for the isolated SN55 miner. Env is baked in so it survives reboot + pm2 resurrect
// (a plain `export` would not, and the axon would republish a loopback IP). Edit the 4 values below.
const HOME = process.env.HOME;
module.exports = {
  apps: [{
    name: "niome-sn55",
    script: `${HOME}/sn55/venv/bin/python`,
    args: "deploy/run_miner.py --netuid 55 --subtensor.network finney " +
          "--wallet niome_cold --wallet-hotkey niome_hot",
    cwd: `${HOME}/sn55/pkg`,
    interpreter: "none",
    max_memory_restart: "1500M",
    autorestart: true,
    env: {
      NIOME_REPO: `${HOME}/sn55/subnet-niome`,
      SN55_PKG: `${HOME}/sn55/pkg`,
      CHR11_FA: `${HOME}/sn55/data/chr11.fa`,
      NIOME_CAPTURE_DIR: `${HOME}/sn55/data/captures`,
      NIOME_AXON_IP: "89.117.2.184",   // <-- the box's public IPv4; change if it changes
      NIOME_AXON_PORT: "8080",
      BT_NO_PARSE_CLI_ARGS: "false"
    }
  }]
};
