# SN55 pilot — deployment log and findings

Companion to `REPORT.md` (which covers the pre-deployment research). This records what was
learned by actually deploying a miner on mainnet, 2026-11-11 → 2026-11-12.

## Deployment state

| | |
|---|---|
| UID | **96** on netuid 55 (finney) |
| Hotkey | `5DX7Ft8tE4vTUwvi7qj7wpUJyYfXjc6UHmj9eXJznNmuXsFj` (`niome_cold`/`niome_hot`) |
| Host | Hosteons VPS `89.117.2.184`, 3 vCPU / 4.8 GB, Asia/Singapore TZ |
| Axon | `89.117.2.184:8080` (served on-chain, externally reachable) |
| Workspace | `~/sn55` (repo `~/sn55/subnet-niome` @ `0c03fef`, venv, `~/sn55/data`) |
| Launcher | `~/sn55/pkg/deploy/run_miner.py` under `nohup`, kept alive by `deploy/watchdog.sh` |
| Spend to date | **0.0005 TAO (~$0.11)** registration. No other cost. |

Run / restart:
```bash
git -C ~/sn55/pkg_full pull --ff-only
kill $(pgrep -f "deploy/run_miner.py")       # watchdog restarts it within ~20s
grep -aE "auth window|SN55 miner up|served axon" ~/sn55/data/miner.log | tail -3
```

## Six real bugs that each silently produce zero rewards

Every one of these was invisible without instrumentation. **Items 4 and 5 mean the stock miner
cannot be scored at all** — they are the most valuable findings here.

1. **`process_task` ships empty.** `neurons/miner.py` has `pass` as its body. Nothing is ever
   submitted. Fixed by writing the solver (`solver/`, `miner/solve.py`).
2. **`--axon.port` is dead on bittensor 11.** `BaseMinerNeuron.add_args` registers it, but the
   flag is rejected by the config parser, so the port silently stays 8091. Fixed with
   `NIOME_AXON_PORT` applied after `super().__init__()`.
3. **Run loop busy-spins.** `while self.block - neuron.last_update < epoch_length: sleep(1)`
   never sleeps for a miner that has never been scored (`last_update` stays at the registration
   block), so `sync()` runs ~5×/sec and hammers the RPC. Fixed by overriding `run()` with a
   fixed 60s throttle.
