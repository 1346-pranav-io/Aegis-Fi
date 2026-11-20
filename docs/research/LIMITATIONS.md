# Limitations and experiment status

Status: DRAFT · Phase 6 (D-31) · Written from the stored results and the phase exit reviews; claims here are limits, not findings. Paper drafting is still gated on research-gap confirmation (R-02).

## 1. Experiment map
The framework matrix (EVALUATION_FRAMEWORK §5) and the script names differ; this table maps them. "Model-free" = no LLM involved.

| Framework | Script / document | State |
|---|---|---|
| E1 detection baselines | `e1_detection.py`, `E1_RESULTS_RUN1.md`, `E1B_RULES_V2.md`, `E1CD_CONTROLS.md` | Run; model-free; re-run once bit-identical |
| (graph analytics, not in matrix) | `e2_graph.py`, `E2_GRAPH.md` | Run; latency/size limits and oracle-windowed block validation, not a detector evaluation |
| (behaviour analytics, not in matrix) | `e3_behaviour.py`, `E3_BEHAVIOUR.md` | Run; model-free |
| E2 grounding, E3 verification, E4 orchestration | `p3_fixture_experiments.py`, `P3_FIXTURE_EXPERIMENTS.md` | Run **on a stub LLM and fixtures only**; says nothing about Claude. Not run on a real model (D-29) |
| E5 entity resolution | `e5_er.py` | Run; **did not support the research claim** (section 3) |
| E6 contradictions | `e6_contradictions.py` | Run; seeded set, mostly by construction |
| E7 feedback | none | **Not run.** Needs retraining and model activation, not authorised (D-31) |
| E8 short-horizon temporal | `e8_temporal.py`, `E8_TEMPORAL.md` | Run; model-free |

## 2. Data
- One dataset, **synthetic** (IBM HI-Small, about 18 days, 0.1% laundering). Nothing here is evidence about real-world laundering or real institutions. The strongest detector signal comes from account and pair history; this may reflect generator artefacts (E1 §3).
- Only the HI-Small variant was used. Medium/Large variants and other datasets were not.
- 38% of laundering rows are in no pattern block, for a reason the dataset documents do not explain, so investigation-level proxies cover 62% of laundering rows.
- Labels are the generator's. There is no human annotation and no annotator-agreement estimate.

## 3. Findings that do not support, or only weakly support, a claim
- **RQ3 / E5 (entity-resolution uncertainty).** The pre-registered expectation (auto-merge produces silent errors that grow with perturbation) was not observed: silent propagation was 0.0000 in every cell. Post-hoc variants were not pre-registered. The shipped scorer's margin is thin. **The claim that retaining ER uncertainty reduces error is therefore narrowed to a design property (non-exact merges need human approval; links are reversible and dependents are re-flagged), not an empirical improvement.** No redesign was done (D-31).
- **RQ5 / E8.** No temporal degradation can be claimed. The expanding-origin design confounds horizon with training size, and day-to-day variation is comparable to the measured differences (E8_TEMPORAL.md).
- **H5a.** Detection-level and the block-based investigation proxy agree, but the proxy is derived from the same scores, so this is weak evidence.
- **E6.** Perfect precision and recall on a seeded set built with the same conventions as the detector; it says nothing about natural contradictions.

## 4. Statistics
- Window intervals are row bootstraps; laundering rows cluster in blocks, so they understate noise.
- Windows are single days with 300-540 positives each; seeds vary by about 0.02 PR-AUC.
- Single dataset split, no multiple-comparison correction, no pre-registration for E8 beyond the design fixed in `e8_temporal.py` before the run.
- Calibration: ECE is uninformative at a 0.1% base rate; calibrated mean versus base rate is reported instead.

## 5. LLM-dependent components
- Live Claude calls are disabled (D-29). Grounding, verification isolation, orchestration, prompt-injection containment and report narratives have been exercised only with a stub or recorded fixtures. The stub is a rubber stamp or canned output; measured behaviour describes the harness.
- Self-reported confidence is ordinal and was never compared with outcomes from a real model.

## 6. Platform and process
- Local Docker only; no deployment, load testing, or real identity provider. The API holds all database logins in one process (Phase 5 exit review §4).
- Security reviews were AI reviewer passes reading code; no human review and no penetration test.
- The reproduction preflight (`scripts/repro_check.py`) verifies data, split, database manifest and number traceability. **Reproduction from a clean checkout has not been performed** beyond E1's one re-run; `docs/REPRODUCE.md` lists the steps.
- Number traceability (`RESULTS_INDEX.json`) covers the E8 document and E1's headline table. Other result documents are generated from their result files but are not indexed.
