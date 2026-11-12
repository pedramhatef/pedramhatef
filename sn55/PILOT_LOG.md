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
