# SN89 (InfiniteQuant) newcomer kit and runbook

Mining Bittensor SN89's **LF Signals** competition as an independent miner, from
a dedicated hotkey-only box, starting 2026-11-13. Every number below was measured
on 2026-11-12/13 from the subnet's code (pinned commit `2e48ae8`), the Finney chain
(block ~9,493,782) and the owner's public checkpoint (31,977 graded calls).

## The verdict in one paragraph

Entry is cheap (τ0.025 per hotkey) and the scoring is honest in the narrow sense
that it grades real forward prices; our labeler re-derives 97% of official grades
from public Hyperliquid candles. But the payout is **edge-gated and owner-tilted**.
The owner's entity (UID 39) takes ~60% of the LF pool, 33% of the referrer pool,
and all of the 50% Agent pool. One owner validator sets the weights, the owner reads
every call in real time, and rules changed about every 2.6 days in the last two
months. Across the whole field there is **no detectable skill**: hit rates match
coin-flip noise, and paid newcomers hit exactly 50.0% after their first paid win.
So SN89 is worth running only if Fable shows **≥ 0.60–0.65 out-of-sample
P(TP first | decisive)** on SN89's exact bands. Prove that first (Step 0); it costs
nothing. Without it, mining here is a ~25% lottery ticket on a few dollars a day,
with ~$310 of alpha locked up.

## Numbers that matter

| Item | Value |
|---|---|
| Registration | τ0.025 per hotkey (min burn); a second one within minutes ≈ τ0.0315 |
| LF bond (needed to be paid at all) | 500 α per trading hotkey ≈ 1.46 TAO ≈ $310. Held stake, not slashed. Checked at 00:00 UTC daily |
| LF pool | 885.6 α/day; UID 39 takes ~527, ~17 independents share ~358 |
| Value of 1 tally unit | LF 3.41 α/day (≈ $2.09); self-referral recruiter +2.95 α/day (≈ $1.81) |
| Top independents | 12–17 units at 60–68% hit, 21–32% wash, ~2 calls/day → $25–35/day LF, $45–65/day with recruiter |
| Warmup | 8 days from your first journaled signal (not from registration). Warmup wins never pay |
| Qualify | ≥ 8 decisive, Wilson lower bound (z = 1.2816) ≥ 0.50: 6/8, 8/10, 10/15, 13/20, 19/30, 25/40 |
| Earliest payout | first call committed ≥ day 8 + 10 min after first_seen, resolved (≤ 8 h), then 1–2.5 h for weights |
| Chain immunity | 5,000 blocks (~16.7 h); prune queue currently ~217 older zero-emission UIDs (~27 days at recent rates) |

What a newcomer should expect, by true hit rate (3 calls/day, ~35% wash; simulated
and checked against the field):

| True P(TP first \| decisive) | Paid by day 14 | Paid by day 30 | Average tally after day 30 | LF $/day | LF + recruiter $/day |
|---|---|---|---|---|---|
| 0.50 (no edge) | 20% | 29% | ~0 (short burst, decays) | ~0 | ~0 |
| 0.55 | 36% | 54% | ~7, intermittent | ~$15 | ~$27 |
| 0.60 | 55% | 79% | ~11 | ~$23 | ~$44 |
| 0.65 | 75% | 94% | ~17 | ~$35 | ~$65 |

Dollar figures assume α = $0.613 (TAO $212) and a field tally of ~260. Both drift:
SN89 alpha fell 11% in 60 days, and protocol TAO inflow to SN89 is tiny.

Your TP/SL execution skill does **not** transfer here. SN89 fixes TP = SL = the
board band and the horizon (8 h crypto, equities, indices, WTI; 12 h FX, metals).
The only choice is pair, direction and timing, so it is a symmetric
barrier-hitting problem.

---

## Step 0 — Backtest gate (today, $0, before registering)

Use [`backtest/`](backtest/README.md). It is stdlib-only Python 3.10+ and calls only
the public Hyperliquid info API.

```bash
cd sn89-kit/backtest
python test_sn89_label.py                                     # 24 tests
python hl_data.py fetch --pairs board --intervals 1m,5m,15m,1h --days 60
python sn89_eval.py baseline --days 7 --interval 3m --fetch   # wash + random-call base rates per pair
# Export Fable's historical calls: t0_unix,trade_pair,direction,prob
#   prob = calibrated P(TP first | decisive) for SN89's band, NOT P(up)
python sn89_eval.py eval --signals fable.csv --fetch --sweep 0.50:0.80:0.02 \
       --oos-from <date the model was frozen> --labels-out labels.csv --field-tally 260
```

**Go only if all four hold on the pairs you would actually trade:**
1. Out-of-sample P(TP first | decisive) ≥ 0.62–0.65 over ≥ 150 decisive calls
   (Wilson lower bound ≳ 0.57).
2. At your threshold, ≥ ~2.5 calls/day survive SN89's filters
   (3/UTC day, 1 h gap, session rules).
