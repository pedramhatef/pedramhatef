# UID249: round 6. Corrected R2 assembler, validator-equivalent membership, and the path to a clean prospective window

Date 2026-11-12 UTC. Off-production. No production, R2 write, wallet, collateral, or host rental. No reward claim, no patch.

## 1. Corrections accepted

| Your finding | Consequence here |
|---|---|
| Preserved `field_f16.npy` are challenge-specific (66-D breakout / 20-D funding) | The round-4 bridge's `[113:146]` slice is invalid on them. **Replaced** (§2). My round-5 "layout PASS" checked the *config* layout only, not those arrays. |
| Original R2 NPZ shards hold full (miners, 194) X; all 32,805 sidx have SHA fingerprints; ~1.34 GB read-only transfer | The new assembler reads exactly those, content-addressed by SHA. |
| F1 scored segment: 4,793/7,940 origins (60.4%) with all-33 future prices unchanged → 158,169 all-negative labels | **F1 is withdrawn as evidence** for any candidate. |
| F2: 1,963/7,940 origins (24.7%) with zero UID249 breakout observations after the Oct 31 lock switch | Expected **observer** lock blindness, the mechanism from round 2. The new assembler measures it for MULTIXSEC directly; observer 0 is never read as validator 0. |

## 2. Corrected assembler: `build_multixsec_from_r2.py` (7/7 tests)

For each of the T manifest sidx, in order:
1. The shard is found as `<shard_dir>/<sha256>.npz`. A **missing shard stops the run**; rows are never dropped.
2. The file is re-hashed and must equal the manifest SHA.
3. The shard's own `sidx` must equal the manifest sidx (exactly once).
4. `X` must be (n_miners, 194). The assembler takes `X[:, 113:146]`, mapped by the **shard's own hotkeys**, never by UID.
5. Prices come from the shard. With `--prices-archive`, they must equal the archived 33-column prices exactly.

The output is memory-mapped and refuses to overwrite.

The integrity report adds a **visibility** block:
- UID249 non-zero fraction before and after sidx 1,880,131;
- rows where UID249 is present but all-zero;
- how many hotkeys are never non-zero;
- non-zero-fraction quantiles across hotkeys.

Tests cover:
- hotkey re-ordering between shards, a deregistering hotkey, and other challenges' columns never leaking in;
- the UID249 visibility numbers;
- that check-only writes nothing;
- each fail-closed path: missing shard, tampered shard, sidx mismatch, price cross-check mismatch, prices absent.

## 3. Validator-equivalent field membership

- **Harness `--active-json`:** per fold, non-registered columns are **removed** (as `_collect_hotkeys` does), not
  zeroed. Equality with a direct scorer call on the subset is tested. It stops if UID249 isn't registered at a fold's
  scoring block.
- **`scoring_time_hotkeys.py`:** reads `SubtensorModule.Keys(123, uid)` at a historical block through the bittensor
  ≥11 SDK, using its default archive routing. Public, read-only, no wallet.
  - **Scoring-time block, from source:** data ending at sidx `s` is first scorable at ≈ `5·s + PAYLOAD_MATURITY_BLOCKS + 500`.
  - So membership is taken about 7 days **after** a fold's last row, not at it. Maturity is an explicit argument,
    recorded as unverified for the paying validator.
  - Tested offline with a fake client (2/2). I confirmed `bt.storage.SubtensorModule.Keys` exists in bittensor 11.3.2.
    **Not run against the chain**, because this session's network policy blocks it.

All 31 tests pass:

| Test file | Passed |
|---|---|
| Harness | 8 |
| R2 assembler | 7 |
| Hotkey script | 2 |
| F2 | 9 |
| P0 | 5 |

## 4. The four validator-equivalent data items

