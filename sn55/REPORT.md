# SN55 (NIOME) — Top-10 Feasibility, CPU-First Solver & Profitability

**Investigation date:** 2026-11-11 (UTC). **Chain snapshot:** finney block 9,480,484.
**Code audited:** `genomesio/subnet-niome` @ `0c03fef2476b56285a202c76cc0d698b62b86b74` (committed 2026-11-10).
All chain/market reads are from the live network; all scores are from the **unmodified** official
scorer run offline. Nothing was registered, no TAO was spent, no production system was touched.

---

## 1. Executive verdict — **PILOT GO** (conditional on owner authorization)

A legally-clean, CPU-only miner that produces a **fully valid, coverage-balanced, weight-tilted**
submission is built, works, and beats the obvious competent baseline by ~10% on the official
scorer, across held-out seeds, building in **under 1 second on 2 vCPU with ~0.6 GB RAM**. The
official GPU / 16-vCPU / 16-GB requirement is false for miners — it describes the validator (which
runs a RandomForest scorer), not the miner (which only emits a JSON design file).

The economics are a clean asymmetric bet: downside ≈ **$8/month** if you never place; upside is
**$300–$9,600/month** (chain-value) depending on rank, with the daily emission liquidatable at
~0.1% slippage in a deep pool. Break-even needs you to hold *any* top-10 slot for **<3% of rounds**.

The one thing that **cannot be resolved offline** is whether the solver clears the live top-10
threshold. There is no public leaderboard (the backend returns 502) and no miner-facing score
endpoint, so incumbent scores are unobservable. The top slots are held by a **persistent core of
~8 competent operators** who continuously re-register UIDs. An offline score gain does not prove
you beat them. The minimum experiment that resolves this is a **bounded, owner-approved, single-UID
mainnet pilot** — total at-risk cost on the order of **$10–20**.

**Do not** spend on GPU: the binding constraint is competitive submission quality and live
threshold uncertainty, both of which are software/measurement problems, not compute problems.

---

## 2. The "11-miner" question — resolved

At block 9,480,484 exactly **11 UIDs** carry nonzero incentive: **10 miners + the owner UID 119**
(hotkey `5DJ5fT1…`, the subnet owner hotkey), which takes exactly the `BURNING_RATE` share.

| layer | count | evidence |
|---|---|---|
| Registered UIDs | 256 (full; MaxAllowedUids 256) | metagraph |
| Validator-permit UIDs | 12 | metagraph |
| Validators actually setting weights | ~9 non-owner + owner 119 | on-chain weight map |
| **Distinct scoring authority** | **1 (owner UID 119)** | every validator's weight vector is a byte-identical copy of 119's; UID 204 copies only `{119:1.0}` |
| Nonzero incentive | 11 (10 miners + owner burn) | metagraph |
| Nonzero emission | 20 (10 miners + validators + owner) | metagraph |
| **Distinct miner operators (coldkeys) in the 10 slots** | **6** | 10 rewarded miner UIDs map to 6 coldkeys |

The incentive vector matches `SCORE_DISTRIBUTION × (1 − 0.01)` on every slot to rounding — the
reward *shape* is a hardcoded constant and carries no information; only *who* holds each slot does.
Because all validators mirror the owner's weights, **the subnet is effectively scored by one party**
(the owner), and today's on-chain ranking reflects a **single weight-set that is ~24 h stale**.

---

## 3. Competitive entry assessment (30-day chain history, 200 samples)

Tracking stable identities (hotkey/coldkey) over 30 days via an archive node:

- **243 distinct hotkeys** held a rewarded slot, but only **26 distinct operators (coldkeys)**.
- A **stable core of 8 operators** occupies slots **43–60%** of all samples and **shares all four
  top-paying slots** (each present in the top-4 ~28–38% of the time).
- The huge hotkey turnover is **re-registration by the same operators** (immunity is 1 day, burn is
  ~0.0005 TAO ≈ $0.11), not displacement by newcomers.
- A **never-before-seen operator** appeared in the top-10 in only **13 of 180** post-warmup samples.

**Finding:** SN55 is a genuine open contest — entry is nearly free and 26 operators did cycle
through — but holding a *paying* slot means out-scoring ~8 persistent, competent operators every
round, where ties break on ~6% per-round scoring noise (§4). It is **accessible but actively
contested**, not a locked cartel and not a free ride.

Verified vs. estimated: operator counts, persistence, slot shares, burn, immunity, prices are
**verified** on-chain. Incumbent *submission quality / scores* are **unobservable** (no public
leaderboard; backend 502).

---

## 4. Source-code & scoring analysis

