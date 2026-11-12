# UID249: round 4. P0 review, timestamp guard applied, MULTIXSEC harness. No reward claim.

Date 2026-11-12 UTC. Off-production only. Input bundle sha256 `196420b0…`; all 130 evidence files and all 4 patch files
verified against their SHA256SUMS. F1 and F2 were not retuned, and the frozen P0 thresholds are unchanged.

## 1. P0 timestamp guard: applied

- `FABLE_P0_ACTUAL_TIME_GUARD.patch` applied cleanly. The result is byte-identical to your corrected module, sha256 `fda34937…`.
- Your bug report is right. The post-settlement search took the first archive row at or after `tau + d` **without
  checking the row's actual time**, so an archive gap could match a sample hours late. The fix only removes
  out-of-deadline samples; no threshold moves.
- Tests: `test_p0_actual_delay_guard.py` 2/2, `test_p0_funding_provenance.py` 3/3, `test_f2_interval_safe.py` 9/9.

## 2. P0 results reviewed: 0/20 BLOCKED stands; the diagnostic points strongly to OKX

**Independent re-analysis of the raw evidence** (`p0_okx_stratified_diagnostic.py`, rules fixed in the file
before the first run, output `round4/P0_OKX_STRATIFIED_DIAGNOSTIC.json`):

| Observation | Value |
|---|---|
| OKX median archive/venue ratio, all 20 assets | **1.000** (Hyperliquid 1–8×; Binance 0.2–2×) |
| OKX `fundingRate` vs `realizedRate` in all saved history | identical at every event. My P0 code's realizedRate-first choice was **not** a cause of misses. |
| Settlements, stale (archive-only rule: 20-vector unchanged ≥30 min) | 10 of 68; **0** match all 20 assets |
| Settlements, fresh | 58; **32 match all 20 assets** |
| Fresh asset-events, **bit-for-bit equal** to OKX `fundingRate` | **795 / 1,160 (68.5%)** |
| within 1% but not exact / finite miss (~1e-6) / archive NaN | 177 / 178 / 10 |
| Finite misses equal to OKX's *previous* settlement (lagged copy) | 2 of 178 |
| Archive NaN rate | 1.15% for the first 10 assets, 0.01% for the last 10 |

**Reading.** Bit-for-bit equality on 795 asset-events across all 20 symbols is very hard to explain except by
**OKX USDT-SWAP being the upstream for all 20 assets**. The misses are small (~1e-6), not lagged copies, and
cluster at the 16:00 UTC settlement. Fresh settlements with at least one finite miss, by hour: **16:00 14/20**,
00:00 7/18, 08:00 5/20. That fits a **polled running estimate** sampled shortly before OKX's final value, and an
hour-dependent timing effect the owner can explain. This is a diagnostic, **not a verification**: the frozen rule (≥90% within 1%) fails, so all 20 stay
BLOCKED and **F2 stays ineligible**. The applied F2 module also fails closed for any non-Binance formula.

## 3. The funding freezes and the paying validator

**From pinned source (fact):** `validator.get_asset_prices` reads `prices` and `funding_rates` from
`latest_prices.json` with **no freshness or timestamp check**. `ledger.append_step` stores whatever it received.
- If the Oct 22 and Oct 23–25 freezes were in `latest_prices.json` itself, every validator running public code
  recorded the same frozen values, and FUNDINGXSEC labels were degenerate for them too (4,317 all-zero-change origins,
  each labelled class 0 on all 20 assets).
- If the freezes were only in the observer's capture, validators were unaffected.

**Discriminating test Paris can run now** (in `multixsec_counterfactual.py`, `freeze_audit`):
compare rows where the archived funding is stale while spot prices keep moving.
- Prices moving while funding is frozen **inside the same snapshot** → a price_service funding-fetch fallback
  (served to validators too).
- Both frozen → the whole feed or the observer stalled.
- It also counts MULTIXSEC label rows with all-33 zero 4h return. A whole-feed freeze would contaminate
  MULTIXSEC labels the same way.

**Owner request (only the owner can answer provenance; copy-paste):**

> Hi. Read-only question about the SN123 price feed (`latest_prices.json`, written by price_service.py). For FUNDING-XSEC:
> (1) per asset, which venue/contract supplies `funding_rates` (OKX USDT-SWAP for all 20?), and is it the live
> current-period rate or the last settled rate? (2) What is the poll interval, and what does the service publish when
> a venue fetch fails (last value kept, or asset omitted)? Is there a `last_updated` field per asset? (3) Did the feed
> serve unchanged funding values on 2026-10-22 ~06:00–19:30 UTC and 2026-10-23 19:45 → 10-25 23:37 UTC? Your
> validator would have stored them; `challenge_data` FUNDINGXSEC `price_data` for sidx 1867421–1868231 and
> 1869688–1872798 shows it. (4) Same for spot prices in that window. Thank you.

## 4. MULTIXSEC: harness built and verified on synthetic data; real-field run pending on Paris

