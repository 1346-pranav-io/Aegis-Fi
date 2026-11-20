# Phase 6: exit review (model-free evaluation, D-31)

Date: 2026-11-12 · Author: principal architect · **Verdict: the authorised scope (D-31) is delivered; the roadmap's full Phase 6 exit is NOT met** because E2-E4 on a real model, E7, the learned policy and the annotation study were out of scope. **Declared DONE by the owner on 2026-11-12 for the D-31 scope, accepting the gaps below.**

## 1. Roadmap exit criteria
| Criterion | State |
|---|---|
| Every reported number traces to a stored run | **Partly.** `RESULTS_INDEX.json` + `scripts/check_results_trace.py` check 33 numbers (all of `E8_TEMPORAL.md`, E1's headline table) against hashed result files, which are now tracked in git (they were ignored). Other result documents are generated from their files but not indexed |
| Reproduction from a clean checkout verified | **No.** `scripts/repro_check.py` verifies raw-file hashes, the split spec, the database manifest and traceability; `docs/REPRODUCE.md` lists the re-run steps. E1 was re-run once (bit-identical); E8 and the rest were not |
| Limitations section complete | Drafted: `docs/research/LIMITATIONS.md` (experiment map, data, non-supporting findings, statistics, LLM-dependent parts, platform) |
| Full matrix E1-E8 | E1, E5, E6, E8 run model-free; E2-E4 only on a stub; **E7 not run** |
| Investigation-level metrics | A detection-derived proxy (pattern-block hit@k, account AP). No agent-based investigation was evaluated |
| Temporal robustness, rolling origin | Done (E8), but the result is that **degradation cannot be separated from training size** in an expanding-origin design |

## 2. What was built
`aegis/eval/temporal.py` (bootstrap AP, PSI, within-day horizon trend, block hit@k, account-level aggregation; unit-tested), `experiments/e8_temporal.py` and `e8_report.py`, run `e8_20261112T082104Z` (849 s, 125 cells), `docs/research/E8_TEMPORAL.md`, `LIMITATIONS.md`, `RESULTS_INDEX.json`, `scripts/check_results_trace.py`, `scripts/repro_check.py`, `docs/REPRODUCE.md`. 402 unit tests pass; ruff, mypy strict and import contracts clean. No migrations, no new dependencies (scipy and pandas were already installed), no agents, no external calls.

## 3. Findings (see E8_TEMPORAL.md for the numbers)
- B1 beats the rules on every evaluated day; B0 is near chance.
- PR-AUC depends mostly on how many training days the model had, not on horizon; within one origin the day-to-day spread is comparable to the bootstrap interval. **No temporal degradation is claimed.**
- One day (Sep 9) is lower for every origin: a day effect.
- Feature drift (PSI) did not predict performance change in the expected direction; confounded with origin.
- The detection metric and the block-based proxy agree and order the systems identically on every day. Weak evidence for H5a, because the proxy uses the same scores.

## 4. Open items and decisions for the owner
1. A fixed-length sliding-window design would separate horizon from training size but leaves very few cells with 10 days of data. Run it anyway, or accept the current statement?
2. Clean-checkout reproduction of E8 (about 15 minutes of compute plus data ingestion) has not been done.
3. E2-E4 on a real model, E7 and retraining need a separate decision (D-29, D-31).
4. Research-gap confirmation (R-02) is still required before paper drafting.
5. `experiments/results/` is now tracked (about 200 KB) so that indexed numbers can be verified from a checkout.
