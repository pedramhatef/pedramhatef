# Challenge 3 readiness — Drug Response & Rare Disease

Strategy (operator-approved): hold the clean solver, keep the read-only tracker running as a
rotation alarm, and deploy on **day one** of Challenge 3 — when the leaderboard resets and the
Challenge-2 CF exploits break, a correct solver can take top-4 emissions cleanly before the
metric-hackers re-crack the new validator. This file is the day-one playbook.

## Why we already have a head start

Challenge 3 is **Drug Response & Rare Disease**. That is the **pharmacogenomics (PGx) star-allele**
task — and the validator code for it already exists in the repo we pulled: `subnet-niome-v4`
(`99ff6bf`, the `/api/v4` pipeline). It is NOT the current scorer (v4 returns 404 today), but it is
the closest available template for what Challenge 3 will score. Studying it now is free prep.

## What the PGx / drug-response task scores (from the v4 validator)

Per round the backend synthesises a population of patient cases from public CPIC allele-frequency
tables and gives each miner a disjoint bundle of cases. For each case × active gene the miner must:

1. **Recover the star-allele diplotype** from realistic, imperfect genomic observations
   (e.g. `*1/*17`). This is the genuinely hard, unsolved part — especially CYP2D6 (copy-number,
   hybrids). Copying is worthless: the true diplotype is one short string identical for every
   honest solver, and it is derived from a seed revealed only after the round closes.
2. **Derive the CPIC phenotype + activity score** from the diplotype (a deterministic table lookup
   the miner must get internally consistent — stage3 zeroes any call whose phenotype/activity/
   copy-number doesn't follow from the submitted diplotype).
3. **Give the CPIC prescribing action / recommendation** per drug in the round's `drug_set`.
4. **Report calibrated confidence** (a strictly proper scoring rule; saying "ambiguous" when it is
   matters).

Scoring is graded by: phenotype concordance (exact / adjacent / wrong), recommendation concordance
(exact / action-only / same-direction / opposite / wrong), weighted by **rarity_weight** (rare and
structurally complex alleles earn most), with an internal-consistency gate and a coverage gate.
**No RandomForest, no cross-validation, no CF lottery** — it is a deterministic string-compare and
table lookup. Correctness wins, honestly.

Miner submission shape (v4): `{miner_uid, cases: [{case_id, genes/calls with submitted_diplotype,
submitted_phenotype, submitted_activity_score, submitted_total_copy_number, recommendations:
[{gene, action, cpic_classification}]}]}`.

## What carries over vs what we rebuild

Carries over unchanged (all challenge-agnostic, already working on Challenge 2):
- the isolated launcher + the auth fixes (receiver-binding, nonce window), axon public-IP publish,
  the watchdog, and the whole read-only tracker/alarm.
- the deployment discipline (pinned upstream, override only the solver hook).

Rebuilt for Challenge 3 (the solver only — `process_task` / `solve`):
- a **star-allele diplotype caller** from the round's observational data (the hard core),
- **CPIC/PharmVar reference tables** (allele definitions, activity scores, phenotype maps, gene-drug
  recommendation tables) — public, built at startup the way the validator builds them,
- phenotype/activity/copy-number derivation kept internally consistent with the diplotype,
- recommendation mapping + a calibrated-confidence head.

## Pre-stage now (low-risk, no new spend)

1. Keep the tracker daemon running; it probes `/api/v4,v5,v6/tasks/current` every poll and writes
   `CHALLENGE_ALERT.txt` the moment a newer API goes live or the leaderboard/contract schema changes.
2. Fully read the v4 validator (stage12/3/4/5, `pgxlib`, `build_reference.py`) and list the exact
   public reference sources it pins (CPIC tables, PharmVar, PharmCAT commit) so we can fetch/build
   the bundle fast on day one.
3. Sketch the diplotype-caller architecture against the known hard cases (CYP2D6, CYP2C19, …).
4. **Do not build the full solver speculatively** until Challenge 3's *actual* validator code is
   published — "Drug Response & Rare Disease" may differ from the old v4 PGx in specifics, and the
   exact scoring constants/`drug_set` arrive with the round contract.

## Day-one sequence (on CHALLENGE_ALERT)

1. Pull the new challenge's validator repo; diff its scoring against this v4 template.
2. Build the pinned reference bundle; wire the diplotype caller into `solve`.
3. Self-test end-to-end against the new scorer offline (reuse the Challenge-2 selftest harness).
4. Deploy behind the existing launcher/watchdog; the tracker starts scoring us immediately.
5. Turn on the hardening switches once a genuine validator + upload host are observed, as before.
