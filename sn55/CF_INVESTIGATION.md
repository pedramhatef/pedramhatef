# CF investigation — can the consistency factor be raised honestly?

Date 2026-11-12. Question: our honest build has a competitive base (TWS·DFF ≈ 168) but CF ≈ 0.10,
and placing needs CF ≈ 0.20 (rank-10) to 0.28 (rank-5). Is there an honest solver change that
raises CF? Verified against the official pinned scorer (`subnet-niome @0c03fef`).

## How CF is computed (v3 / 0c03fef)

`CF = clip(0.7·max(avg_r2, 0) + 0.3·(1 − avg_nmae), 0, 1)`, where `avg_r2` is the mean 5-fold CV R²
of a `RandomForestRegressor(n_estimators=200, max_depth=12)` predicting three targets
`y = [is_cut, is_hdr, indel_length]` from `X = [gc, distance, gc_score, dist_score, consistency,
energy, mh]` (stage4). The targets are **random draws** whose distributions are deterministic
functions of the features (stage3):

- `energy = clip(accessibility·(1.8·gc + 0.6·exp(−dist/1500) + region_offset), 0, 1)`
- `is_cut ~ Bernoulli(cut_p)`, `cut_p = clip(base_cas + 0.18·energy, 0.4, 0.99)`
- `is_hdr` via `repair_mode`, `hdr = base_cas + 0.35·energy`; `indel_length` gamma/expo by mode.

## Why our CF is stuck at ~0.10

Our TWS optimisation pushes gc→0.5 and picks near sites, which **saturates energy near 1.0 for
every row** → `cut_p ≈ 0.99` everywhere → outcomes near-constant → the RF has almost no
feature-driven variance to learn → R² ≈ 0 → CF ≈ 0.10.

## The energy-spread hypothesis — tested and falsified

Spreading energy widely (raw guides, gc 0.14→1.0, distance 0→2000; energy std 0.06 → 0.22) did
**not** raise CF (0.096 → 0.075–0.087) and collapsed TWS (139 → 81), so final dropped (13.2 → 6–8).
Official stages, 4 seeds. Reason: the targets are Bernoulli/gamma *draws*; even a perfect predictor
of the energy-driven mean cannot beat the irreducible per-row variance (`p(1−p)` etc.).

## The hard ceiling — oracle proof

The **oracle** predictor (the true per-row conditional mean, estimated by Monte-Carlo over 300
seeds — the best any model can do) achieves:

| submission | oracle avg R² | implied CF ceiling (best case, nmae→0) |
|---|---|---|
| our 250-row build | **0.0016** | 0.30 |
| maximally energy-spread ideal (energy 0→1) | **0.0195** | 0.32 |

So the `0.7·R²` term contributes ≤ ~0.014 to CF no matter what we submit. Honest CF is essentially
all the `0.3·(1−nmae)` term, ceiling ≈ **0.31**, and only if MAE error → 0. **There is no honest
solver change that materially raises CF under the v3 scorer.** Our ~0.10 is near the realistic
honest level.

## What this says about the winners

Leaderboard winners post CF 0.4–0.67 every round — **above the honest oracle ceiling (~0.31)**. A
5-fold CV R² that high is not achievable by honest prediction of these targets; it comes from
overfitting the specific CV realisation (near-duplicate / low-variance feature structure that
inflates reported CV R² without genuine predictive signal). That is metric-gaming of the
cross-validation, not submission quality. Out of scope on principle.

## The v4 fork — where honest effort would pay off

The leaderboard that scores us uses the **v3** fields (`consistency_factor`,
`distribution_fidelity_factor`, `total_weighted_score`) via `/api/v3/miners/scores`. A **v4**
pipeline exists in the repo (`99ff6bf`, `/api/v4/…`) with a completely different, **deterministic,
biology-based** score — `recommendation_credit` from phenotype / edit-direction / adjacency /
rarity_weight, **no RandomForest and no CV lottery**. Under v4, score is honestly optimisable by
making biologically correct, well-directed recommendations.

## Conclusion / recommendation

- **Under the live v3 scorer, there is no honest lever that lifts CF enough to place.** Our
  deployed build already sits at the honest ceiling for the controllable part (base ≈ 168), and
  the dominant CF term is a CV lottery the leaders game. Honest outcome: mid-pack.
- **Confirm whether the subnet is migrating v3 → v4.** If v4 is (or is about to be) the scoring
  pipeline, that is where honest, competitive effort belongs — build a v4 solver against the
  phenotype/direction/rarity model. If v3 persists, the honest ceiling is mid-pack and no amount of
  solver work changes that.
- The tracker records our real scores each round so the moment v4 goes live (or the v3 cutoffs
  move) we see it immediately.