3. Labels come from 5m-or-finer candles. On 15m or coarser, subtract ~1 pp of hit rate.
4. The edge is not market drift: per-direction rows beat the baseline's random
   LONG/SHORT P(TP) for the same dates. In Oct–Nov, random SHORTs won ~64% of
   decisive BTC calls because the market fell.

Labeler fidelity, measured against 8,082 official grades: 97.2% three-way agreement,
98.9% won/lost agreement on 5m-or-finer data. AUDUSD and USDCAD cannot be backtested
(not on Hyperliquid).

---

## Step 1 — Prepare (no chain actions yet)

**Miner box.** Use a dedicated VM: Linux x86_64, glibc ≥ 2.34 (Ubuntu 22.04+), an
unprivileged user, NTP synced. It holds hotkeys only: no coldkey, no exchange keys,
none of your perp bots. Put this kit at `~/sn89-kit` on it (clone this branch, or `scp -r`).

```bash
bash ~/sn89-kit/miner/setup_minerbox.sh  # pins 2e48ae8, removes the git remote, checks hashes + timelock format
```

**Coldkey machine.** Use a separate machine or a hardware wallet, with a **fresh
coldkey dedicated to SN89**. The bond divides the coldkey's alpha by the number of
signal-committing hotkeys on it.

```bash
btcli wallet new-coldkey -w sn89 --n-words 24
btcli wallet new-hotkey  -w sn89 -H iqref   --n-words 24   # A: recruiter, never trades
btcli wallet new-hotkey  -w sn89 -H iqminer --n-words 24   # B: your trading hotkey
# fund the coldkey: ~0.1 TAO now (two burns + fees), ~1.5 TAO more by day 7 for the bond
```

Copy only `coldkeypub.txt` and the two hotkey files to the miner box, under
`~/.bittensor/wallets/sn89/`.

**Relay token.** Use a dedicated Telegram account with 2FA and DM `/token` to
`@Iqsignals2026_bot`. Put the token in `~/.sn89.env` as `SN89_FEED_TOKEN=...`. It is
mandatory: the validator fetches blobs only from the owner's relay, so without it
every call is a forfeit LOSS. If the bot asks for a hotkey, give B's address only
after Step 2.4 confirms the referral. **Keep B's address private until then.**

---

## Step 2 — Register with a self-referral (one sitting, ~15 min)

The referral nearly doubles income per tally unit: the recruiter earns 2.95 α/day
per unit of B's tally, on top of B's 3.41. It costs one more burn, and it can **only**
be set up **before B registers**.

```bash
# 2.1 coldkey machine: price check + review upstream first
btcli subnets burn-cost 89 -n finney          # expect ~τ0.025
bash ~/sn89-kit/ops/sn89_watch.sh             # on the miner box: exit 0, or read every flagged diff

# 2.2 register A
btcli subnets register --netuid 89 -w sn89 -H iqref -n finney

# 2.3 miner box: A claims B (hotkey-signed on-chain commitment; no server call)
cd ~/sn89 && . .venv/bin/activate
python neurons/miner.py --wallet.name sn89 --wallet.hotkey iqref refer <B_ss58>

# 2.4 wait >= 10 blocks AND until the claim is in the public checkpoint (~5 min)
python ~/sn89-kit/ops/sn89_status.py --check-referral <A_ss58> <B_ss58>   # repeat until "journaled": true

# 2.5 register B IMMEDIATELY after (the claim is public: don't leave a squat window)
btcli subnets register --netuid 89 -w sn89 -H iqminer -n finney
btcli subnets metagraph 89 -n finney          # both UIDs present
```

Do not send any other commitment from A. If A is ever pruned (it earns nothing until
B banks a win), re-register it for τ0.025; the referral survives.

---

## Step 3 — First call and the warmup (days 0–8)

```bash
# miner box: local intake, localhost only, bearer token from ~/.sn89.env
tmux new -d -s sn89 'cd ~/sn89 && . .venv/bin/activate && set -a && . ~/.sn89.env && set +a && \
  python neurons/miner.py --wallet.name sn89 --wallet.hotkey iqminer serve --host 127.0.0.1 --port 8089 2>&1 | tee -a ~/sn89-serve.log'

# Fable goes through the gate (conviction, 3/day, 61-min gap, no overlapping open call, receipt ledger)
python ~/sn89-kit/miner/fable_intake.py submit --pair BTCUSD --direction SHORT --prob 0.67
python ~/sn89-kit/miner/fable_intake.py status
# TypeScript engine: spawn the same command and read the JSON on stdout (exit 0 sent, 2 refused, 3 error)
```

The first call starts the 8-day clock. After ~20 minutes, run
`~/sn89-kit/ops/sn89_status.py --hotkey <B_ss58>`. It must show `first_seen_utc`, and the call must not be
`void`. 28 hotkeys have been voiding with `decrypt_or_hash_mismatch` since 11-08: if
yours does, stop and fix it before calling again.

