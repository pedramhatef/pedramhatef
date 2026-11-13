# SN89 LF ("Signals") barrier backtest harness

A stdlib-only (Python 3.10+, no numpy, no repo imports) harness that grades your model's
directional calls exactly the way SN89's validator grades an LF call, using Hyperliquid
candles. It has been checked against **27,837 real, officially graded SN89 calls** from the
owner's public checkpoint. It also measures today's wash rate per pair, which tells you how
fast each pair can get you qualified.

Everything is read-only. The only network call is the public Hyperliquid `info` endpoint.

| file | what it does |
|---|---|
| `sn89_bands.json` | The current board, `hluniverse-20261007` (in force since 2026-10-07T00:00Z, copied 2026-11-13 from repo HEAD 2e48ae8). It has 50 pairs, each with TP = SL bps, class, horizon, HL coin and feed fidelity. |
| `hl_data.py` | Fetches and caches HL candles at any interval. It pages around the 5,000-candle cap and maps SN89 pairs to HL coins. |
| `sn89_label.py` | The grader: labels one call WON / LOST / WASHED from candles, and implements the FX-week session calendar. |
| `sn89_eval.py` | `eval`: your signals CSV goes through the quota filter, labels, stats, a causal qualification and tally simulation, a probability sweep and calibration. `baseline`: random-call wash and P(TP) per pair. `validate`: re-grades the public checkpoint against the official status. |
| `test_sn89_label.py` | 24 unit tests, including the Brownian-drift closed-form test. Run them with `python test_sn89_label.py`. |

## Quick start

```bash
cd sn89-kit/backtest
python test_sn89_label.py                                   # 24 tests, ~10 s
python hl_data.py pairs --check                             # mapping + live listing check
python hl_data.py fetch --pairs board --intervals 1m,3m,5m,15m,1h --days 60
python sn89_eval.py baseline --days 7  --interval 3m  --fetch   # today's wash table
python sn89_eval.py baseline --days 30 --interval 15m --fetch
python sn89_eval.py eval --signals fable_calls.csv --fetch \
       --sweep 0.50:0.80:0.02 --oos-from 2026-10-15 --labels-out labels.csv
python sn89_label.py --pair BTCUSD --direction SHORT --t0 2026-11-10T14:05:00Z
python sn89_eval.py validate --checkpoint sn89_checkpoint.json --fetch   # re-check fidelity
```

Signals CSV (header required): `t0_unix,trade_pair,direction[,prob]`.
* `t0` can be epoch seconds or ms, or ISO-8601 UTC. It is the time the call lands on chain.
* `direction` is LONG/SHORT (1/-1 and BUY/SELL are also accepted).
* `prob` is the model's calibrated **P(TP first | decisive) for the SN89 band**. Do not use
  P(up) here.

**HL retention.** HL keeps only about the last 5,000 candles per interval: 1m ≈ 3.5 d,
3m ≈ 10 d, 5m ≈ 17 d, 15m ≈ 52 d, 30m ≈ 104 d, 1h ≈ 208 d. Run `hl_data.py fetch` on a timer
(1m every 2 days) to build your own fine archive. `eval` picks the finest cached interval
that covers each call. The cache CSV format is `t,T,o,h,l,c,v,n` with times in epoch ms, so
other candle sources can be used as well. For example, you can drop Binance-perp 1m klines
in as `BTC__1m.csv` to backtest years of crypto. That is a different venue, so the basis
moves the labels a little.

## The SN89 rules this implements (repo HEAD 2e48ae8)

**Grading** (`grader.py::_grade_touch_ticks`, for t0 ≥ 2026-07-25):
* Entry is the last tick at or before t0.
* TP and SL sit ±band from entry.
* SN89 walks every tick in (t0, t0+H] and tests SL before TP on each tick. A level scores
  once **2 ticks** have touched it, counted cumulatively.
* If neither level scores, the call is WASHED.
* H is 8 h for crypto, equities, indices and commodities, and 12 h for forex and metals.
  Every official wash in the checkpoint resolves at exactly t0+8 h (crypto, equities,
  commodities, indices) or t0+12 h (forex, metals), across 10,347 washes.

**Validity** (`scoring.apply_validity_filters`):
* Session-bound classes (forex, metals, equities, indices, WTI) are refused while the FX
  week is closed (Sun 17:00 → Fri 17:00 New York). They are voided when less than 25 % of
  the horizon is open market. In practice this means no equity or index call after about
  Fri 15:00 NY, and none at the weekend.
* Calls need at least 3600 s − 30 s since the last accepted call.
* At most 3 accepted calls per UTC day.
* Voided calls use no quota.

