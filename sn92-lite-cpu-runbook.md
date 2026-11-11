# SN92 s1-lite + s1-cpu on one rented card: runbook for an operator session

Goal: put the operator's **existing, already registered** SN92 hotkey on two pools at once (`s1-lite` + `s1-cpu`)
on the cheapest rented Vast GPU that maximises **net $/day** = expected pay − rent. No new registration, no burn.

## Hard limits (do not cross without the operator's explicit OK)

- Spend: one instance at a time, **≤ $0.25/h**, verified hosts only. Never register a hotkey, never move TAO/alpha.
- The VPS runs other subnets' production work (other users' homes, other venvs, services, `/opt/*`, Mantis, Hermes).
  **Touch nothing outside** `~/scout`, `~/scout/bt-venv`, `~/.config/vastai`, the dedicated Vast SSH key and the
  root-side wallet mirror. Read-only commands elsewhere only. No restarts, no package installs system-wide.
- Wallet: only the **hotkey** file and `coldkeypub.txt` go to the GPU box. The coldkey never leaves the VPS and is not needed.
- Ask the operator before: renting, committing on chain, destroying, anything irreversible.

## Pay model (measured on chain, 11 Nov 2026, `sn92_pool_econ.py`)

- Single-pool slots at cap: s1-lite **0.043 %** of incentive, s1-cpu **0.011 %**. s1-pro single card ≈ 0.09 % (loses to rent, skip).
- $ per 1 % of incentive ≈ $160/day at the 9 Nov alpha price; rescale by today's SN92 alpha price.
- Caps: s1-lite 32 decisions/s (one decision at a time, llama.cpp), s1-cpu 4/s. Pay is linear below the cap.
- Known s1-lite speeds (kit docs): RTX 5070 Ti 33/s, RTX A6000 24/s. s1-cpu model on CUDA ≈ 0.01–0.014 s/decision.
- Expected gross at cap ≈ $8.7/day ⇒ break-even ≈ $0.33/h; target ≤ $0.20/h for ≥ +$3/day.

## Steps

1. Re-run `~/scout/bt-venv/bin/python3 ~/scout/sn92_pool_econ.py --tao-usd <TAO$> --uid <UID>`; confirm the UID is
   registered and the s1-lite cap share is still ≈ 0.04 %. Get today's SN92 alpha price; recompute $/day.
2. `vastai show user` (credit), then search verified offers, e.g.
   `vastai search offers 'verified=true rentable=true num_gpus=1 gpu_ram>=12 reliability>0.98 inet_down>200 direct_port_count>=2' -o 'dph+'`
   and rank by net/day: est. lite speed (5070 Ti/4080/4080S/5080/4090/5090 ≥ 32/s → full; 3090/A6000/A5000 ≈ 0.75;
   smaller cards: measure first) × $6.9 + $1.8 − dph × 24. Show the top 3 to the operator before renting.
3. Rent with a CUDA **devel** image (needs nvcc for llama.cpp), ≥ 40 GB disk, one mapped TCP port for the axon.
   On first interactive login `touch ~/.no_auto_tmux` (Vast forces tmux; HOME may be /tmp).
4. On the box, from the kit repo `reaxlabs/reax-subnet` at the release tag the validators run (check `version.py`):
   build `llama-cpp-python==0.3.36` with `CMAKE_ARGS="-DGGML_CUDA=on"` as in `runtimes/s1-gguf/Dockerfile.cuda`;
   download the pinned GGUFs from `src/reax_subnet/manifests/s1-lite-v1.json` and `s1-cpu-v1.json` (repo, revision)
   and **verify their sha256**; start two `runtimes/s1-gguf/gguf_server.py` sidecars on loopback (lite 8103, cpu 8104,
   `GGUF_N_GPU_LAYERS=-1`, one shared token file, manifest per model). Read the server's header for its env vars.
5. Install the kit hash-locked (`requirements/lock.txt`), copy only the hotkey + `coldkeypub.txt`, start the miner with
   `REAX_MODELS=s1-lite,s1-cpu`, `REAX_S1_LITE_RUNTIME_URL=http://127.0.0.1:8103`,
   `REAX_S1_CPU_RUNTIME_URL=http://127.0.0.1:8104` and the external IP/port Vast maps (docs/MINER.md,
   "Several models on one hotkey"; `deploy/miner-s1.env.example`). Leave s1-pro and s1-fast off.
   Expected commitment: `reax-s1m/1:s1-lite:8c7297e14e5e89bf,s1-cpu:f2c9981f0766f2a6` (check with `reax-commit show`).
6. Measure each sidecar's sequential decisions/s; check the axon is reachable from the VPS and the sidecar ports are not.
7. Monitor with the tally script every tempo (72 min). First non-zero reading comes 1–2 tempos after start.

## Stop rule (pre-registered)

After **three readings from the first non-zero one** (≈ 6 h): if the UID's share × $/1 % < rent + $1/day, or any strike
appears, destroy the instance (`vastai destroy instance <id>`; confirm `vastai show instances` is empty) and report.
Also stop if the operator says so, if SN92 announces a pool/budget change for s1-lite/s1-cpu, or if credit < 1 day of rent.
