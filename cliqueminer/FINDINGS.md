# CliqueAI (Bittensor SN83): can a new miner reach the top 20?

Investigation date: 2026-11-12. Sources: CliqueAI source at v0.0.17, 8,004 logged validator rounds (72 hours, every
queried miner's answer), and the live metagraph at block 9,488,706.

## Answer

**No. Under the current mechanism and incumbent behaviour, no program run by a newcomer can reach the top 20, on any
hardware, with one miner or with a fleet of up to about 60.** Do not rent a VPS or GPU for this. Two independent reviewers tried to overturn this conclusion and could
not (see "Independent review").

| | Mean reward per round |
|---|---|
| Needed to hold a top-20 place half the time | 2.704 |
| Needed to hold it 90% of the time | 2.724 |
| The six incumbent operators (232 of 248 miners) | 2.64 – 2.69 |
| **A newcomer running this repository's miner** | **2.50** |
| **A newcomer with the best answer-picking model anyone found** | **2.55** |
| **A newcomer with perfect foresight of every other miner's answer** | **2.64** |
| The 16 independent miners already on the subnet | 2.45 (best of them 2.52 – 2.54) |

A miner at 2.50 sits around rank 247 of 251, earns about 0.1 alpha a day (roughly $0.15), and is among the first to
be deregistered when someone else registers. At 2.55 it would be around rank 238. Even the perfect-foresight ceiling
is below the 200th-ranked incumbent (2.645), so this is not something a better solver, a GPU, or more cores can fix.

The reason is structural. It is explained in "Why a newcomer loses" below.

## How the subnet scores miners

The documentation (`docs/mechanism.md`) is out of date. This is what the code does.

- A validator sends one graph per minute. Graphs look like uniform random graphs with edge density drawn evenly from
  0.70 to 0.95 (degree spread matches a binomial to within 0.2%).
- Four tiers, chosen with equal probability: 290–300, 490–500, 690–700 and 890–900 nodes, with difficulty `d` of 0.7,
  0.8, 0.9 and 1.0. Each miner is sampled independently with probability 0.317, 0.245, 0.166 and 0.078.
- Time limit is drawn from 6, 7.5, 10, 15 and 30 seconds (no 6 s on the largest tier). It is the total HTTP timeout.
- Reward per round is `omega × (1 + d) + delta`.
  - `omega = exp(−pr / rel)`, where `rel` is your clique size divided by the largest size returned, and `pr` is the
    share of queried miners who returned something strictly larger. Normalised by the round's best.
  - `delta = 1 / (number of queried miners who returned exactly your vertex set)`, normalised by the round's best.
  - The answer must be a clique that cannot be extended, or it scores zero.
- The validator keeps a debiased moving average of each miner's reward (alpha 0.01, half-life 69 rounds, about 6
  hours) and converts scores to weights through a sigmoid and a power that gives the top half of miners 80%.

I re-implemented this scoring and it reproduces the rewards the validator logged exactly (0 mismatches in 1,300
rounds checked). Everything below rests on that re-implementation.

## Who the miners are

| Operator (coldkey) | Miners | IPs | Registered (days ago) | Share of incentive | In top 20 |
|---|---|---|---|---|---|
| `5HMevt8h…` | 60 | 1 | 83 – 94 | 30.3% | 6 |
| `5Hg2Ps2L…` | 35 | 1 | 84 – 87 | 24.8% | 7 |
| `5EfHz7fE…` | 40 | 1 | 80 – 83 | 22.3% | 7 |
| `5Eyh8ePM…` | 40 | 1 | 79 – 82 | 10.3% | 0 |
| `5FcJnoeN…` | 40 | 1 | 70 – 79 | 6.4% | 0 |
| `5HHKTW5b…` | 17 | 1 | 69 – 76 | 5.9% | 0 |
| 8 other coldkeys | 16 | 8 | 0 – 71 | 0.07% | 0 |

Six coldkeys, each on a single IP, run 232 of the 248 miner slots. All 232 were registered in consecutive waves
between 68 and 94 days ago, in the weeks after the last release (2026-07-22). Everyone who has registered since is at
the bottom.

