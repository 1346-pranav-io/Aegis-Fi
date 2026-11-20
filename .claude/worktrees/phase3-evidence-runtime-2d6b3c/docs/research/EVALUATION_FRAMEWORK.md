# Evaluation Framework

Status: DRAFT · Version 0.1. **No results exist. No metric values or dataset properties are asserted.** Everything below is a definition of *what will be measured and how*.

## 1. Principles
1. Temporal splits only for headline results (D-12, D-17). Random splits only for clearly labelled sanity checks.
2. Tune on validation; test touched once per pre-registered config.
3. Unit of analysis for investigation metrics = case.
4. Report uncertainty (bootstrap CIs) and sample sizes; report failures and excluded cases.
5. LLM systems: record/replay; repeated live runs for variance.
6. Scores are evaluated as scores; calibration is evaluated separately and only for outputs claimed to be probabilities.

## 2. Datasets — candidates (UNVERIFIED)
The following are candidates *recalled by the architect* and **must be verified in Phase 1** (existence, current location, licence, size, schema, label semantics, whether typology/pattern annotations exist, time span) before any is selected. Do not cite properties until verified.
| Candidate | Domain | Potential use | Verify |
|---|---|---|---|
| IBM synthetic AML transaction datasets | AML (synthetic, with laundering labels) | Primary candidate for detection + pattern-based investigation truth | availability, licence, whether pattern annotations are provided, size variants |
| Elliptic Bitcoin transaction dataset | Illicit crypto transactions graph | Graph/temporal detection robustness | licence, time-step structure, label coverage, relevance to bank AML |
| PaySim-style synthetic mobile-money data | Fraud/AML-like synthetic | Fraud extension / sanity | provenance, realism limits |
| IEEE-CIS fraud detection data | Card fraud | Fraud extension only | licence terms, redistribution |
| Project-generated synthetic cases | Investigation truth | Seeded perturbations, entity/contradiction tests | Generator design must be documented and validated |
Selection criteria: timestamps present (for temporal splits); labels separable from features; structure sufficient for graph analysis; licence permits research use; reproducible download script. Decision recorded in `decisions.md` (Q-03).

## 3. Metric families
### 3.1 Detection-level (components B0/B1 and signals)
- Ranking: PR-AUC (primary under imbalance), recall@k alerts, precision@k, ROC-AUC (secondary).
- Cost-sensitive: workload–recall curve (analyst alerts reviewed vs. true cases found).
- Calibration (only where `calibrated_probability` exists): Expected Calibration Error with stated binning, reliability diagrams, Brier score.

### 3.2 Investigation-level (core of contribution 5)
Defined per case, against the gold structure (§4):
| Metric | Definition (to be implemented as code with tests) |
|---|---|
| Finding recall | Share of gold facts (key entities, transactions, pattern structure) present in verified claims |
| Finding precision / **unsupported-claim rate** | Share of reported findings not entailed by evidence / not matching gold facts, reported separately |
| Citation validity | Share of claim–evidence links where evidence truly supports claim (adjudicated by gold/human sample) |
| Citation coverage | Share of report finding sentences with ≥1 valid citation (should be 100% by construction; verify) |
| Typology accuracy | Correct primary typology hypothesis vs. gold (where annotation exists) |
| Evidence completeness | Share of gold-required evidence items retrieved |
| Contradiction recall/precision | Against seeded or known contradictions |
| ER error propagation | Rate of reported claims built on incorrect entity links |
| Cost | Tokens, dollars, wall time per case; steps per case |
| Efficiency | Quality metric per unit cost (curve across budgets) |

### 3.3 Verification quality (RQ2)
On seeded-error and labelled-support sets: verifier precision, recall, F1 for detecting unsupported claims; false-rejection rate of correct claims; per-error-type breakdown; agreement with human adjudication (if available).

### 3.4 Confidence analysis (RQ3 / H3c)
- Does self-reported LLM confidence discriminate between verified and unsupported claims? (AUROC of confidence label vs. verified outcome; reliability by bin.)
- Does ER `match_score` discriminate correct from incorrect links? (calibration only if a calibrator is trained.)
Results reported as findings, whatever the direction.

### 3.5 HITL (RQ4)
Simulated or real feedback: improvement on later window vs. baseline; effect of injected feedback-noise rates with and without validation; feedback acceptance/quarantine rates; reviewer agreement.

### 3.6 Short-horizon temporal robustness (RQ5)
**Splits (D-24):** train Sep 1-6, validation Sep 7-8, test Sep 9-10, stress slice Sep 11-18 (about 59% positive, reported separately, never in headline metrics). Detection unit is the transaction with point-in-time features (D-25).
Dataset span is about 18 days (IBM HI-Small, measured). Windows are therefore day-level (e.g. 3-day blocks, sizes declared in advance), not months. Because laundering is rare (0.1%) and window counts are small, report per-window positive counts and wide intervals, and compare window-to-window variation against any apparent trend before calling it degradation.
- Rolling-origin evaluation across chronological windows (day-level).
- Per-window metrics for rules, ML, full system.
- Drift indicators (population stability index or equivalent, declared in advance) vs. performance change.
- Calibration decay (ECE per window) vs. ranking-metric decay.
- Rank correlation of system orderings under detection vs. investigation metrics (H5a).

## 4. Gold construction (see RESEARCH_METHODOLOGY §3.4)
- Gold structure from generator/labels where available; documented mapping to claim vocabulary (`structure.predicate` set).
- Seeded perturbation catalogue with versioned generator and seeds.
- Optional human annotation protocol: guidelines, ≥2 annotators on a subset, agreement statistic reported, adjudication process.

## 5. Experimental matrix (initial)
| Experiment | Systems | Varies | Primary metric | RQ |
|---|---|---|---|---|
| E1 detection baselines | B0, B1 | model, features | PR-AUC, recall@k | RQ5 |
| E2 grounding | B2 vs B3 | grounding | unsupported-claim rate | RQ2 |
| E3 verification | B3 vs B4 (+ isolation off) | verifier mode | unsupported claims reaching report; false rejection | RQ2 |
| E4 orchestration | B3/B4 fixed vs A1 vs A2 | policy, budget | quality-per-cost curves | RQ1 |
| E5 ER | auto-merge vs proposal-only | ER mode, perturbation rate | ER error propagation | RQ3 |
| E6 contradictions | detector on/off, LLM-only | detection mode | contradiction recall/precision | RQ3 |
| E7 feedback | no feedback / validated / unvalidated-noisy | feedback regime | later-window performance | RQ4 |
| E8 short-horizon temporal | all | day-level window | metric change vs. window noise, calibration change | RQ5 |

## 6. Harness requirements (FR-EXP)
Config-driven, deterministic splits, MLflow logging, record/replay, automatic table/figure generation, refuses to run if manifest hash mismatch, writes only to `eval.*`.

## 7. Reporting checklist (paper)
Datasets & verification status; split definitions; baselines; hyperparameter search budget; seeds & repeats; CIs; ablations; failure analysis; cost; limitations; reproducibility artefacts; explicit statement of which results are on synthetic data.