**Scoring** (`scoring.qualified_wins`, `decayed_qwin_tally`):
* A win is **qualified** when, judged causally at its t0, the decisive rows resolved by then
  plus the win itself meet the bar. The rows come from the trailing 60 d, capped at the 200
  most recent. The bar is n ≥ 8 and a Wilson LB (z = 1.2816) of at least 0.50.
* Wins during warmup (8 d from first seen) never pay.
* value = max(1, ramp(shrunk)) × efficiency
  * shrunk = (w+6)/(n+12)
  * the ramp passes through (0.55, 1.0), (0.60, 1.2) and (0.70, 2.0)
  * efficiency = clamp(1 − 0.6·(washes+16)/(graded+40), 0.55, 1)
* tally = Σ over the 20 most recent paid wins younger than 30 d of value × (1 − age/30 d).

Minimum wins needed to qualify (Wilson LB at least 0.5, z = 1.2816):

| decisive n | 8 | 10 | 12 | 15 | 20 | 30 | 40 | 60 | 100 | 150 | 200 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| wins needed | 6 | 8 | 9 | 10 | 13 | 19 | 25 | 35 | 57 | 83 | 110 |
| hit rate | .750 | .800 | .750 | .667 | .650 | .633 | .625 | .583 | .570 | .553 | .550 |

## How candles approximate ticks (defaults chosen by measured agreement)

1. **Entry** `entry="interp"`: interpolate at t0 between the previous candle close and the
   close of the candle that contains t0 (Brownian-bridge expectation). This reads one close
   after t0. That is fine for an outcome label, but never feed it to the model. A stale
   entry (`prev_close`) gives momentum-timed calls a fake edge on coarse candles. Over HL-era
   official calls, our hit rate came out **+1.4 pp** too high on 15m candles and **+1.7 pp**
   too high on 30m. `interp` cuts that to +0.7 / +0.8 pp and keeps it under 0.6 pp on 1m–5m.
2. **First (partial) candle**: only its close is tested, because its high and low may come
   from before t0.
3. **Last candle** is included.
4. **The 2-tick rule.** A single touching candle scores only if one of these holds:
   * its extreme passes the level by at least 2 % of the band, or
   * it closes beyond the level, or
   * the same level was already touched by an earlier candle.

   With this setting our crypto wash rate comes out within ±0.5 pp of the official one.
5. **Same-candle TP+SL** is resolved as LOST and flagged `ambiguous`. That is
   conservative: SN89 tests SL first. In practice it is rare: 0.1 % of calls on ≤5m
   candles and 2.5 % on 1h.

## Fidelity: re-grading real SN89 calls

**Method.** Every resolved checkpoint signal (won/lost/washed with plaintext) on a pair HL
carries was re-graded with the finest HL interval covering its window. It was graded with its
**own committed tp/sl**, not today's board, and the result was compared with the official
status. Script: `sn89_eval.py validate`.

**Headline.** This covers the HL-graded era, t0 ≥ 2026-08-21, when SN89 records crypto from
HL and has carried the HIP-3 equities since 2026-10-07. It counts crypto and HIP-3 pairs whose
finest interval is ≤ 5m: **n = 8,082, 3-way agreement 97.2 %, W/L agreement 98.9 %** among
the 5,309 calls both sides call decisive.

* Official wash 33.25 %, ours 33.30 %.
* Official hit rate 52.90 %, ours 52.87 %.

```
             ours:won  ours:lost  ours:washed
official won     2786         29          39
official lost      31       2463          47
official washed    33         49        2605
```

**By interval.** These are the same 1,508 calls (HL era, 1m-covered), graded at each
interval. Use ≤ 5m when you can.

| interval | 1m | 3m | 5m | 15m | 30m | 1h |
|---|---|---|---|---|---|---|
| 3-way agreement | 97.3 % | 95.9 % | 95.6 % | 92.8 % | 91.1 % | 87.9 % |
| W/L agreement (both decisive) | 98.7 % | 98.2 % | 97.9 % | 95.9 % | 94.0 % | 92.5 % |
| our wash (official 20.4 %) | 20.5 | 21.0 | 21.2 | 21.6 | 21.4 | 20.2 |
| ambiguous | 0.0 % | 0.1 % | 0.1 % | 0.3 % | 0.7 % | 2.5 % |

On the full HL-era sets where coarse candles are the finest available:
* 15m (n = 8,038): 93.9 % agreement, 97.3 % W/L.
* 30m (n = 3,454): 91.1 % agreement, 94.8 % W/L.

**Per pair, ≤5m** (3-way / W/L agreement):