**Task.** Each round the backend issues (via presigned URLs inside a validator→miner synapse) a
`contract.json` and `hbb_reference.json`. The miner emits ≤ `max_experiments` CRISPR guide designs
around the HBB locus (chr11, GRCh38) and PUTs a JSON array to a presigned URL. **The miner predicts
nothing and trains nothing** — it submits designs. The grader is open-source and deterministic.

**Score:** `final = TWS × CF × DFF`, averaged over `SEED_COUNT=3` seeds.
- **TWS** (stage 1–2): sum over valid rows of `(0.625·gc_score + 0.375·dist_score) · offtarget_factor · mutation_weight`. A **sum**, so valid-row count is linear money.
- **CF** (stage 4): a RandomForest cross-validation "consistency" factor. Measured here at
  **mean 0.089, per-seed std 0.011**; negative R² in every configuration — it is **seed noise ~0.09
  for everyone** and not a useful optimization target.
- **DFF** (stage 5): geometric mean of 6 coverage/diversity ratios (mutation, Cas, strand, joint
  coverage entropy; k-mer diversity; distinct-guide ratio). Because it is a geometric mean, **any
  single collapsed category zeroes the score** — concentrating on one mutation/Cas/strand gives
  DFF ≈ 0.03 (−97%).

**Hard validity rules (each voids a row silently):** mutation ∈ active set; `cell_type` exact
match; guide length ∈ {20,23}; `target_alignment_end == start+len`; in-bounds; valid PAM
(Cas9 NGG, Cas12a TTTV, strand-aware); guide↔reference Hamming ≤ `max_mismatches`; proximity gate
`|start−mut| ≤ base_padding`. **De-dup key = (cas, start, strand, guide) — it omits `mutation`**, so
one physical site serves exactly one mutation; `truncate_submission` cuts overflow and
duplicate `experiment_id`s from the file before scoring.

**What it rewards:** deterministic optimization (fill every slot with valid, balanced,
quality guides) plus operational reliability. Quality (TWS) and coverage (DFF) are **seed-independent**;
only CF varies with seed, and it is near-constant. So ranking ≈ by **TWS × DFF**.

**Changes since the Sept-2026 independent audit (verified from git history):**
1. **`BURNING_RATE` 0.02 → 0.01** (owner share halved; commit 2026-10-18).
2. **`INTERVAL_BLOCKS` 720 → 1080**, `VALIDATION_BLOCK` 500, `WEIGHT_SET` 1000 (2026-11-04).
3. **Seeds are no longer in the task.** Since 2026-09-23 the validator draws seeds from **block
   hashes** at scoring time; since **2026-11-10** the range is `(0, 1,000,000)` (was `(0,1000)`).
   This **closes the pre-enumeration loophole**: a miner can no longer pre-score against the exact
   seeds. It does not hurt a quality-first solver, because TWS and DFF do not depend on the seed.
4. **A byte-identical-cross-UID warning** was added (copy/collusion detection; logs only).
5. Storage moved to S3-or-Google-Drive with a server-side upload deadline.

**Layer separation.** Documentation (GPU/16-core) ≠ code (miner needs neither). Code is fully
auditable. **Deployed validator behavior** (exact live `contract.json` values, backend scoring)
is **UNKNOWN** — gated behind a validator hotkey signature. The contract used here is a labelled
synthetic reconstruction; its parameters are plausible but unverified. Resolving this needs a
registered hotkey to receive one real task (part of the pilot).

---

## 5. Infrastructure recommendations

| | Minimum | **Recommended** | Performance (not needed) |
|---|---|---|---|
| vCPU / RAM | 2 / 2 GB | **2 / 4 GB** | 4–8 / 8 GB |
| GPU | none | **none** | none — no measured use |
| Disk | 1 GB (genome 135 MB) | 5 GB | 10 GB |
| Build time (250 rows) | ~1 s first, ~0.2 s warm | same | same |
| Peak miner RSS | 0.6 GB | 0.6 GB | 0.6 GB |
| Cost/month | **$0** (reuse existing VPS) | **~$5** (dedicated 2 vCPU) | ~$12 |
| Registration | 0.0005 TAO ≈ $0.11 | same | same |

The deadline budget is enormous: broadcast lands early in a 1080-block (~3.6 h) round and the upload
window is 300 s; the build is ~1 s. A 2-vCPU box under shared scheduling meets it with >100× headroom.
GPU rental ($500+/mo) is **not justified** — there is no workload for it.

---

## 6. Experimental results (unmodified official scorer)

**Source-fidelity gate — PASSED.** A fast reimplementation of stages 1/2/5 reproduces the official
scorer's **TWS and DFF to 1e-6** on 8 contracts × 2 strategies. (CF is seed-random and taken only
from the official scorer.)

