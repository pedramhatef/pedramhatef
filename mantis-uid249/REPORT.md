# UID249 recovery: investigation report (independent review copy)

Run: 2026-11-11, about 21:55–22:15 UTC, in a cloud session that is **not** the Paris investigator VPS.
Scope: read-only. Nothing in production, wallets, timelock, collateral, PM2 or R2 was touched.

## 0. What this environment could reach

| Resource | Status |
|---|---|
| Investigator VPS, its recovery context and investigation directory | **Not present** in this container. No archived field, cohort array or prior report could be read or re-hashed. |
| Production miner host | No access (as expected). |
| Paying validator host, logs or datalog export | No access. |
| `entrypoint-finney.opentensor.ai`, `api.drand.sh`, `taostats.io` | **Refused by this environment's network policy.** |
| `github.com/Barbariandev/MANTIS` (git clone) | Reachable. Upstream HEAD is still the pinned commit `ace548a` (2026-11-06); there are no newer public commits. |

Pinned source verified (git blob ids match the handover):
`ledger.py 7f07120d…`, `validator.py f9af2ae8…`, `xsec_rank.py 48b3ce20…`, `funding_xsec.py` blob `027a4937…` / sha256 `99057f58…` (matches handover).
Sha256 values of the others: ledger `18b5c478…`, validator `c1caab19…`, config `127e1916…`, bucket_forecast `42f6ebf8…`.

## 1. Live baseline: NOT MEASURED

The chain RPC host was refused, so the metagraph, leader, realized 7-day alpha and FLOW P&L are **unavailable from this session**. The handover's figures (UID160 0.02788 vs UID249 0.00467 at block ≈9,480,217) are unverified carry-overs. The deployed scorer hash and config on the paying validator are unknown; no authenticated source exists here.

## 2. Receipt and maturity trace

### 2a. What the pinned public source does (verified by reading the code)

| Stage | Source behaviour (ace548a) | Relevance of a 48h lock |
|---|---|---|
| (1) Fetch | `cycle.get_miner_payloads`: on-chain commitment URL must be `*.r2.dev` or `*.r2.cloudflarestorage.com`, path = exactly the hotkey, ≤25 MB, v2 schema (`comms._is_v2_payload`). **No check on the round or the lock horizon.** | None |
| (2) Retain | `append_step` writes `raw_payloads(ts=idx, hotkey)` for every metagraph hotkey each step (`{}` if nothing was fetched). | None |
| (3) Mature | `process_pending_payloads`: `current_block - block >= PAYLOAD_MATURITY_BLOCKS` (default **50,400 ≈ 7d**, env-overridable). | 48h = 14,400 blocks, so the drand round exists about 5 days before maturity. |
| (4) Decrypt | Drand signature fetched with 3 retries. If missing, the payload is **retained** for up to maturity + `DRAND_RETAIN_GRACE_BLOCKS` (50,400). V2 decrypt checks: owner_pk = config key, binding, skeK length 64, pke derivation, owner wrap, then AEAD. Plaintext `hotkey` must equal the hotkey. | Fails only if maturity were overridden to < 14,400 **and** the grace were absent. The grace has existed since 2026-08-17. |
| (5) Backfill | Non-zero vectors are written into `challenge_data` at the original `sidx = block // 5`; breakout embeddings are backfilled. | None |
| (6) Score | Weight calc every 1000 blocks; "young UID" floor below 36,000 + maturity blocks; per-challenge salience; then `BURN_PCT` 0.52 to UID 0. | None |

Git history: `PAYLOAD_MATURITY_BLOCKS=50400`, the drand-retain grace and `TLOCK_PROD_SUGGESTED_LOCK_SECONDS=604800` were **unchanged from Aug 17 through Nov 6**. The only Nov 6 config change was BURN 0.45 → 0.52 plus four FX binary weights set to 0.

### 2b. Conclusion about the Oct 31 zeros (inference, not proof)

- **Every miner goes through the same 7-day maturity.** So, under public source, the 48h lock **cannot** make UID249's rows zero while peers' rows at the same sidx are non-zero, once maturity has passed.
- The boundary falls exactly at one step and coincides with the 30s → 172,800s lock switch. That is the signature of an **observer that decrypts R2 objects itself, soon after fetching**. 30s-lock peers become readable within minutes; UID249's 48h ciphertexts were not yet readable when the observer ran.
- If the observer archive is instead a **validator datalog export** taken more than 7 days after the rows, the zeros would point to a real UID249-specific decrypt or validation failure. Candidates: owner_pk/binding mismatch, plaintext `hotkey` mismatch, or out-of-range values.
- **Consistency flag:** `sidx = block // 5` (`SAMPLE_EVERY=5`, so one sidx ≈ 60s). Yet the handover lists consecutive sidx 1880130 → 1880131 as 15 minutes apart. Either the archive index is not the validator's sidx, or the timestamps are capture times. This must be resolved before trusting the boundary.

**First failed stage: undetermined. It cannot be proven without validator-side evidence.**
The cheapest decisive test is read-only and uses Paris data only: re-run the observer's own decrypt on the archived UID249 ciphertexts for sidx ≥1880131, now that their rounds are more than 48h old.
- Non-zero plaintexts would mean the zeros were an observer artifact.
- Persistent failure would mean a payload-construction bug.

The Nov 12 16:55 UTC unlock witness tests the same decrypt path for one round. It still proves nothing about inclusion by the paying validator.

### 2c. Operational consequences of the source

