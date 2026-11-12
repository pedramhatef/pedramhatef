# cliqueminer

A maximum-clique miner for Bittensor subnet 83 (CliqueAI), plus the analysis toolkit used to decide whether running
it is worthwhile.

**Read [FINDINGS.md](FINDINGS.md) first.** Short version, as of 2026-11-12: the miner works and matches the best
clique size any incumbent returns, but a newcomer cannot reach the top 20 under the current mechanism. The limit is
the reward's *diversity* term combined with how the existing miners coordinate their answers, not solver quality or
hardware. `python -m analysis.contestability` re-measures this from public data whenever you want to check again.

## What is here

| Path | What it is |
|---|---|
| `solver/` | C++17 solver: multi-threaded randomized local search plus swap closure. Returns the best size, the distinct maximum cliques it found, and distinct maximal cliques one vertex smaller. `make -C solver` builds `libcliquesolver.so` (used by the miner) and `clique_cli` (benchmarks). |
| `cliqueminer/` | The miner. `engine.py` (decode → solve under a deadline → choose → verify → fallback), `policy.py` (which clique to return), `handler.py` (concurrency), `miner.py` (Bittensor axon wiring). |
| `analysis/` | Offline tools on public data: `wandb_logs.py` (download validator logs), `dataset.py` (exact re-implementation of the validator's scoring), `market.py` (who runs the miners, are their answers coordinated), `backtest.py` (what a newcomer would have earned), `contestability.py` (one-command go / no-go), `chain.py` (metagraph snapshot). |
| `tests/` | 58 tests, including a full local round trip through the real Bittensor axon driven by CliqueAI's own validator requester and scored by its own scoring class. |

## Check whether the subnet is contestable

Needs only Python 3.12, numpy and a C++ compiler; no wallet, no registration.

```bash
make -C solver
python -m analysis.wandb_logs --steps 3000 --out data/     # ~3 min, ~200 MB of public validator logs
python -m analysis.contestability data/*.jsonl.gz          # ~10 min on 4 cores; prints GO / MARGINAL / NO-GO
```

`GO` means the reward this miner would have earned on those rounds clears the reward level of the current rank-20
miner. `MARGINAL` means only a perfect predictor of the other miners' answers would. `NO-GO` means not even that.

## Run the miner

Do this only if the check above says it is worth it. The miner needs the CliqueAI repository for the wire protocol.

```bash
git clone https://github.com/toptensor/CliqueAI && pip install -r CliqueAI/requirements.txt && pip install -e CliqueAI --no-deps
make -C solver
python -m cliqueminer.miner --netuid 83 --subtensor.network finney \
    --wallet.name <coldkey> --wallet.hotkey <hotkey> --axon.ip <public-ip> --axon.port <port> \
    --neuron.autoupdate 0 --blacklist.force_validator_permit --logging.info
```

- `--neuron.autoupdate 0` is required: the upstream updater runs `git pull` in the current directory and restarts.
- Have it serving before you register. The first query arrives about 72 minutes after registration and unanswered
  queries are averaged into the score.
- Hardware: any 4–8 core CPU machine. Every problem tier is solved in well under the shortest time limit on a few
  cores. A GPU does nothing for this workload.
- Options: `--miner.threads`, `--miner.budget_fraction`, `--miner.max_budget`, `--miner.safety_margin`,
  `--miner.policy {mirror,always_best}`, `--miner.min_validator_stake`.

## Tests

```bash
python -m pytest tests                                              # solver, codec, policy, scoring (47 tests)
PYTHONPATH=/path/to/CliqueAI python -m pytest tests                 # adds the Bittensor integration and round-trip tests (58)
```

## Design notes

- **Solver.** The graphs are dense Erdős–Rényi-like (290–900 nodes, edge density 0.70–0.95). A DLS-MC-style search
  with no vertex penalties (random expansion, plateau swaps, perturbation by forcing in a random vertex) finds a maximum
  clique in milliseconds to a few seconds per core. Every maximal clique visited at the best size, and one below, is
  recorded by Zobrist hash; a closure pass then enumerates all maximum cliques within one or two vertex swaps.
- **Answer choice.** `policy.py` mirrors the size rule the incumbents use (answer one vertex below the maximum when
  the graph has fewer than 12 / 8 / 5 / 3 maximum cliques on the 300 / 500 / 700 / 900-node tier) and picks uniformly
  at random inside that size class. Nothing observable at request time predicts which cliques the incumbents occupy.
- **Safety.** The chosen clique is re-verified in Python against an independent decode of the graph (valid, maximal,
  in range, no duplicates) before it is sent. If the native solver is missing, fails, or returns anything that does
  not verify, a greedy maximal clique is returned instead. A reply is never knowingly invalid.
- **Deadline.** Solve time is `min(max(0.5 × limit, 0.4 s), 8 s)`, and never more than `limit − 2 s`, which leaves the
  rest for transfer and verification. Overlapping requests from different validators share the cores.