Incentive on chain is one validator's opinion. The subnet owner's validator (uid 0, 43% of validator stake)
computes scores; six other validators holding 56% copy its weight vector a few blocks before each epoch. Replaying
that validator's public log through the scoring code reproduces its on-chain weights to 26 parts in 65,535.

## What the incumbents do

Their solvers are already optimal. On 1,200 backtested rounds my solver matched the largest clique in the pool 1,190
times on one core (the other 10 with three cores) and never beat it. An exact solver confirmed the pool's best is
the true maximum in all 111 rounds where a proof finished. The incumbents also enumerate the maximum cliques at least
as completely as I do: my lists are complete on the 300-node tier and miss a clique the pool used in 11% of 500-node
rounds and 40 – 50% of the larger ones at the backtest's one-core budget.

They then play the diversity term, with one shared rule and one shared allocation:

1. **A shared size rule.** If the graph has fewer than 12, 8, 5 or 3 maximum cliques (for the 300, 500, 700 and
   900-node tiers), five of the six operators return distinct cliques one vertex smaller, and the sixth puts a few
   miners on the maximum cliques and the rest one smaller. Otherwise all six return maximum cliques. The thresholds
   fit 100% of rounds on the two smaller tiers and 94 – 99% on the two larger ones, where my own count of maximum
   cliques is less reliable. This is why rounds split cleanly into "everyone at the best size" (75%) and
   "only three or four miners at the best size" (25%), with almost nothing in between (0.2%).
2. **A shared allocation.** When there are fewer maximum cliques than miners, the six operators' miners are spread
   over the cliques so that the per-clique counts, summed across all six operators, differ by at most one. Example:
   78 miners over 12 cliques gave counts of 6, 6, 6, 6, 6, 6, 7, 7, 7, 7, 7, 7. This held in 99.6% of 1,862 such rounds
   across both logging validators. Six operators who each spread their own miners evenly but independently would
   manage it about 5% of the time, and each operator taken alone is balanced in only 29% of rounds: the balance exists
   only in the six-way total.

That degree of balance needs something that knows, round by round, which miners of all six operators were queried.
The data shows coordinated allocation across the six coldkeys. It does not by itself show common ownership.

## Why a newcomer loses

`delta` is normalised by the best `delta` in the round. If every clique carries the same number of miners, everyone
gets the full 1.0. The six operators arrange exactly that among themselves.

Anyone outside the arrangement is the extra miner on whichever clique they choose. If the balanced count is `c`, the
newcomer's clique has `c + 1` and scores `c / (c + 1)`: 0.50 when cliques are held singly, 0.67 at two each, 0.86 at
six each. The balanced miners elsewhere keep 1.0. When there are somewhat more maximum cliques than incumbent miners,
the incumbents never collide with each other, while a newcomer's pick lands on an occupied clique in proportion to
how many are taken.

Three things would rescue a newcomer, and none is available:

- **Find a larger clique than everyone.** It never happened in 1,200 rounds. The incumbents return true optima.
- **Pick a clique the incumbents leave empty.** In crowded rounds they occupy every maximum clique (a free one
  exists in under 4% of such rounds). In the other rounds the cliques they take are only weakly predictable. No simple
  ordering works (lexicographic order, vertex ids, degrees and search hit counts all give an AUC of 0.37 – 0.57, where
  0.50 is a coin flip). The cliques they use do cluster among cliques that differ by a vertex swap, and a model built
  on that lifted a newcomer from 2.50 to 2.55, about a third of the way to the ceiling.
- **Answer one size smaller for uniqueness.** When everyone else is at the best size this costs about 64% of the
  `omega` term. It only pays in the rounds where the incumbents do it too, and my policy already does it there.

The perfect-foresight row in the table at the top is the cleanest evidence. Give the newcomer every other miner's
answer in advance and let it choose its best reply, and it still averages 2.64. On the 300-node tier, where my
enumeration of candidates is complete, that ceiling is 2.53 against 2.58 for the leading operators. Granting the
newcomer every benefit of the doubt on the larger tiers, where my candidate lists are incomplete, raises the overall
ceiling to at most 2.68. By round type the whole shortfall is in the crowded rounds (about 36% of the weight): there
the ceiling is 2.47 against 2.58 for the incumbents, and elsewhere perfect foresight would match or slightly beat
them.

### Would running many miners help?