4. **Axon publishes a loopback IP.** `_serve_axon_on_chain` uses
   `socket.gethostbyname(socket.gethostname())`, which on many VPS resolves to `127.0.1.1`.
   Validators then cannot reach the miner at all. Fixed with `NIOME_AXON_IP`.
   *(The subnet owner's own validator UID 119 advertises `127.0.1.1:8091` on-chain — same bug.)*
5. **Receiver-binding mismatch → 401 on every validator request.**
   - validator (`validator/forward.py::query_miner`): `bt.http_auth.sign(wallet, method, path, body)`
     — **no `receiver_ss58`**, so no `X-Bittensor-Receiver` header is sent.
   - miner (`base/miner.py`): `bt.http_auth.verify(...)` with the default **`require_receiver=True`**.
   - Result: `WrongReceiver: missing X-Bittensor-Receiver` → 401, always.

   Reproduced directly against bittensor 11.0.1: stock `sign()` emits
   `[Version, Hotkey, Nonce, Signature]`; strict `verify()` rejects it; `require_receiver=False`
   accepts it. Fixed by overriding `_setup_routes`.
6. **Validator nonces arrive older than the 10s freshness window.** After fixing (5), the next
   real validator request failed with `StaleRequest: nonce is older than the 10s freshness
   window` (from `209.209.8.121`). Our clock is NTP-correct, so the validator's signed nonce is
   simply older than stock `max_age=10 / allowed_skew=2` allows. Fixed by widening to
   `max_age=600 / allowed_skew=300` with a matching-retention `InMemoryNonceStore` (replay
   protection preserved).

## Abuse detection (because the auth window is deliberately loosened)

Widening the window means we accept requests a stock miner would reject, so the miner now
identifies callers and validates task origin:

- `_caller_verdict()` — resolves the (cryptographically verified) caller hotkey in the metagraph
  and logs `uid / validator_permit / stake / dividends / trust` with an explicit verdict.
- `_check_origin()` — logs the contract/reference hosts and, critically, the **upload target**;
  warns when the presigned URL is not known NIOME storage (how an impostor would harvest work).
- Opt-in hardening, both currently **off** so they cannot block a first real task:
  `NIOME_REQUIRE_VALIDATOR=1` (refuse callers without a validator permit),
  `NIOME_STRICT_UPLOAD_HOST=1` (refuse uploads to unknown hosts).
  **Turn both on once a genuine validator hotkey + upload host have been observed.**

## Operational findings about the subnet

- **One task per round; one shot per validator.** `broadcast_task` appends the uid to
  `collected_uids` **before** querying, and `query_miner` swallows all errors without retry.
  So any failure (401, timeout) burns that validator's shot until the task id changes next round.
- **Only one validator appears to actually broadcast.** All hits observed came from
  `209.209.8.121`, and all validators' weight vectors were byte-identical copies — consistent
  with one scoring validator and the rest mirroring. Plan on ~1 shot per round.
- **Validation froze for ~40 h** (owner validator UID 119 offline) while other validators kept
  re-committing a stale weight vector, so the rewarded set did not move and no new miner could
  enter. It resumed 2026-11-11 ~20:47Z; the owner confirmed in Discord ("Owner validator is live
  now!"). **Treat validation reliability as a real operational risk.**
- **Once live, the rewarded set churns ~90% per round.** Three consecutive observations shared
  almost no UIDs. Combined with 30-day history (most persistent UID held a slot only ~14% of
  samples), this implies an effective competing pool of roughly **70–100 miners**, i.e. a merely
  competent miner lands top-10 in ~10–14% of rounds.
- **Timing:** round = 1080 blocks ≈ 3.6 h → **6.7 rounds/day** (not 20; 20 is the 360-block
  *epoch* count). Weights set per round, so one win is held for ~3 epochs.
- Per-round value of a win at current prices: rank 1 ≈ $56, rank 5 ≈ $9, rank 10 ≈ $2.

## Multi-UID question — deliberately not pursued

The validator still contains `# One submission per machine and per owner: a coldkey running many
hotkeys behind one IP must not get more shots at the task.` (enforcement was added in `7385f6b`
then removed, comment retained), and duplicate-submission detection shipped 2026-11-11 flagging
byte-identical submissions across uids. Running many UIDs would contradict the operator's stated
rule and the project's own constraint against bypassing anti-Sybil measures. **Open question to
ask the operator directly before considering it.**

## Verified offline, pending on-chain

- End-to-end self-test (`deploy/selftest_task.py`) signs a `/forward` request exactly as the
  validator does, serves a contract locally, and confirms the full chain:
  **auth → download → solve → upload, 250 rows in 2.86 s** (vs a 300 s deadline).
- **Still unproven: a real scored round.** No validator request has yet authenticated
  successfully, so our submission has never been scored and we do not know where it ranks.
  That single data point is what decides whether the solver is competitive.

## Immediate next step

Be reachable and authenticating during a broadcast window (first ~500 blocks of each round) and
capture one real task. Then: verify the live contract schema against `harness/synth.py`, read the
score when that round's weights land, and turn on the two hardening switches using the confirmed
validator hotkey and upload host.

## First real scored task — 2026-11-12 05:47Z

- **Owner validator UID 119** (`5DJ5fT174AY8…`, the `OWNER_HOTKEY`) from `209.209.8.121`
  authenticated, sent task `8a8c2c7d-9d67-46cf-8faf-d7ee7c5a43a6`; contract/reference on
  `drive.usercontent.google.com`, upload target `www.googleapis.com`. Uploaded 250 rows in 5.56 s.
  **Both auth fixes (#5, #6) confirmed against the real validator.**
- **Live contract differs sharply from the reconstruction** (`evidence/live_task_20261112/`):
  2 mutations with HGVS names (`HBB:c.359G>A` w=1.155, `NC_000011.10:g.5226539G>A` w=0.65),
  cell type `HUDEP-2`, `base_padding=400`, **`proximity_gate=false`**, `max_experiments=250`;
  reference carries `window_id`, `chromosome`, and a `challenge` block mirroring the contract.
  The solver read it dynamically and produced **250/250 valid rows** (official scorer).
- **Weakness it exposed:** candidate sites were ranked GC-first. With the gate off the window is
  ±2000 bp and `dist_score = exp(-d/400)`, so far perfect-GC sites crowded out near ones
  (median distance 841 bp). Fixed by ranking on the true stage-2 score
  `(0.625·gc + 0.375·exp(-d/bp))·offtarget`. On the live contract, official scorer, 8 seed-rounds:
  **13.03 → 14.32 (+9.9%)**; median distance 841 → 276 bp. Non-regressive on all 12 synthetic
  contract shapes (+0.0% to +4.7%).
- The uploaded submission for this round used the old ranking (~13.0); the fix applies from the
  next round.

## Seed-safe GC tuning + greedy allocation — 2026-11-12

The solver now (a) GC-tunes every guide toward the gc_score optimum and (b) allocates the 250
experiments greedily by true stage-2 row_score, with a per-cell coverage floor/cap so DFF holds.

- **GC tuning is specificity-preserving by construction.** `offtarget_factor` reads the seed
  12-mer as `guide[-12:]` (Cas9) / `guide[:12]` (Cas12a). `seed_safe_push_gc` edits *only* the
  complementary PAM-distal bases, within the contract's `max_mismatches`, so the seed — and the
  off-target factor computed from it — stays exactly the genome's. These are real, tolerated
  PAM-distal mismatches explicitly allowed by the contract; the guide still targets its site.
  **Audited: 0 of 250 live rows, and 0 rows on all 12 synthetic shapes, have any edit inside the
  seed.** This raises gc_score honestly; it does not fabricate uniqueness.
- **Greedy-by-score allocation** shifts mass toward genuinely higher-scoring guides (notably
  genome-unique `-` strand guides that legitimately earn offtarget=1.0), lifting TWS, while a
  12% floor / 45% cap per (cas×strand) cell keeps strand/cas/joint coverage high for DFF.
- **Measured on the live contract (official stages, 3 seeds):**
  deployed build final **14.18** (TWS 150, DFF 0.962) → new build final **17.62** (TWS 184,
  DFF 0.912), **+24%**, 250/250 valid. Beats the earlier hand-tuned static file (16.69).
- **Non-regressive and robust** across all 12 synthetic shapes (max_mismatches 1–4, padding
  100–2000, gate on/off, 2–6 mutations): every shape yields a fully valid, seed-safe submission;
  the few sub-250 shapes are tight-window candidate ceilings (e.g. gate + 100 bp → 143 raw
  sites), identical to the old limit, not a regression.
- **Honest ceiling, stated plainly:** final ~17–18 with a CF ~0.10 lands mid-pack (~median), not
  top-5. The remaining gap to top-5 is not an optimization problem — it requires faking off-target
  specificity (editing the targeting seed to make a non-unique guide read as unique → a guide that
  scores but would not cut) or Sybil slot expansion against the operator's "one submission per
  machine and per owner" rule. Those are out of scope on principle, independent of risk appetite.