**Warmup playbook.** Warmup decisive calls count toward qualifying; warmup wins never pay.
- Use all 3 slots a day, but only on calls that pass your gate. A weak warmup
  record delays payout: to qualify on day 8 you need ~70% of warmup decisive calls
  won (e.g. 10/14).
- Prefer low-wash pairs where your backtest shows edge. More decisive calls per day
  means a faster sample and a higher efficiency multiplier (low-wash wins pay
  ~10–20% more than wins at 35% wash). Expected decisive calls/day at 3 calls/day:
  - **2.4–3.0**: US equities and indices (NVDA, TSLA, AMD, MU, SNDK, SP500, XYZ100,
    EWY). Their bands are 2–3× narrower than current volatility. They are session-bound,
    and their bands are PROVISIONAL, so expect them to widen.
  - **2.2–2.4**: KPEPE, AVAX, AAVE, BCH, TAO.
  - **1.4–1.5**: BTC, ETH, XAU (slowest).
- Be first, never follow. A 15-min shadowing pattern against one leader is
  penalized, and copied wins are stripped from pay.
- The first **payable** call must be committed at least first_seen + 8 days + 10 min.

---

## Step 4 — The bond decision (day 7, finish before 23:45 UTC)

Bond only if `sn89_status.py` shows `qualified_now: true` or
`consecutive_wins_to_qualify` ≤ 2. Otherwise defer: wins banked while unbonded
still pay (decayed) once you bond.

```bash
btcli subnets metagraph 89 -n finney    # pick a weight-setting validator's hotkey ss58 (e.g. UID 77, 255, 163 or 47)
btcli stake add --netuid 89 --hotkey <validator_ss58> --amount 1.5 -w sn89 -n finney --dry-run
btcli stake add --netuid 89 --hotkey <validator_ss58> --amount 1.5 -w sn89 -n finney
btcli stake list -w sn89 -n finney      # >= 500 α on netuid 89; hold it continuously
```

---

## Step 5 — Daily operations

- `sn89_watch.sh` from cron. Exit 2 means a miner-breaking change, such as an
  `OWNER_PK` rotation, which makes a pinned miner earn **strikes**: 3 strikes is a
  permanent exclusion and strikes never expire. Stop submitting, read the diff,
  then re-pin with `SN89_PIN=<sha> bash setup_minerbox.sh`.
- `sn89_status.py --hotkey <B_ss58>` every day.
- Spot-check grades with `backtest/sn89_label.py`. Investigate W/L flips only:
  marginal-touch disagreements happen about 2% of the time and are expected.
- Keep `~/.sn89/fable_ledger.jsonl` and the serve log. They are your evidence if a
  blob is withheld or a call is misgraded.

## Kill criteria

- **Step 0 fails**: don't register and don't bond.
- **Day 7**: raw hit < 60% and Wilson lower bound < 0.45 → don't bond. Keep calling
  unbonded, at most until day 21.
- **Day 21**: no banked paid win, or hit < 58% over ≥ 25 decisive → stop calling and unstake.
- Any strike, ≥ 2 unexplained `no_reveal` forfeits, or ≥ 2 W/L flips against your own
  re-grade in the first 20 calls → pause and escalate publicly with the commit hash.
- Rule change: LF bond > 1,000 α, LF share < 25% of miner emission, or the recruiter
  score bond-gated or removed → re-evaluate within 24 h.
- **Day 30**: combined income < ~15 α/day (~$9) → exit. SN89 alpha down > 25% from
  your bond entry while income is < $20/day → unstake.

## Never

- Run `run.sh`. It pulls and pip-installs unsigned upstream code every 30 minutes,
  next to your hotkey.
- Use `follow`, `limit`, `positions`, `watch-positions`, `submit-closers` or
  `register-x`. The server picks your calls, or you blind-sign server challenges.
- Set `SN89_R2_*` or `SN89_BLOB_DIR`. Self-hosted blobs are never fetched, so every
  call becomes a forfeit LOSS.
- Submit a call ahead of your own fills elsewhere: **the owner can decrypt every call
  at commit time**.
- Hand-roll the encryption. A wrong owner key is a strike.

## Contents

| Path | What it is |
|---|---|
| `miner/setup_minerbox.sh` | Hardened, pinned, hash-checked install on the miner box |
| `miner/fable_intake.py` | Gate between your model and the miner intake, with a receipt ledger |
| `ops/sn89_status.py` | Your standing and the referral check, from the public checkpoint |
| `ops/sn89_watch.sh` | Daily upstream rule diff (review-only) |
| `backtest/` | SN89-exact barrier labeler, Hyperliquid data fetcher, evaluator, tests |

Not tested in this session: the third-party install inside `setup_minerbox.sh`.
Running the subnet's own code was out of scope here, so run it first on your box.
The intake gate was tested against a mock of the miner's endpoint, the status tool
against the live checkpoint and chain weights, and the labeler against 8,082
official grades.