Only as a majority. The incumbents' edge is the size of their coordinated bloc, not a property of the scoring rule
that protects them specifically. Simulated on the real rounds, with a coordinated fleet using the same size rule,
never placing two of its own miners on one clique, and displacing the lowest-ranked existing miners:

| Fleet size | Fleet reward per miner | Best incumbent operator after entry | Gap |
|---|---|---|---|
| 1 | 2.50 | 2.69 | −0.19 |
| 20 | 2.53 | 2.69 | −0.16 |
| 60 | 2.55 | 2.65 | −0.11 |
| 100 | 2.58 | 2.64 | −0.06 |
| 124 | 2.61 | 2.63 | −0.02 |
| 160 | 2.63 | 2.60 | +0.03 |
| 200 | 2.66 | 2.58 | +0.08 |

A fleet of up to 60 miners is still far from the top 20. The roles flip at roughly 125 to 140 miners, which is more
than half of the 248 slots. That is not a practical plan. Registrations are throttled (the subnet targets one per 360
blocks and no 20-hour immunity window in the current metagraph contains more than 21), so a growing fleet's early
miners would lose immunity while it is still the minority and be the first pruned. It also means evicting more than
100 existing miners, whose operators can re-register. The simulation holds incumbent behaviour fixed.

## What top 20 means here

Nobody holds a top-20 place. Over 48 hours 104 different miners appeared in it; the most persistent one was there 76%
of the time and only 9 were there more than half the time. The gap between rank 1 and rank 20 is 0.023, about the
size of the moving average's own noise (standard deviation 0.015). A miner exactly as good as the best operator's
average would be in the top 20 about 20% of the time.

| Rank | Alpha per day | USD per day |
|---|---|---|
| 1 | 48 | 70 |
| 20 | 25 | 37 |
| Median (124) | 10 | 15 |
| 200 | 4 | 6 |
| Independents today | 0.1 | 0.15 |

Alpha at 0.00676 TAO and TAO at $215. Total miner emission is about 2,950 alpha a day. Registration costs 0.148 TAO
(about $32). The weight curve is steep: each +0.01 of mean reward multiplies a miner's emission by about 1.3.

## What I built

Details and commands are in [README.md](README.md).

- **Solver** (C++): multi-threaded randomized local search with swap closure. Returns the best size, the distinct
  maximum cliques, and distinct maximal cliques one smaller.
- **Miner** (Python): decodes the graph, solves under a deadline, applies the answer policy, re-verifies the answer
  independently, and falls back to a greedy clique if anything goes wrong. Tested end to end on this machine against
  the real Bittensor axon, CliqueAI's own validator requester and its own scoring class. It has not been run on
  mainnet, since that needs a registered wallet.
- **Contestability check**: `python -m analysis.wandb_logs` downloads fresh public validator logs, and
  `python -m analysis.contestability` re-measures the key numbers in this report from them and prints GO, MARGINAL or
  NO-GO. The check takes about ten minutes on four cores.

While testing I found and fixed two bugs that would have bitten on mainnet: the search loop hung on a graph that is
one complete clique, and postponed annotations in the miner module made Bittensor's `Axon.attach` reject the handler.
The solver is clean under address, undefined-behaviour and thread sanitizers on real and degenerate graphs.

The miner picks uniformly at random within the chosen size class. The learned picker that reached 2.55 is not
included: it needs a trained model, its gain was not independently reproduced (a simpler variant gave 2.51), and it
does not change the outcome.

## Independent review

Two reviewers, working separately from my scripts, were asked to prove the conclusion wrong.

**The attacker** tried to build a winning newcomer policy. It could not find a usable pattern in how the incumbents
assign cliques: twelve candidate orderings failed, the answers arrive sorted so their order carries nothing, and which
cliques get the extra miner in crowded rounds is unpredictable. It did find that the cliques in use cluster, and a
gradient-boosted model on that structure scored 2.545 on held-out rounds (standard error 0.012). It confirmed the
2.64 ceiling and could not push it past 2.68 under the most generous assumptions.

**The auditor** re-derived the key claims with its own code, including an exact clique enumerator.

