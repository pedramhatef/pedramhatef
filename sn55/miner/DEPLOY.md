# SN55 (NIOME) CPU miner — deployment

**No GPU. No paid API.** A 2 vCPU / 2–4 GB always-on Ubuntu VPS is sufficient. The miner replies
to the validator instantly, then builds a ~250-row submission on CPU in <1 s and uploads it well
inside the 300 s window.

## 1. Host
Any always-on Linux box with a public, reachable axon port (default 8091). Reuse an existing budget
VPS or rent a ~$5/mo 2 vCPU box. Peak RSS ~0.6 GB.

## 2. Code
```
git clone https://github.com/genomesio/subnet-niome.git && cd subnet-niome
git checkout 0c03fef2476b56285a202c76cc0d698b62b86b74   # pin; re-audit before moving forward
python3.12 -m venv .venv && source .venv/bin/activate
uv sync    # (docs say requirements.txt — there is none; use uv)
```
Copy this repo's `solver/` and `miner/` next to `niome_subnet/`, and replace
`Miner.process_task` in `neurons/miner.py` with the body in `miner/process_task.py`.

## 3. Reference genome (must match the validator exactly)
```
mkdir -p ~/niome-data
wget -O ~/niome-data/chr11.fa.gz \
  https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.chromosome.11.fa.gz
gunzip ~/niome-data/chr11.fa.gz
export CHR11_FA=~/niome-data/chr11.fa
```
Checksum (BSD `sum`) must be `06038 39169` — the exact file the validator builds against.

## 4. Wallet + registration (owner authorization required before spending)
```
btcli wallet new_coldkey --wallet.name niome_cold
btcli wallet new_hotkey  --wallet.name niome_cold --wallet.hotkey niome_hot
btcli subnet register --netuid 55 --wallet.name niome_cold --wallet.hotkey niome_hot  # burn ~0.0005 TAO
```
Use a NEW wallet. Do NOT reuse SN123/SN105/SN28 keys.

## 5. Run under a process manager
```
export PYTHONPATH=$PWD CHR11_FA=~/niome-data/chr11.fa
pm2 start "python neurons/miner.py --netuid 55 --subtensor.network finney \
  --wallet.name niome_cold --wallet.hotkey niome_hot --axon.port 8091" --name niome-miner
```

## 6. Isolation & safety
- Separate VPS / user / wallet from all production miners (SN123 MANTIS untouched).
- One UID. Running many UIDs per owner/IP contradicts the subnet's stated one-per-owner intent
  (the filter is currently absent from validator code but the intent comment remains) — do not
  multi-UID without the owner's explicit go-ahead.
- Financial cap: pre-decide a max registration spend and a stop date; the only ongoing cost is the
  VPS and occasional re-registration (~$0.11 each).

## Offline check before registering
```
python -m miner.solve contract.json hbb_reference.json $CHR11_FA out.json
```
Score `out.json` with the pinned validator stages to confirm a full valid submission.
