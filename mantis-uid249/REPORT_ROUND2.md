# UID249: round 2, after the independent F1 audit (2026-11-12)

Session: cloud container, not Paris. The proxy refuses chain RPC, drand, venue APIs, and both
`*.r2.dev` hosts (the owner datalog and the price feed). There is no validator access. Everything below
is source analysis or tooling verified on fixtures. **No new real-data result is claimed.**

## 1. F1: rejection accepted

The Paris real-field run (capped-at-3: 8.552339% → 8.224857%, −0.327 pp, still rank 3; uncapped fails
coverage) supersedes my synthetic mechanism evidence. F1 is closed; I will not retune it on the
inspected period. The `pairing by sidx + 60-sidx settlement buffer` correction you applied is right:
my gate paired by row position.

## 2. Validator receipt (Issue A): what the corrected timeline implies

**Fact (your correction):** archive rows are captured about 1 minute apart (sidx 1880130 at 01:49:53 non-zero;
1880131 at 01:50:52 zero). Peers are **non-zero at capture time**.

**Consequence (from source):** the public validator path cannot produce non-zero embeddings until
`PAYLOAD_MATURITY_BLOCKS` (default 50,400 ≈ 7d) after the row. A live-captured archive with non-zero
peers therefore did **not** come from that path. Only two mechanisms fit:

| Archive decrypts via | Lock length matters? | What UID249's zero from 01:50:52 means |
|---|---|---|
| drand, shortly after the round (anyone can do this) | **Yes**: a 48h lock is unreadable at capture | Expected artifact. Says nothing about validator credit. |
| owner key `W_owner` (only the subnet owner holds it; the guide says owner settlement reads it live) | **No** | A real failure of UID249's payload on the owner path. Then check whether the producer change at the switch also changed `owner_pk`, binding or plaintext. |