| Claim | Auditor's result |
|---|---|
| The six operators are allocated as one | Confirmed: 99.6% of 1,862 crowded rounds balanced, the same on both validators, every tier and both halves of the window. Rules that need no shared allocator (for example each miner picking by its uid) give 0.7%. |
| Shared switch to one-smaller cliques at 12 / 8 / 5 / 3 | Confirmed: 100% / 100% / 98% / 94% of rounds; exact proofs on 111 rounds bracket the first threshold between 10 and 12. |
| Perfect-foresight ceiling of 2.64 | Confirmed: 2.638 reproduced; most generous version 2.646 (95% interval 2.629 – 2.665) against 2.693 for the best operator on the same rounds. No resample reached 2.70. |
| No other kind of answer does better | Confirmed: cliques two below the maximum lost in 24 of 24 rounds tried; no clique larger than the pool's best exists in 111 proven rounds. |
| Fleets do not help | Corrected. True up to about 60 miners. My original simulation stopped there and missed that the gap closes with size (table above). |

The review corrected three of my working conclusions, and the corrections are what this report states: the
incumbents' choices are weakly predictable rather than unpredictable, my clique enumeration is complete only on the
300-node tier rather than on both smaller tiers, and fleet size does matter once a fleet approaches a majority.

## What would change the answer

Re-run the contestability check when any of these happens. They are all visible in public data.

1. **The operators stop balancing across each other, or their bloc shrinks toward half the slots.** The check
   reports the share of crowded rounds in which their combined counts are within one. Today it is 99%. If it falls
   toward the 5% that independent operators would produce, a newcomer competes on equal terms.
2. **The scoring changes.** The owner changed the mechanism eleven times between September 2025 and May 2026 and
   has not touched it since. The diversity formula itself has never changed. A rule that counted duplicates
   per coldkey or per IP, dropped the normalisation of `delta`, or reduced its weight would remove the incumbents'
   edge.
3. **The problems change** to graph families or sizes where the incumbents can no longer enumerate every maximum
   clique. Solver strength would then matter again.
4. **An independent miner starts doing well.** Today every independent averages below 2.55 and every incumbent
   above 2.60. An independent hotkey above about 2.62 in the logs means someone has found a way in.
5. **The check prints MARGINAL or GO.**

## History of the mechanism

From the repository's 22 commits.

| Date | Version | Change |
|---|---|---|
| 2025-08 | 0.0.0 | Launch. Scoring formula as it is today. Miners with more stake were sampled more often. |
| 2025-12-24 | 0.0.7 – 0.0.8 | 95% of validator weight sent to uid 0 (the owner); miners shared 5 – 6%. |
| 2026-01 | 0.0.9 – 0.0.11 | 700-node tier replaces the 100-node tier. Time limits introduced (7.5 – 30 s). |
| 2026-03-17 | 0.0.12 | 6 s limit added. Difficulties changed. |
| 2026-04-14 | 0.0.13 | Sigmoid weighting. Share to uid 0 cut to 50%. |
| 2026-04-25 | 0.0.14 | Share to uid 0 removed. Stake on the owner's validator no longer raises sampling odds. |
| 2026-04-29 | 0.0.15 | All miners sampled with equal probability. |
| 2026-05-10 | 0.0.16 | 900-node tier. Difficulties 0.7 – 1.0. Power transform (top half gets 80%). |
| 2026-07-22 | 0.0.17 | SDK compatibility only. |

Scoring has never looked at coldkeys or IPs. The only coldkey-aware rule there has been was in sampling, until April
2026: a coldkey's stake on the owner's validator was split across its miners when computing their sampling odds.

## Limits of this analysis

- The data covers 72 hours from the two validators that publish logs. Only the owner's validator matters for
  incentive, and it is one of the two.
- Incumbents are treated as static. They could change behaviour, in either direction.
- The fleet results are simulations with incumbent behaviour held fixed. The crossover near 125 – 140 miners has
  not been observed in practice.
- Exact proofs of optimality cover only 300-node graphs at density 0.70 – 0.73. Everything larger or denser relies
  on heuristic enumeration, mine and the incumbents'.
- Whether the six coldkeys are one owner or separate parties sharing an allocator cannot be told from this data.
- The pruning rule (lowest emission among non-immune miners is deregistered first) is inferred from seven
  replacements seen in the logs, not read from the chain's source.
- Dollar figures use spot prices on the day and ignore slippage, which is negligible at these volumes against a pool
  of 7,800 TAO.
