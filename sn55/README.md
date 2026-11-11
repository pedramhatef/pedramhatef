# SN55 (NIOME) — feasibility study + CPU-first miner

Full findings: **[REPORT.md](REPORT.md)**. Verdict: **PILOT GO** (one cheap single-UID mainnet test).

```
harness/   synth.py        synthetic contract/reference builder (labelled; coords real GRCh38)
           official.py     runs the UNMODIFIED official scorer pinned at subnet-niome 0c03fef
solver/    core.py         candidate PAM-site finder + balanced submission builder
           fastscore.py    fast stage1/2/5 reimplementation (matches official TWS/DFF to 1e-6)
           optimize.py     tilt-swept weighted-balanced allocation
miner/     solve.py        standalone contract+ref+genome -> submission
           process_task.py drop-in body for neurons/miner.py (instant ack, async build, retry)
           DEPLOY.md       2-vCPU CPU-only deployment, isolation + financial caps
bench/     run.py          strategy benchmark (held-out seeds)
           economics.py    live per-rank USD economics + break-even
           persistence*.py 30-day operator-level rank persistence
tools/     snapshot.py analyze_mg.py history.py   read-only chain tooling
evidence/  metagraph_now.json weights_now.json history_30d.jsonl bench.json
```

Reproduce (Python 3.12, deps pinned to subnet-niome `uv.lock`):
`CHR11_FA=/path/chr11.fa PYTHONPATH=. python bench/run.py`

Read-only study: no registration, no TAO spent, no production system touched.