| pairs | 3-way | W/L | n |
|---|---|---|---|
| BTC | 97.7 % | 99.4 % | 663 |
| TAO | 96.5 % | 98.9 % | 798 |
| ETH | 98.4 % | 100 % | 188 |
| SOL | 95.7 % | 97.9 % | 235 |
| XRP | 97.9 % | 99.4 % | 235 |
| HYPE | 96.7 % | 99.5 % | 270 |
| other alts | 95–100 % | 97.5–100 % | — |

| HIP-3 market | 3-way | W/L | n |
|---|---|---|---|
| NVDA | 96.6 % | 97.4 % | 116 |
| TSLA | 96.5 % | 97.3 % | 115 |
| SNDK | 98.2 % | 98.2 % | 110 |
| META | 97.7 % | 97.3 % | 88 |
| MU | 96.4 % | 97.4 % | 84 |
| SP500 | 94.7 % | 94.7 % | 38 |
| WTI | 100 % | 100 % | 30 |

Proxies:

| pair | HL proxy | 3-way | W/L | n |
|---|---|---|---|---|
| XAUUSD | xyz:GOLD | 97.7 % | 100 % | 398 |
| XAGUSD | xyz:SILVER | 100 % | 100 % | 57 |
| EURUSD | xyz:EUR | 92.7 % | 94.5 % | 480 |

EURUSD is the weakest because xyz:EUR is thin: half of its 1m candles have no trades.

AUDUSD and USDCAD **cannot be graded**, because no HL dex lists AUD or CAD.

**Older regimes use a different substrate, so treat them only as a sanity check:**
* 2026-07-25 → 08-21, crypto graded on Polygon ticks: 93.2 % on 30m/1h candles.
* Before 2026-07-25, the rule was a 1m close cross: 87.7 % on 1h candles. Our touch rule
  under-washes there (42.8 % vs 45.6 %), as expected.

**Why the remaining disagreements happen** (HL era, ≤5m, n = 8,082):
* **Level-edge cases.** 1.1 % are official-decisive / ours-wash, and 1.0 % are the reverse.
  * Where the official result was decisive and ours a wash, the HL candles reached a median
    0.99× of the band, and 97.7 % reached at least 0.9×.
  * Where the official result was a wash and ours decisive, our extreme went a median 3.5 %
    past the level, and 73 % of cases were within 5 % of the band.

  These are marginal touches. SN89's tick line mixes HL trades with book mids, and our
  candles are trades only. The 2-tick rule adds to the difference.
* **Flips** (official WON vs our LOST or the reverse) are 0.74 %. Only 1 of the 60 is a
  same-candle ambiguity. The official and our exit times differ by hours (IQR −109 to
  +42 min), so a near-touch in one series sent the call the other way later. Candle ordering
  is not the cause.
* **Entry.** For agreeing decisive calls at 1m, the entry implied by the official exit candle
  matches ours in 86 % of cases. The 90th percentile gap is 1.4 bps and the 95th is 5.1 bps.
  This is negligible next to bands of 50–330 bps.
* **Exit timing.** At 1m, the official exit is a median 0.55 min after our exit candle opens,
  and 84 % fall within that candle ±1 min. So we are scoring the same event.

**Synthetic check** (`SyntheticDrift` tests plus a pooled run). Simulated paths are
arithmetic Brownian motion on a 10 s grid with band 100 bps, σ = 40 bps/√h and H = 8 h. They
were aggregated to candles and labelled with this code (5 seeds × 3,000 paths per case):

| target P(TP\|decisive) | 0.50 | 0.60 | 0.60 (5m) | 0.60 (15m) | 0.65 | 0.75 |
|---|---|---|---|---|---|---|
| labeller, 1m unless noted | 0.505 | 0.608 | 0.608 | 0.597 | 0.655 | 0.757 |

Every case is within 1.7 SE of the closed form 1/(1+e^(−2μb/σ²)). The labeller is also
exactly mirror-symmetric: a mirrored driftless set (10 seeds, n = 14,290) gave 0.5013 and
0.4987.

## Live wash: random-time calls with the current board

Run with `sn89_eval.py baseline`:
* Calls every 10 min, both directions.
* Session-bound pairs are included only when SN89 would accept the call.
* Two windows: 7 d on 3m candles and 30 d on 15m candles, ending 2026-11-13 ~00:20 UTC.
  15m under-states wash by 0.7 pp on average (largest gap 2.5 pp) compared with 3m.