- No FUNDING, ETH or BREAKOUT change can affect scores for **at least 7 days** (plus walk-forward evidence and EMA). A "24h abort trigger" can only use producer-side telemetry; there is no validator feedback within 24h.
- The source comment says payloads are publicly readable once the drand round passes. With the previous **30s lock, any peer could read UID249's vectors within a minute and resubmit them**. That is one candidate explanation for the ETHLBFGS cohort growing 4 → 5 around Sep 28. Testable on Paris: lagged correlation of peer vectors at t+k against UID249 vectors at t, for k = 1…30 sidx. The 48h lock already removes this for horizons ≤ 8h.

## 3. Strategy decision

### Candidate F1: causal per-asset abstention on FUNDINGXSEC. Status: **EXPLORATORY** (mechanism only)

**Scorer facts it relies on** (`funding_xsec.py`):
- Exact 0.0 is treated as missing everywhere: `nz_fit`/`nz_eval`/`nz_va`, and NaN → 0, which is neutral log-odds in the meta model.
- Each miner gets **one pooled univariate logistic across all 20 assets** (a single slope and sign).
- Credit = |bootstrap-L2 meta coefficient| × (meta AUC − 0.5)/0.5, top-20 selection by OOS AUC.
- Wrong-signed assets (the AAVE .41 pattern) therefore depress both selection and the coefficient.

**Rule:** at row t, zero asset a if the sign-agreement score of UID249's own outputs over the last 3 days of **resolved** labels (rows s ≤ t − 480) is ≤ 0. The window (4320) and minimum history (1440) are fixed in advance, not tuned. Code: `funding_causal_gate.py`. Its self-test proves rows ≤ t are invariant to any change after t.

**Mechanism test.** `abstain_mechanism_test.py` runs the unmodified pinned scorer on synthetic fields: T = 32,000, 29 peers including a correlated sub-cohort, and the target with 6 of 20 assets wrong-signed. "Placebo" means 6 *random good* assets are zeroed, at the same coverage.

| Seed | Baseline | Oracle abstain | **Causal gate** | Placebo |
|---|---|---|---|---|
| 1 | 0 (unselected) | .0595 r12 | **.0816 r1** | 0 |
| 2 | 0 | .0634 r1 | **.0761 r1** | 0 |
| 3 | 0 | .0639 r4 | **.0780 r1** | 0 |
| 4 | .0588 r5 | .0603 r3 | **.0718 r1** | 0 |
| 5 | .0527 r13 | .0568 r11 | **.0684 r2** | .0184 |
| Control (no bad assets) s1/s2/s3 | .0684 / .0588 / .0610 | same | **same** (gate never fired, coverage 1.0) | .0559 / .0512 / .0567 |

The causal gate beats baseline in 5/5 bad-asset seeds and has zero effect in 3/3 clean controls. Zeroing uninformed assets hurts, so the gain comes from the gate's information, not from abstention as such. The seed-1 bad-asset row was printed from an earlier single-seed run and not saved to `results/`; the other rows are saved there.

**What this does NOT show:** gain on the real field. Synthetic peers, stationary bad assets and absolute share levels are not comparable to UID249's real .0855. The causal gate's coverage (~.72) **fails the handover's proxy gate of coverage ≥ .85**. The cap `max_abstain=3` (coverage ≥ .85) exists for that reason and is untested. Pooled-AUC gates computed on non-zero rows are inflated by abstention by construction, so F1 must be judged **only by full-field challenge share**.

**Required next test (Paris, off-production, read-only):**
1. Apply `gate()` to UID249's archived FUNDING vectors, with labels from the archived funding rates paired by sidx (port `_build_forward_pairs`; do not pair by row).
2. Re-run the native low-memory scorer on all 258 peers with only UID249's column replaced.
3. Do this for uncapped and for `max_abstain=3` (two variants, declared now).
4. Report the share change per walk-forward segment and a UTC-day block bootstrap.
5. Oct 10–Nov 1 has already been used in design work, so label it **exploratory**. Promotion requires **share +≥.025** and a CI lower bound > 0 on a **future matured window** (Nov 12 onward, scorable from about Nov 19 because of the 7-day maturity).

### Rejected or not pursued this session

- Median-centering, 8h settlement proxy, 24h baseline, MULTIBREAKOUT flip: the handover's rejections stand. I had no data to re-test them.
- Causal regime-switch protocol: no result exists, and I did not run it (the protocol file is not accessible here).
- MULTIXSEC: not started; there is no evidence it beats F1.

## 4. Deployment proposal: **none**

Nothing is PILOT-READY. If F1 passes step 3 on the real field, the minimal patch would be:
- one wrapper on the producer's 20-dim FUNDING vector immediately before encryption (`out = gate(...)[-1]`, with a rolling buffer of its own past outputs and observed funding rates);
- rollback = the previous run.py sha `633d2377…`.

The exact diff cannot be written without the production `run.py`, which is not accessible here.

## 5. The one blocking request

To settle Issue A, the single missing piece is **a validator-side record for UID249 rows Oct 31–Nov 4**. Copy-paste message for the subnet/validator operator:

> Hi. I run SN123 UID249 (hotkey 5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD). From Oct 31 ~02:00 UTC my payloads use a 48h drand lock. Could you share, for that hotkey, blocks 9,400,650–9,430,000: (a) any `Download failed` / `commit URL` warnings, (b) the per-pass "Payload decryption failures" counts, and (c) whether `challenge_data` rows for ETHLBFGS/FUNDINGXSEC at those sidx contain a non-zero embedding for my hotkey? Also: is `PAYLOAD_MATURITY_BLOCKS` overridden from 50400 on your validator? Thanks.

Independently and without asking anyone: re-run the observer decrypt on the archived UID249 ciphertexts (§2b) on Paris.