There is no owner-side decrypt or export code in public source (only the owner's public key), so the
source cannot say which one built the archive. **Who publishes those R2 shards is the deciding fact.**

**Side effect that matters for every replay:** if the archive is drand-gated, it shows **every
long-lock miner as all-zero**. The "258-hotkey full field" would then be missing exactly the
miners that follow the source's suggested 7-day lock (`TLOCK_PROD_SUGGESTED_LOCK_SECONDS=604800`).
UID249's rank 3 and every counterfactual share, F1 included, would be computed on an incomplete
field.

### Discriminating tests Paris can run now, with data it already has (read-only)

- **T1 Provenance.** Identify the publisher of the R2 shard bucket and its capture code. Done if it is identified.
- **T2 Blind-spot test.** For each hotkey in the archive window, take its archive non-zero fraction per challenge
  and its on-chain incentive from your metagraph snapshots.
  - Drand-gated signature: several miners with material incentive (≥ the UID249 level) that are all-zero for the whole archive.
  - If found, the archive is blind to long locks, those miners are paid anyway, so a 48h/7d lock is scored, and the full-field replays need re-weighting.
- **T3 Chain differential.** Daily UID249 incentive, Oct 24 → Nov 12+.
  - The first post-switch rows mature about Nov 7 02:00 UTC. If validators were dropping them, ETHLBFGS, FUNDINGXSEC and BREAKOUT salience would decay from then as walk-forward segments roll.
  - A flat series through Nov 7–12 is (weak) evidence of ingestion. A step-down starting Nov 7–9 is evidence of loss.
- **T4 Owner datalog probe** (new tool below). Run it only if the file has been republished since Nov 7 02:00 UTC.

### New tool: `validator_datalog_range_probe.py` (sha256 recorded in `SHA256SUMS.txt`)

- Reads the owner-published validator datalog (`config.DATALOG_ARCHIVE_URL`) **over HTTP Range only**, with a hard byte budget (64 MiB default).
- `If-Match` on the ETag ensures it never mixes file versions.
- It refuses to run when Last-Modified < 2026-11-07T02:00Z, because such a file cannot contain matured rows.
- For each ticker and sidx it reports whether UID249 and named peers are listed and non-zero in the validator's own `challenge_data` (stages 2–5).
- It does primary-key seeks on `raw_payloads(ts, hotkey)` for pending UID249 payloads (stages 1–2: round, hk/owner_pk match, length). It never prints ciphertext.

Verified on an 83 MB fixture built from the source schema and packing, with a known answer of UID249 zero from 1880131:
- correct per-row answers;
- **1.5 MB fetched in 24 requests**;
- stale-file refusal works;
- budget abort works.

The first version scanned a `raw_payloads` ts-range and fetched 46.7 MB. I replaced that with key seeks.

```
pip install apsw
python -I validator_datalog_range_probe.py --hotkey 5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD \
  --peer 5Eq8YZzyhUATm9wrU1HyynJyykRmAUKYrgkGjsist4EX2bb1 \
  --sidx 1880125:1880140 --sidx 1879000:1879000 --sidx 1884000:1884000 --out probe_result.json
```

Caveats:
- This is the owner's validator, if that is who publishes the file. It is one validator, not the stake-weighted consensus.
- The handover said Last-Modified was Oct 30. If it is still stale, the probe exits with `STALE` and T4 falls to the operator request.

### Evidence request for a validator operator (if T4 is stale)

Small, read-only, no secrets. Each item maps to a stage:

> Hi. I mine SN123 UID249 (hotkey 5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD). On Oct 31 ~01:50 UTC I moved to a
> 48h drand lock and want to confirm your validator still credits me. Could you run these read-only commands and paste
> the output (no keys or ciphertext are printed)?
>
> ```
> cd <mantis checkout>; git rev-parse HEAD; echo "PAYLOAD_MATURITY_BLOCKS=${PAYLOAD_MATURITY_BLOCKS:-unset}"
> sqlite3 -readonly <storage>/mantis_datalog.db "
>  SELECT ticker, sidx, instr(hotkeys,'5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD')>0 AS listed, length(embeddings)
>  FROM challenge_data WHERE ticker IN ('ETHLBFGS','FUNDINGXSEC') AND sidx IN (1880130,1880131,1880200,1881000);
>  SELECT b.block, json_extract(rp.payload,'$.round'), length(rp.payload) FROM raw_payloads rp JOIN blocks b ON rp.ts=b.idx
>  WHERE rp.hotkey='5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD' ORDER BY rp.ts DESC LIMIT 3;"
> grep -E "Payload decryption failures|Retained .* matured payloads" <validator log> | tail -5
> tail -1 <per-challenge log> | python3 -c "import json,sys; d=json.loads(sys.stdin.readline()); \
>  print(d['block'], {t: v['salience'].get('5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD', 0) for t, v in d['per_challenge'].items()})"
> ```
> Thanks.

Mapping:
- `listed` + `length` at 1880131 → stages 2–5 for the switch row (listed alone is not proof of non-zero; the embedding blob is).
- `raw_payloads` → stage 1 fetch for current rows.
- The decrypt-failure lines → stage 4.
- The per-challenge line → stage 6 (the actual paid salience per challenge, before burn and EMA).

## 3. Next predictive candidate: F2, mechanical funding projection. Status: DESIGN (no result)

**Why FUNDINGXSEC and not MULTIBREAKOUT:** the FUNDING label is computable exactly from a public
feed the miner can poll. `validator.get_asset_prices` reads `funding_rates` from the public
`config.PRICE_DATA_URL` (`latest_prices.json`). MULTIBREAKOUT needs validator trigger and pending-state
parity, which earlier work showed is hard to prove.

**Feature.** Venue funding is `F = avgP + clamp(I − avgP, ±c)` over each interval's premium index.
- The value the feed shows at t+8h comes from a window that is partly observed by t and partly in the future.
- The future part is projected with an AR(1) fitted on **premium history only**.
- `x_a = F̂(t+8h) − F̂(t)`; submit `tanh((x_a − median)/(1.4826·MAD))`.
- **No parameter is fitted to labels.**
- The feature is not in the guide's list of suggested features (levels, OI, momentum, liquidations, BTC lead-lag). Whether peers already carry it is what the full-field counterfactual measures.

Reference implementation: `funding_premium_projection.py`. Self-test passes:
- causal at t;
- clamp formula exact;
- a fully observed settled window is exact;
- a premium jump inside the current interval gives a positive projected change;
- outputs in [−1, 1] with no exact zeros (exact zero means "missing" to the scorer).

**Pre-registered protocol** (frozen before any label is examined):

| Step | Gate |
|---|---|
| **P0 provenance** (archive funding values only, no labels) | (a) Venue identification: archived `funding_rates` match one venue's public history (Binance `premiumIndexKlines` + `fundingRate`, Bybit, or Hyperliquid) with per-asset corr ≥ .99 and timing aligned. (b) Feed semantics: minute-to-minute variation means `estimated`; steps only at settlements means `settled`. (c) Per-symbol interval (8h/4h/1h), interest and cap. (d) The production host can reach that venue's public endpoint (geo-blocking check). **Freeze the `Venue` parameters, then stop if any of (a)–(d) fails.** |
| **P1 historical folds** (Oct 10–Nov 1 archive, labelled *historical-exploratory*: the period was inspected for F1 and other candidates, though never for this feature) | UID249's FUNDING column replaced by F2, other 257 columns unchanged, pinned native scorer. Fold 1 = Oct 10–21, fold 2 = Oct 22–Nov 1 (UTC days). Two pre-registered variants only: **A** replace, **B** equal-weight blend of the cross-sectionally standardized current output and F2. Both folds must be positive for the chosen variant. |
| **P1 uncertainty** | Recompute baseline and candidate share at each daily scorer end-date across the window (the validator recomputes every 1000 blocks). Paired day-clustered bootstrap of the share difference; 95% lower bound > 0. |
| **P1 effect size** | Full-field FUNDING share **≥ +2.5 pp absolute**; other challenge columns byte-identical (no other-challenge regression is possible except through global normalization, which must be reported). |
| **P1 field completeness** | Re-state the result under T2's finding. If the archive is blind to long-lock miners, report the share both as computed and with a sensitivity case that adds the T2-identified missing incentive mass. |
| **P2 untouched forward** | Shadow only. Production is unchanged. Paris computes F2 live from public data from **2026-11-13 00:00 UTC**. Outputs go to an append-only hash-chained log (pre-commitment). Score after labels resolve and the field archive covers the window; evaluate **Nov 13–26**. **Dependency:** the field archive must keep collecting past Nov 6, otherwise forward full-field scoring is impossible. |
| **P3 pilot (owner approval required)** | Only if P1 and P2 pass. The patch is one function replacing the FUNDINGXSEC vector before encryption. Rollback = current run.py sha `633d2377…`. Producer telemetry: feed fetch age, venue fetch age, NaN/zero counts, output std per asset. Abort on any stale or zero output. There is no validator feedback for ≥7 days. |
| **P4 income** | Attribution only from the validator per-challenge salience line (the evidence request above) or ≥14-day on-chain incentive versus the pre-pilot trend. A backtest number is never reported as income. |

**Rejection criteria (any one):** P0 venue mismatch; either fold ≤ 0; bootstrap lower bound ≤ 0; share gain < 2.5 pp;
forward window ≤ 0. If P1 passes but P2 fails, the P1 gain is treated as overfit to the inspected period.

## 4. What I ran this session

- Pinned source still `ace548a`, with no newer public commits.
- Read the validator price/funding fetch, the encryptor, and the guides. There is no owner-key decrypt code in public source.
- Built and fixture-tested the Range probe (3 scenarios).
- Built and self-tested the F2 reference implementation (5 checks).

The first version of each test found a defect (a probe VFS class that failed to open; a mis-placed jump in the F2 test). Both are fixed and shown above.