* σ_real is the std of 1h log returns (open sessions only for session-bound pairs), in bps/√h.
* σ_impl is the σ at which a driftless BM would wash 40 % (the board's target):
  band/(1.0322·√H).
* dec/day = 3 × (1 − wash at 30 d).
* "Official" is the checkpoint's wash for all miners on that pair in the same window.

**Fast** (wash below about 20 %, so 2.4–3.0 decisive calls per day):

| pair | band | σ_real / σ_impl | wash 7d | wash 30d | official 7d / 30d | P(TP\|dec) L / S, 30d | dec/day |
|---|---|---|---|---|---|---|---|
| SNDKUSD | 127 | 131 / 44 | 0.000 | 0.009 | 0.00 / 0.00 | .45 / .54 | 2.97 |
| MUUSD | 102 | 108 / 35 | 0.000 | 0.021 | 0.00 / 0.08 | .44 / .56 | 2.94 |
| EWYUSD | 90 | 72 / 31 | 0.009 | 0.033 | 0.00 / 0.04 | .47 / .53 | 2.90 |
| AMDUSD | 114 | 114 / 39 | 0.011 | 0.041 | 0.00 / 0.03 | .43 / .57 | 2.88 |
| MRVLUSD | 141 | 126 / 48 | 0.000 | 0.045 | 0.00 / 0.02 | .41 / .58 | 2.86 |
| TSLAUSD | 65 | 75 / 22 | 0.064 | 0.057 | 0.00 / 0.04 | .50 / .49 | 2.83 |
| NVDAUSD | 62 | 71 / 21 | 0.000 | 0.068 | 0.00 / 0.02 | .40 / .60 | 2.80 |
| INTCUSD | 164 | 131 / 56 | 0.038 | 0.097 | 0.00 / 0.07 | .40 / .60 | 2.71 |
| XYZ100USD | 43 | 36 / 15 | 0.041 | 0.102 | 0.00 / 0.07 | .40 / .60 | 2.69 |
| EURUSD (proxy) | 17 | 7.7 / 4.8 | 0.086 | 0.153 | 0.08 / 0.16 | .46 / .54 | 2.54 |
| KPEPEUSD | 203 | 125 / 70 | 0.159 | 0.185 | 0.10 / 0.16 | .45 / .55 | 2.44 |
| SP500USD | 26 | 21 / 8.9 | 0.106 | 0.186 | 0.00 / 0.02 | .43 / .57 | 2.44 |

**Middle** (wash 0.24–0.40, so 1.8–2.3 decisive calls per day):

| pair | wash 30d | pair | wash 30d | pair | wash 30d |
|---|---|---|---|---|---|
| MSTR | .239 | COIN | .240 | META | .249 |
| AVAX | .264 | CRCL | .266 | AAVE | .269 |
| WTI | .269 | BCH | .271 | TAO | .285 |
| LTC | .305 | NEAR | .313 | DOT | .319 |
| XRP | .331 | ZRO | .332 | LINK | .339 |
| SUI | .356 | ZEC | .373 | DOGE | .382 |
| SOL | .383 | ENA | .384 | BNB | .384 |
| PUMP | .396 | | | | |

**Slow** (wash 0.40–0.61, so 1.2–1.8 decisive calls per day):

| pair | wash 30d | pair | wash 30d | pair | wash 30d |
|---|---|---|---|---|---|
| HYPE | .407 | ALGO | .429 | WLD | .432 |
| XMR | .482 | ETH | .485 | UNI | .492 |
| XAG | .498 | XAU | .501 | ARB | .515 |
| ADA | .521 | ASTER | .529 | BTC | .539 |
| TRX | .549 | CRV | .613 | | |

Over the last 7 days, BTC washed .542 and ETH .568. The full tables are printed by the two
`baseline` runs above.

**Reading the live table:**
* **Equity and index bands in the fast group are far too narrow for current volatility.**
  σ_real/σ_impl runs 2.3–3.4× over 30 d (NVDA 5.2× over 7 d), so they almost never wash.
  They are the fastest route to 8+ decisive calls.
* **That will not last.** These bands are marked *provisional* in the repo. The recalibrator
  targets 40 % wash and can move a band by at most 20 % per cycle, so expect them to widen
  over the coming cycles.
* **Equities do not remove the need for edge.** The field's official hit rate on these pairs
  over the last 30 d is 0.34–0.58, so the pairs give no edge for free.
* **They are session-bound:** no weekend calls, and none after about Fri 15:00 NY.
* **BTC and ETH, the pairs you know best, are the slowest to qualify.** They wash about 54 %
  and 49–57 %.
* **Random LONGs won only 0.35–0.45 of decisive crypto calls over 30 d.** The market fell.
  The random-call P(TP|dec) shows the **regime drift**, not skill. A backtest whose edge
  comes from one direction is a regime bet. Check `eval`'s per-direction rows.

**Cross-check with official data.** Over the same windows, official (miner-timed) wash ran
below random-time wash by **2.8 pp** (30 d, n-weighted, 48 pairs) and **4.0 pp** (7 d,
45 pairs). Miners call into active periods. The random baseline is therefore slightly
conservative.

## How fast you qualify (Monte Carlo, `sn89_eval.simulate`)

Assumptions:
* 3 calls a day, 8 h apart, all accepted.
* Each call is decisive with probability 1 − wash, and a decisive call wins with
  probability p.
* Decisive calls resolve at a uniform time inside the 8 h window. Washes resolve at 8 h.
* 300 runs of 90 days each.
* "Tally" is the mean tally over days 30–90. Divide it by Σ field tallies to get your LF
  share.

| p = P(TP\|dec) | wash | median days to first PAID win | paid by day 14 | by day 30 | mean tally d30–90 |
|---|---|---|---|---|---|
| 0.55 | 0.20 | 23.7 | 38 % | 55 % | 7.0 |
| 0.60 | 0.00 | 10.3 | 67 % | 90 % | 17.3 |
| 0.60 | 0.35 | 12.0 | 58 % | 81 % | 11.2 |
| 0.60 | 0.50 | 15.3 | 47 % | 69 % | 8.1 |
| 0.62 | 0.35 | 11.3 | 57 % | 86 % | 13.3 |
| 0.65 | 0.00 | 8.3 | 85 % | 98 % | 23.6 |
| 0.65 | 0.35 | 9.0 | 78 % | 95 % | 16.8 |
| 0.65 | 0.50 | 10.3 | 67 % | 86 % | 12.5 |
| 0.70 | 0.35 | 8.7 | 89 % | 99 % | 20.5 |

The earliest possible paid win comes just after day 8, when warmup ends.

## Decision rule

**Go only if all of the following hold** for the pairs you will actually trade:

1. **Calibrated, out-of-sample P(TP first | decisive) of at least about 0.62–0.65**, measured
   over **at least 150 decisive calls**.
   * Use `eval --oos-from <date the model was frozen>`.
   * Read the hit rate and Wilson LB on the OOS rows, and the calibration table.
   * The OOS Wilson LB (z = 1.28) should itself sit above about 0.57.
   * At p = 0.55 a hotkey qualifies by luck and slowly, then drops in and out of
     qualification (LB ≈ 0.50 at n = 150).
2. **Enough passing signals per day.** At your chosen `--prob-threshold`, `pass/d` should be
   at least about 3, so the 3-call daily quota fills, and `acc/d` at least about 2.5 after
   the 1 h gap and the session rules. Fewer accepted calls stretch every row of the
   Monte Carlo table.
3. **Graded on candles ≤ 5m.** That means 1m/3m/5m, or your own archive. On 15m or coarser
   candles, discount the hit rate by about 1 pp.
4. **Edge holds in both directions,** or at least is not explained by the window's drift:
   compare with `baseline` P(TP)L / P(TP)S for the same days.
5. **Pairs:** prefer low-wash pairs graded on HL itself.
   * Fastest right now: KPEPE, then AVAX, AAVE, BCH and TAO among crypto, plus the HIP-3
     equities and indices if the model has equity skill.
   * Avoid basing the decision on BTC/ETH alone.
   * Avoid AUDUSD and USDCAD (cannot be backtested here).
   * Treat EURUSD, XAU and XAG as proxies.

If the OOS hit rate is 0.55–0.60, the economics are marginal. Before committing, get a
tally estimate against the field (`--field-tally`) and weigh it against the cost of
running your own UID. That cost is registration plus the 500-alpha LF bond required since
2026-11-09. The other option is the entity-account route.

## Limitations

* **Candles, not ticks.** Remaining error is about 3 % of labels at ≤5m and 6–12 % at
  15m–1h. W/L flips stay at or below 1.1 % on ≤5m.
* **The feed basis drifts.** SN89's line mixes HL trades with book mids. FX (EURUSD) is a
  thin HL proxy. AUD and CAD do not exist on HL.
* **The board changes over time.** Bands are recalibrated (40 % wash target, ±20 % per cycle)
  and listings change. Re-copy `data/signals-bands.json` and re-run `baseline`.
* **Assumed details.** These simulation inputs are assumptions, not verified validator
  behaviour:
  * first_seen = your first accepted call
  * the copy-detection, elimination and HF pair-lock rules are not simulated
  * entity-account (UID 39) pooling is not modelled
* **The live table is a 7 d / 30 d snapshot,** taken in a falling crypto market and a very
  volatile equity week.