**Strategy benchmark** — 8 synthetic contracts × 8 held-out seeds each, official scorer:

| strategy | rows | valid % | TWS | DFF | CF mean | final median | final p10 | build |
|---|---|---|---|---|---|---|---|---|
| `reference_stub` (shipped `process_task = pass`) | 0 | 0 | 0 | 0 | 0 | **0.00** | 0.00 | — |
| `simple_balanced` (valid, balanced, exact-match) | 225 | **100** | 182.6 | 0.971 | 0.094 | **16.37** | 12.73 | 19 ms |
| `optimized` (tilt-swept weighted-balanced) | 225 | **100** | 226.7 | 0.909 | 0.088 | **18.04** | 14.77 | 139 ms |

**Ablation:** the shipped miner earns **0** (H2 — structural completeness is the single biggest
lever: 0 → 16.4). The weight-tilt optimizer (H3) adds **+10% median, +16% p10** on top, robustly
across unseen seeds (H5). Peak RSS of the whole bench (incl. scorer) < 0.9 GB.

**Round-score noise (fixed submission, live 3-seed config):** mean 18.4, std 1.08, **CV ≈ 6%**
(single-seed CV 12.7%). So among miners of similar quality, rank jitters ~6% per round on CF noise
alone — an edge smaller than that does not reliably hold a slot.

**Limitation:** tight contracts constrain supply — a `base_padding=100` proximity-gated contract
yields only ~69 valid rows (not 250) because real PAM sites run out. This caps everyone equally.

**What the offline score does NOT prove:** that 18 beats the live top-10 threshold. No incumbent
scores are obtainable, so top-10 entry remains unproven until a pilot.

---

## 7. Viability hypothesis matrix

| ID | Mechanism | Result | Confidence |
|---|---|---|---|
| **H1** CPU-first is enough | Build is ~1 s on 2 vCPU, 0.6 GB; 300 s upload window → >100× headroom | **Validated** | High |
| **H2** Validity/completeness dominates | Shipped stub → 0; full valid balanced → 16.4. Largest single lever | **Validated** | High |
| **H3** Global allocation/diversity | Weight-tilt + grid balance adds +10% median over simple balanced | **Validated** | High |
| **H4** Exploitable scoring inefficiencies | CF is ~0.09 noise (ignore it); DFF geometric-mean is fragile (must balance). Exploit-style tricks (deterministic-outcome gaming, seed-region mismatch to dodge off-target) were **identified but deliberately not built** — they resemble metric manipulation and were out of scope | Partially validated / **declined on ethics** | High |
| **H5** Robustness to unseen seeds | TWS·DFF are seed-independent; optimizer's edge holds on held-out seeds | **Validated** | High |
| **H6** Precompute lowers cost | k-mer index + genome cached; warm build ~0.2 s; a 2 GB box matches an 8 GB box | **Validated** | High |
| **H7** Reliability is an edge | Instant-ack + async build + retry/deadline-aware upload; crash-safe | **Validated (design)** | Med (live-untested) |
| **H8** Field < 256 | Only ~8 persistent operators + occasional entrants genuinely compete; 245 UIDs earn 0 | **Validated** | High |
| **H9** Risk-adjusted rank economics | Ranks 1–4 hold ~84% of the pool; even rank 5–10 clears infra many-fold | **Validated** | High |

Declined-on-ethics items are reported as **risks/known levers**, not built: they would raise the
offline number but constitute gaming the validator's statistics, which the mission excluded.

---

## 8. Financial model (live, 2026-11-11)

Prices: **TAO $219.88**, alpha **0.0019589 TAO = $0.431**, pool **5,630 TAO / 2.87 M alpha**
(selling the entire 2,922 alpha/day miner pool = **~0.1% slippage** — daily emission is liquidatable
near spot). Per-rank payout read from on-chain emission:

| rank | share | alpha/day | USD/day | USD/mo (chain) | USD/mo (realizable ×0.85) |
|---|---|---|---|---|---|
| 1 | 29.7% | 877 | $378 | $11,340 | **$9,630** |
| 2 | 19.8% | 585 | $252 | $7,560 | $6,420 |
| 3 | 19.8% | 585 | $252 | $7,560 | $6,420 |
| 4 | 14.8% | 438 | $189 | $5,670 | $4,820 |
| 5 | 4.9% | 146 | $63 | $1,890 | $1,600 |
| 6–10 | 0.99–2.97% | 29–88 | $13–38 | $380–1,130 | $320–960 |
| 11+ | 0 | 0 | 0 | 0 | **$0** |