Source facts (pinned `xsec_rank.py`, `ledger._build_xsec_from_db`):
- The label is a 4h forward return above the cross-sectional median of 33 assets, paired by **row position**.
- There is no stale-column gate.
- The walk-forward embargo is `LAG = 60` rows while the label spans 240 rows. This is symmetric across miners; noted, not exploited.
- Only rows older than `PAYLOAD_MATURITY_BLOCKS` are scored, so any change appears **≥7 days later**.

**Harness:** `multixsec_counterfactual.py`. It calls the pinned `xsec_rank.py` **unmodified**.

| Pre-registered element | Definition |
|---|---|
| Primary candidate **C2** | Funding crowding: `−tanh(robust_z(as-of funding level))` on the 20 assets with funding (OKX-upstream per §2). Exact 0 (missing) on the other 13. |
| Promotable variants (two) | **V1** = C2 replaces UID249's column. **V2** = 0.5·tanh(robust_z(current)) + 0.5·C2. |
| Control **R** (never promoted) | tanh(robust_z(current)) alone. Required because V2 contains this re-scaling. |
| Ablation **C1** (never promoted) | BTC-beta-residual 4h reversal from rows ≤ t. |
| Folds | F1 = rows[:24240] → scorer segment (16000, 16060, 24000) only. F2 = rows[8000:32240] → segment (24000, 24060, 32000) only. FULL = all rows. |
| Gates | Δshare > 0 in F1 and F2; for V2 also V2 − R > 0 in F1 and F2; FULL Δ ≥ +0.025; day-cluster bootstrap 95% lower bound > 0 (end-date runs overlap, so the CI is optimistic and labelled as such). |
| Freeze report | Price-stale rows, funding-stale rows, funding-stale-while-prices-move rows, all-33-zero-return label rows. |

Verification (`test_multixsec_counterfactual.py`, 5/5, against the real pinned scorer):
- **Causality:** V1, V2, R and C1 outputs for rows ≤ t are unchanged when the future is altered.
- **Bit-exact default:** harness scoring equals a direct scorer call exactly.
- **Segment replica** equals the segments the scorer actually scores (its own `_QUALITY_LOG`) for FULL, F1 and F2.
- **End-to-end CLI**, including `--daily`, runs.

**Two defects found by my own tests and fixed before handing over:**
1. My first F2 slice (`rows[8000:]`) included a partial 5th segment. It is now `rows[8000:32240]`, verified to
   isolate segment 4.
2. I claimed that dropping all-zero miner columns was exact. Measured on synthetic data: **not bit-exact**
   (max |Δshare| 3.1e-6, ranks identical). It is now opt-in (`--drop-zero-cols`, default off); the default path is
   bit-exact.

**Attribution caveat found on synthetic data:** with C2 as pure noise, V2 still beat baseline (.066 → .094) because the
re-scaling alone (R) gave .083. Hence the V2 > R gate. Whether re-scaling helps on the real field is unknown. If R
beats baseline in both real folds, that is a separate exploratory finding needing its own forward window, not a
result.

**No real-field number exists yet**: the 258-peer MULTIXSEC archive is on Paris. Command:

```
MANTIS_SRC=<ace548a checkout> python -m pytest -q test_multixsec_counterfactual.py
python -I multixsec_counterfactual.py --mantis <ace548a checkout> --npz <multixsec.npz> \
   --target 5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD --daily --out MULTIXSEC_RESULT.json
```

`multixsec.npz` keys:
- `prices` (T, 33): validator-feed spot prices, `config.BREAKOUT_ASSETS` order.
- `multixsec` (T, H, 33): miner vectors.
- `hotkeys` (H,), `capture_ts` (T,).
- `funding33` (T, 33): as-of funding mapped to BREAKOUT order, NaN for the 13 assets without funding.

Memory: T·H·33·4 bytes ≈ 1.1 GB at 258 miners, and the scorer copies its pooled matrix. If 5 GB is tight, use
`--drop-zero-cols` and report it.

**Also already known:** the MULTIXSEC label is exposed to the same feed-freeze risk.
- If price_stale_rows > 0 in Oct 22–25, F1 (segment 3: rows 16060–24000, which covers those dates) is contaminated in
  the same way as FUNDING fold 1.
- The verdict must then rest on F2 and a fresh forward window.

## 5. Deployment status

Nothing is deployable.

| Gate | FUNDING F2 | MULTIXSEC V1/V2 |
|---|---|---|
| Source provenance | BLOCKED (OKX strongly indicated, unverified) | Price feed: Polygon per old README, unverified; funding as F2 |
| Historical source-native | not run | harness ready; **not run** |
| Prospective shadow | not started | not started |
| Pilot | not eligible | not eligible |

**Smallest defensible production proposal: none.** The next step that could create one is the Paris MULTIXSEC run
above. If V1 or V2 clears every gate there, the next steps are:
1. A 14-day append-only shadow log of the candidate vectors.
2. Scoring after maturity on a field archive that keeps collecting.
3. Only then a one-function patch of the 33-element MULTIXSEC vector, with unchanged fallback on stale or missing
   funding (age > 15 min) and owner-approved restart.
