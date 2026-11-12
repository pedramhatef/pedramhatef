# UID249: round 5. MULTIXSEC real-field run: preflight done here; assembly and scoring BLOCKED

Date 2026-11-12 UTC. No production, R2, wallet, collateral or miner configuration touched. No scorer run on real data.
**No patch proposed.**

## 1. Package integrity

| Check | Result |
|---|---|
| Bundle zip | sha256 `0202e790…`; 5/5 files OK against its SHA256SUMS |
| `multixsec_counterfactual.py`, `test_multixsec_counterfactual.py` in bundle | byte-identical to the versions I committed in round 4 |
| `test_builder.py` | **3/3 pass** |
| Pinned `xsec_rank.py` | sha256 `87c43106…`, matching the bundle README |

## 2. Exact blocker (stops assembly and scoring)

The builder's inputs live only on the Paris investigator: the historical and holdout 194-D field arrays, `CHECKPOINT.json`
mappings, `original_sha256_S64.npy`, the funding `sidx`/`sha256` arrays, and `P0_FABLE_CAPTURE_TIME_VALIDATED_20261112.npz`.
- This session runs in a separate cloud container. `/root/engineering` does not exist here, and this session has
  no access to Paris (no SSH or connector).
- So `--check-only`, the build and the scorer runs were **not executed**. There are no shares to report.
- Nothing was substituted: no synthetic stand-in field, no partial rows.

## 3. Preflight work that could be done here, against pinned source

**3a. Slice and asset order: PASS.** At every `config.py` commit from 2026-08-17 through 2026-11-06 (7 commits):
total width 194, MULTIXSEC = `[113:146]`, FUNDINGXSEC = `[146:166]`, and the 33-asset BREAKOUT order is unchanged
(order hash `94e79a74e6c8`). The builder's `113:146` is source-correct for the whole archive window, *provided the
observer packed vectors in config order* (Paris should confirm in its archive notes).

**3b. Memory: FAIL for a ~5 GB host, even with the unmodified scorer alone.**

Peak RSS was measured on the pinned scorer at the real length T = 32,805, A = 33, single-threaded BLAS.

| Peers H | Scorer alone (peak MB) | Harness, round 4 | Harness, round 5 (copy-free) |
|---|---|---|---|
| 16 | 811 | 1,030 | – |
| 32 | 1,123 | 1,539 | – |
| 64 | 1,751 | 2,564 | **1,781** |

The fit `peak ≈ 0.5 GiB + 4.6 · T·H·33·4 bytes` matches all three scorer measurements within 1%.

| Run | T | H = 258 | H = 320 |
|---|---|---|---|
| FULL | 32,805 | **≈ 5.6 GB** | ≈ 6.8 GB |
| F1 or F2 fold slice | 24,240 | ≈ 4.3 GB | ≈ 5.2 GB |

Runtime is about 0.8 s per peer per run (about 3.5 min per scorer call at 258 peers). The README's ~1.4 GB estimate
covers the input file only. The scorer itself builds a pooled transposed copy and then a masked copy, which is the
4.6× factor.

**Harness fix (mine, round 4 to 5):**
- Round 4 copied the full field per variant. It now swaps UID249's column **in place** and always restores it
  (`try/finally`), and passes a view to the scorer.
- Peak at H = 64 dropped from 2,564 to 1,781 MB, the scorer floor plus 2%.
- Default scoring is still bit-exact with a direct scorer call. A new test shows the input is restored exactly and
  the result equals the explicit-copy path.

**New fail-closed guard:** `multixsec_counterfactual.py` prints the predicted peak and **exits without running** if it
exceeds 85% of `MemAvailable`. No rows or peers are dropped to make it fit.

**3c. Field-membership fidelity issue in the builder (must be resolved before results count).**
- The pinned validator builds training data with `active_hotkeys = set(metagraph.hotkeys)` at scoring time
  (`validator.py` → `iter_challenge_training_data` → `_collect_hotkeys`). **Deregistered hotkeys are excluded.**
- `build_multixsec_input.py` uses the **union** of both archives' hotkeys. Since shares are normalised across peers,
  keeping hotkeys a validator would have dropped changes every share, UID249's included.
- Required input: the metagraph hotkey list at each fold's end block (F1 end, F2 end, FULL end). The builder should
  restrict columns to it and record the list's source and hash.
- This also lowers H, and with it the memory need.

**3d. Feed-freeze exposure (carried over).** F1 is scorer segment 3 = rows 16,060–24,000 of the Oct 10–Nov 1 window,
which covers Oct 22–25. If `freeze_audit` reports price-stale rows or all-33-zero-return label rows there, F1 is
contaminated like FUNDING fold 1. The verdict would then rest on F2 plus a fresh forward window.

## 4. Gates (unchanged, pre-registered in round 4)

Applied in this order once real numbers exist:
1. V1 and V2 Δshare > 0 in F1 and F2.
2. V2 − R > 0 in F1 and F2.
3. FULL Δ ≥ +0.025.

Only then run `--daily`. Even a full historical pass does not authorize deployment: funding provenance is still
0/20 verified, there is no forward window, and there is no paying-validator receipt.

## 5. What would unblock this (one request)

Run on a host with **≥ 7 GiB MemAvailable**: Paris if it can be freed, or another off-production machine you approve.
The ~5.6 GB need is the pinned scorer's own floor, and changing it would break source-nativeness. Inputs:
1. The existing archive paths.
2. The metagraph hotkey set at each fold end block (from the observer's metagraph snapshots, or an archive-node query).

Order: builder `--check-only` → restrict to active hotkeys → build → `pytest test_multixsec_counterfactual.py` →
harness, **without** `--daily`. Send back `INPUT_INTEGRITY.json`, the harness JSON (it includes the preflight line,
`freeze_audit`, and per-fold shares and ranks for baseline/V1/V2/R/C1), plus `/usr/bin/time -v` peak RSS.
