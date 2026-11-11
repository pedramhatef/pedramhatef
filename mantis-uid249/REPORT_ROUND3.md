# UID249: round 3. F2 interval-safe patch applied; P0 provenance BLOCKED in this session

Date 2026-11-12 UTC. Experimental branch only. Production miner, wallets, timelock, collateral and R2 were not touched.
F1 was not revisited.

## 1. Patch applied (verified)

| Item | SHA256 |
|---|---|
| Uploaded bundle zip | `67c27be2065b51791cc0dd121175415376583aca2f5e4cd658b6112b510f8be7` |
| `FABLE_F2_INTERVAL_SAFE.patch` (bundle SHA256SUMS: OK) | `27296e3528f9acc9f12fbd3f288e8de1d1fab44124c4d3012c9ad4158d8b8267` |
| `funding_premium_projection.py` after `git apply` (byte-identical to the shipped `funding_premium_projection_interval_safe.py`) | `4d1b22e9b1e0fe8a1312223b86b2afe567fd4d38b8f80ed23aa57fcbe36bdfde` |
| `test_f2_interval_safe.py` (unmodified) | `929d11a9216d2870d9924f1cc2628139d02f04e08054fc3cc318319061056747` |
| `funding_provenance_template.json` (unmodified; still all null) | `1de86588c14b6f347d342da45074a85886b0736aa1d8f9868430354060df4d99` |

- `funding_premium_projection_interval_safe.py` in this folder is a two-line import shim, so the shipped test runs unmodified.
- `pytest`: **9/9 passed** here.
- I could not independently verify the Binance 2025-09-18 announcement, because the network is blocked here. The patch's formula is taken on the bundle's authority.

## 2. P0 provenance: what was established, and the blocker

**New evidence found (public MANTIS git history, all 226 commits searched):**
- `README.md` from commit `d595b18` (2026-04-15) until commit `d453933` (2026-08-17) listed: *"`price_service.py` | Fetches spot prices (Polygon) + funding rates (**OKX/HL/CoinGlass**), uploads to R2"*.
- The line was removed in a bulk upload; no change of source is stated.
- `price_service.py` itself was never committed.
- GitHub code search finds the feed URL only in copies of `config.py` and in unrelated miner repos, never in a producer.

**Consequences:**
- The owner's own documentation names **OKX, Hyperliquid and CoinGlass, not Binance**.
- The applied module fails closed unless `formula == 'binance_post_2025_09_18'`. If P0 confirms OKX or HL for an asset, **F2 as applied is ineligible for that asset**. It would need a venue-specific adapter, pre-registered and tested before any label is examined.
- If an asset is a CoinGlass aggregate (paid API, so not queried), there is no single premium formula to project, and F2 is ineligible for it.

**Per-asset provenance table: all 20 BLOCKED (not run).**

| Asset(s) | Venue | Symbol | Semantics | Interval / offset | Interest / clamp / cap | Premium sampling | Status |
|---|---|---|---|---|---|---|---|
| BTC ETH SOL XRP DOGE ADA AVAX LINK DOT SUI NEAR AAVE UNI LTC HBAR PEPE TRX SHIB TAO ONDO | prior only: OKX / HL / CoinGlass (README, Apr–Aug 2026) | unverified | unverified (Paris: BTC/ETH/SOL change at minute cadence, which suggests an estimate) | unverified | unverified | unverified | **BLOCKED** |

**Exact blocker.** P0 needs two things together: (a) the archived feed values (sidx, capture time, 20 funding columns), and (b) read-only access to the venues' public settlement histories. This session has neither:
- The archive is on Paris.
- The proxy here refuses `www.okx.com`, `api.hyperliquid.xyz`, `fapi.binance.com` and both MANTIS `r2.dev` hosts.

The feed itself (`latest_prices.json`) returns HTTP 403 at origin to Paris as well. Historical provenance therefore has to come from the archived values matched against venue settlement history.

**F2 is NOT eligible. P1 was not run. No production patch is proposed.**

## 3. Ready-to-run P0 tool (for Paris)

`p0_funding_provenance.py` (with known-answer tests in `test_p0_funding_provenance.py`, 3/3 passing):

- **Phase A (offline):** a fingerprint of the archived feed values per asset.
  - It reports modal values, change frequency, the UTC hour and minute-of-hour of changes (a settlement-clock signature) and magnitude class (hourly ~1e-5 vs 8h ~1e-4).
  - **No forward pairing, no labels, no scores.**
- **Phase B (read-only public, no key):**
  - Venues queried: OKX `public/funding-rate-history` + `public/funding-rate` (which returns venue formula metadata), Hyperliquid `info fundingHistory` (coins `kPEPE`/`kSHIB`), and Binance `fapi/v1/fundingRate` as a **control** only.
  - Every response is saved under `raw/<sha256>.json` with its URL and capture time.
- **Pre-registered rule, fixed before any real run:**
  - ≥30 settlements, r ≥ .99, |median ratio − 1| ≤ .02, and ≥90% within 1% relative error.
  - Semantics: "estimated" if the archive matches just *before* settlement; "settled" if it matches just *after* (publication delay ≤ 30 min, measured).
  - One passing venue → VERIFIED. Two → AMBIGUOUS. None → BLOCKED (possible aggregate).
  - It never substitutes or synthesizes a feed.
- **Known-answer test:**
  - OKX-like 8h running estimate → VERIFIED okx/estimated.
  - HL-like hourly last-settled with a 2-min delay → VERIFIED hyperliquid/settled, delay 120 s recovered.
  - 50/50 aggregate → BLOCKED.
  - Identical twin venues → AMBIGUOUS.
  - Phase A makes no venue call.
- **One test defect (in the fixture, not the logic):** my first fixture had 8 days = 24 eight-hour settlements, below the rule's 30. I lengthened the fixture to 12 days rather than loosening the rule.

```
pip install numpy pytest
python -m pytest -q test_p0_funding_provenance.py test_f2_interval_safe.py
python -I p0_funding_provenance.py --npz '<archive>/*.npz' \
   --funding-key <funding array key> --time-key <capture time key> --sidx-key <sidx key> --out p0_out/
```

What P0 still cannot give, even if the run succeeds:
- Contract clamp, cap, interest exceptions and premium sampling frequency are not in settlement history. They must come from venue specs. OKX's `public/funding-rate` returns some of them; the HL ones come from its docs, recorded with URL, capture time and hash.
- A CoinGlass-aggregated asset stays BLOCKED unless the owner approves a paid key.

## 4. Gates, unchanged and pending

| Gate | Status |
|---|---|
| P0 provenance (20 assets) | **BLOCKED** (needs the Paris run above) |
| Venue adapter(s) if the venue ≠ Binance | Not started; must be frozen and tested before P1 |
| P1 historical (two frozen variants, two folds, day-block CI, ≥ +0.025) | Not run |
| P2 prospective shadow | Not started |
| P3 production pilot | Not eligible |

Parity statement: the observer archive is source-native **only for the observed set** (possible long-lock blind spot, as in round 2). Paying-validator maturity and runtime remain unauthenticated.
