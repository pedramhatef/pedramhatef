# SN55 pilot runbook — Paris box (vps97812493), isolated

Box verified: 3 vCPU / 4.8 GB / 8.6 GB free / Python 3.12.3 / public IPv4 **89.117.2.184**.
Everything lives under `~/sn55` and `~/sn55/data`. **Nothing outside those is touched.** Your MANTIS
observers / watchers / pm2 / tmux are not modified. The only TAO spent is the registration burn
(~0.0005 TAO ≈ $0.11) in **Step 4**, which you run yourself.

Isolation guarantees:
- dedicated workspace + dedicated venv (no global installs, your other Python envs untouched)
- a new pm2 app named `niome-sn55` on its own axon port — I never run `pm2 kill/update/restart all`
- a brand-new wallet; **never reuse SN123/SN105/SN28 keys**

---

### Step 1 — Setup (no wallet, no spend). One paste:
```bash
cd ~ && curl -fsSL https://raw.githubusercontent.com/pedramhatef/pedramhatef/claude/upbeat-turing-wmip3o/sn55/deploy/bootstrap.sh -o /tmp/sn55_bootstrap.sh && bash /tmp/sn55_bootstrap.sh
```
Expect it to end with `self-check OK: built 250 valid rows offline`. If your repo is private, instead
`git clone` it manually into `~/sn55/pkg_full` and re-run the script (it will skip the clone).

### Step 2 — Pick a free axon port and open it
```bash
PORT=8191
ss -ltn "( sport = :$PORT )" | grep -q ":$PORT" && echo "PORT $PORT BUSY — pick another" || echo "PORT $PORT free"
command -v ufw >/dev/null && sudo ufw allow ${PORT}/tcp || echo "no ufw; open ${PORT}/tcp in your VPS provider firewall panel"
```
If your provider (OVH/Scaleway/etc.) has an external firewall/security group, open `${PORT}/tcp` there too.

### Step 3 — New wallet (no spend)
```bash
btcli wallet new_coldkey --wallet.name niome_cold
btcli wallet new_hotkey  --wallet.name niome_cold --wallet.hotkey niome_hot
```
Then send a small amount of TAO (e.g. 0.05) to the `niome_cold` coldkey address shown. **Back up the
mnemonics offline. Never paste seed phrases anywhere.**

### Step 4 — Register on SN55 (THE spend: ~0.0005 TAO)
```bash
btcli subnet register --netuid 55 --network finney \
  --wallet.name niome_cold --wallet.hotkey niome_hot
```
Confirm the burn shown is ~0.0005 TAO before accepting. This is the only on-chain spend.

### Step 5 — Start the miner under pm2 (isolated app, reboot-safe)
Edit the 4 values in `~/sn55/pkg/deploy/ecosystem.config.js` if your wallet names / port / public IP
differ (defaults: `niome_cold` / `niome_hot` / `8191` / `89.117.2.184`), then:
```bash
cd ~/sn55/pkg && pm2 start deploy/ecosystem.config.js
pm2 logs niome-sn55 --lines 40
```
`pm2 save` (optional) persists it across reboot — it only snapshots the current list and does not
disturb your other apps. The env is baked into the ecosystem file, so a reboot republishes the correct
public axon IP (not loopback).

### Step 6 — Verify the pilot (the two things that must be true)
1. **Axon is reachable** — from another machine: `curl -m5 http://89.117.2.184:8191/forward` should
   answer (401/403/422, i.e. the server is up), not time out.
2. **First task captured** — within a round (~3.6 h) the logs should show
   `captured live contract+reference` and `uploaded submission (NNN rows)`. The raw live contract is
   saved under `~/sn55/data/captures/` — **send me one** so I can confirm the live schema matches the
   reconstruction (the one thing the pilot verifies). If you instead see `LIVE SCHEMA MISMATCH`, the
   miner safely declined; send me the logged keys and I'll adapt the solver.

### Step 7 — Watch earnings (read-only)
```bash
btcli wallet overview --wallet.name niome_cold --netuid 55
```
Look for nonzero incentive/emission on your UID after a scored round. Auto-stop rule: if no top-10
placement within the ~1-day immunity window across several rounds, pause and reassess before spending
more time — do not keep re-registering blindly.

---

### Stop / rollback (clean)
```bash
pm2 stop niome-sn55 && pm2 delete niome-sn55
```
Nothing else to undo. Staked alpha (if any) can be unstaked and sold at ~spot.