**Break-even entry probability** (infra-only cost; $0 when out of top-10): at $5/mo infra, holding
rank-10 for **1.3% of rounds** breaks even; rank-1 for **0.04%**. At $40/mo, rank-10 needs 10.6%.
The downside of a failed attempt is ~**$8/month** (VPS + a few re-registrations).

**Expected net** = Σ(p_r · net_r) − idle costs. The p_r are **unknown offline** and are **not
invented here**. What is certain: the loss is bounded and tiny, and any sustained top-4 presence is
worth thousands/month.

**30-day net scenarios (realizable, $5/mo infra):** rank1–2 sustained ≈ **+$9.6k**; rank3–4 ≈
**+$6.4k**; rank5–10 ≈ **+$0.3–1.0k**; intermittent (held 25%) rank3–4 ≈ **+$1.6k**; never-enter ≈
**−$8**.

**Stress tests:** −25% alpha → rank1 ~$7.2k/mo, rank5–10 still > infra. −50% alpha → rank1 ~$4.8k,
rank10 ~$160/mo (still > $5 infra). A new strong entrant or an incumbent improving, a validator
scoring revision, or the **v4 task switch** (see §below) are the real tail risks, not price.

**Theoretical vs. realizable:** the pool is deep enough that emission converts at ~spot today, but
these are **gross chain-value** figures; sustained net selling by all miners and alpha-price drift
over months are not modelled and should be treated as the dominant uncertainty after competitive entry.

**Forward-looking risk — `genomesio/subnet-niome-v4`:** a separate repo defines a *completely
different* task (pharmacogenomic star-allele inference) with `BURNING_RATE=0.02`. It is **not
deployed** (the live 1% owner share matches the current repo, not v4). If the owner switches SN55 to
v4, **every solver built for the current CRISPR task becomes obsolete**. This is the single largest
strategic risk and argues for a *fast, cheap* pilot rather than heavy up-front investment.

---

## 9. Deployment design (see `miner/DEPLOY.md`)

- **Isolation:** new VPS, new Linux user, new wallet (coldkey + hotkey) created solely for SN55.
  SN123 MANTIS and its observers/wallets/keys are not touched; no production process is modified.
- **Miner:** drop-in `process_task` (`miner/process_task.py`) — replies `{}` instantly, builds the
  submission off the event loop, uploads with retry inside the 300 s window. Pinned validator code
  at `0c03fef`; reference genome pinned to Ensembl r116 chr11 (BSD `sum` = `06038 39169`).
- **One UID.** Multi-UID farming contradicts the subnet's stated one-submission-per-owner intent
  (filter currently absent from code, intent comment remains); do not do it without the owner's OK.
- **Uptime/monitoring:** pm2 with health logging; alert if a round passes with no successful upload.
- **Financial caps:** pre-set a max registration spend and a hard stop date; only ongoing cost is
  the VPS + ~$0.11 per re-registration.
- **Rollback:** kill the pm2 process; nothing else to unwind. No funds are locked beyond staked
  alpha, which can be unstaked and sold at ~spot.

---

## 10. Single highest-priority next action

**Run a bounded, owner-approved, single-UID mainnet pilot.** It is the only experiment that
resolves the one unknown that offline work cannot: whether this submission clears the live top-10
threshold against the ~8 persistent incumbents, and whether the live `contract.json` matches the
reconstruction. Concretely: register one hotkey (~$0.11), capture one real task to validate the
contract schema, run the miner for ~5–10 rounds (~1–2 days), and measure actual incentive/emission.
**Max at-risk ≈ $10–20; automatic stop if no top-10 placement within the immunity window.** Do this
*before* any further solver investment, and *before* committing to the subnet, because the pending
v4 task switch could obsolete everything.

---

### Evidence ledger
- Code: `genomesio/subnet-niome` @ `0c03fef` (2026-11-10); v4 @ `99ff6bf` (2026-10-19).
- Chain: finney block 9,480,484; archive node for 30-day history (200 samples, 0 errors).
- Snapshots: `evidence/metagraph_now.json`, `evidence/weights_now.json`, `evidence/history_30d.jsonl`.
- Scorer fidelity: `solver/fastscore.py` vs. official, 1e-6 on 8 contracts.
- Benchmarks: `evidence/bench.json`. Economics: `bench/economics.py` (TAO $219.88, alpha $0.431).
- Genome: Ensembl r116 chr11, `sum` = `06038 39169` (matches Ensembl CHECKSUMS).
- **UNKNOWN / needs pilot:** live `contract.json` values; incumbent scores; exact live round timing;
  whether/when SN55 migrates to the v4 task.