| Item | Who can supply it | How |
|---|---|---|
| Scoring-time registered hotkeys | **Paris, from public chain state** | `scoring_time_hotkeys.py`. No operator needed. |
| UID249's decrypted MULTIXSEC submissions | **You** (own data) | (a) If the Paris private receiver stored the full encrypted objects (it verified byte SHA on Nov 11), every round older than 48h is now publicly decryptable: decrypt them with pinned `ledger._decrypt_v2_payload` + drand. That recovers UID249's 194-D vectors, MULTIXSEC included, for the receiver's window. (b) Or the producer's own plaintext logs for rounds already unlocked. Read-only, and only with your approval. |
| Price-feed freshness | **Owner only** | Paris gets HTTP 403 on `latest_prices.json`, the URL the public validator code reads. If public access is closed, paying validators must use another feed. That is a parity question in itself, so it is included in the request below. |
| Challenge-level salience | **A validator operator** (`per_challenge` log), or reproduced by the pinned scorer once the three items above exist | Request in round 2 §2; append the MULTIXSEC line. |

**Owner/operator request, consolidated (copy-paste):**

> Hi. Read-only questions about SN123 data for my miner UID249 (5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD):
> (1) `latest_prices.json` returns 403 to us. Is it still the feed validators read? Is there a per-asset
> `last_updated`, and what is published when a source fetch fails? (2) Were spot prices and funding frozen
> on Oct 22 ~06:00–19:30 and Oct 23 19:45 → Oct 25 23:37 UTC? (3) Your validator's `PAYLOAD_MATURITY_BLOCKS` and the
> commit it runs. (4) The latest `per_challenge` log line, filtered to my hotkey's MULTIXSEC, FUNDINGXSEC, ETHLBFGS and
> MULTIBREAKOUT salience. Thanks.

## 5. A clean prospective window

The observer zeros come from decrypting **at capture**. The validator instead stores ciphertext and decrypts **after
maturity**. A validator-equivalent observer must do the same:

1. **Store** each miner's raw v2 object (as fetched) per sampled block. Only those that cannot be decrypted at capture
   (drand round in the future) are needed; immediate-lock ones can be decrypted now as today.
2. **Decrypt after the round passes** with the pinned, unmodified `ledger._decrypt_v2_payload` and the public drand
   signature, then keep only the needed slices (MULTIXSEC 33 + FUNDING 20, float16) and delete the ciphertext.
   - Retention needed is ≈ lock length, not 7 days.
   - Storage ≈ (long-lock miners) × (object bytes) × 1,440/day × lock-days. Paris should measure object size before
     enabling, given ~6 GB free disk.
3. **Membership** from `scoring_time_hotkeys.py`; **labels** from the validator price feed, which needs the feed-access
   answer from §4.

**Forward protocol** (pre-registered; starts when step 1 is live, earliest the next UTC midnight):
- **Shadow:** compute baseline, V1, V2 and R live from causal inputs, in an append-only, hash-chained log.
  - C2's funding comes directly from **OKX's public funding endpoint**. C2 is a *feature*, not the label, so its
    eligibility does not depend on the blocked P0 feed-provenance gate. It needs only as-of availability, which the
    shadow log records.
- **Window:** 14 days, scored ≥ 7 days after its last row, with the pinned unmodified scorer on the
  validator-equivalent field.
- **Gates:** V Δshare > 0 in each of the two 7-day halves; V2 > R in both; FULL Δ ≥ +0.025; then the day bootstrap.
  Any feed-freeze rows are reported and excluded from promotion.
- **Memory:** a 14-day window is ≈ 20,160 rows: predicted peak **3.44 GiB at 258 peers, 4.15 GiB at 320**. It fits
  a ~5 GB host only if other jobs leave enough `MemAvailable`, and the harness guard enforces this.

Historical F2 (24.7% UID249 observer-blind) can still be run with the corrected assembler as an **exploratory** read.
It cannot promote anything.

## 6. Status

| Item | Status |
|---|---|
| R2-based MULTIXSEC assembler | built and tested; **not run on real shards** (Paris only) |
| Scoring-time hotkeys | script ready; not run (chain blocked here) |
| Historical V1/V2 | F1 withdrawn; F2 exploratory only |
| Prospective window | designed; needs the validator-equivalent observer (§5) |
| Production patch | **none**; no evidence of a paid-score improvement exists |

Nothing here needs a larger host yet. The 14-day forward window is predicted to fit Paris.
