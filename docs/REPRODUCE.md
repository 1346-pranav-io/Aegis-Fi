# Reproducing the stored results

Status: DRAFT · Phase 6 (D-31). Everything here is local; nothing is downloaded by a script.

## What "reproduced" means here
- **Preflight (fast, automatic):** `uv run python scripts/repro_check.py` from `backend/`. It checks the raw files against the hashes in `docs/research/DATASET_VERIFICATION.md`, the split spec against the hash stored in each result file, the database's dataset manifest against the stored results (when `AEGIS_APP_URL` is set), and that every number in the results documents traces to a stored run (`scripts/check_results_trace.py`, index in `docs/research/RESULTS_INDEX.json`).
- **Re-run (slow, manual):** the commands below. E1 was re-run once and came out bit-identical (see `docs/research/E1_RESULTS_RUN1.md`). E8 and the others have **not** been re-run from a clean checkout; until that is done, "reproduction from a clean checkout verified" (roadmap Phase 6 exit) is **not claimed**.

## Clean-checkout steps
1. `git clone` the repository; install `uv`; run `uv sync` in `backend/`.
2. Put `HI-Small_Trans.csv`, `HI-Small_accounts.csv`, `HI-Small_Patterns.txt` in `data/raw/ibm-aml/` (obtain them yourself from the dataset's publisher; the hashes are in `DATASET_VERIFICATION.md`).
3. `uv run python scripts/dev.py up`, then `migrate`, then ingest with `scripts/ingest_ibm.py` (see its docstring).
4. Run the experiments from `backend/` with `AEGIS_APP_URL` set to the read-only application login:
   - `uv run python ../experiments/e1_detection.py` (about 4 minutes)
   - `uv run python ../experiments/e8_temporal.py` (see the results document for the run time)
   - others: `e1b_rules_v2.py`, `e1c_shuffled_labels.py`, `e1d_full_permutation.py`, `e2_graph.py`, `e3_behaviour.py`, `e5_er.py`, `e6_contradictions.py`, `p3_fixture_experiments.py`
5. Compare to the stored result files in `experiments/results/`. Seeds are fixed in the scripts; results are expected to match to the printed precision, but a different CPU count or library version can change tree-model results. The `code_hash` and `dataset_manifest_hash` in each result file say which code and data produced it.

## Result files
New runs write new timestamped files; old files stay. If a result in a document is replaced by a newer run, update `RESULTS_INDEX.json` (`check_results_trace.py --rehash`) in the same commit and say why in the document.
