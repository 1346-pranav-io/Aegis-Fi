# Research Methodology

Status: DRAFT · Version 0.1.
**Integrity rules:** (1) no literature is cited from memory — every citation must be located and checked by the research-analyst before inclusion; placeholders read `[CITE-NEEDED: topic]`. (2) No results, metrics or dataset properties are claimed until measured/verified. (3) Negative and null results are reported.

## 1. Research problem
AML investigation is labour-intensive; automated detection yields many alerts whose investigation depends on assembling evidence across transactions, networks, behaviour and external context. LLM-based agents can assemble and narrate such evidence but risk unsupported statements, unreliable self-assessed confidence, entity-merge errors, and brittleness over time. Aegis-FI studies whether *evidence-grounded, independently verified, uncertainty-aware, human-in-the-loop* multi-agent investigation produces more reliable case files than less constrained alternatives.

## 2. Research questions and hypotheses
Hypotheses are directional expectations to be **tested, not assumed**. Null outcomes are acceptable findings.

**RQ1 — Adaptive orchestration (contribution 1).** Does adaptive, budget-aware task allocation improve investigation quality per unit cost compared with a fixed pipeline?
- H1: At equal budget, `rule_adaptive` achieves higher investigation-level quality (EVALUATION_FRAMEWORK §3) than `fixed_pipeline`, or equal quality at lower cost.
- H1b: A `learned` policy (if built) improves over `rule_adaptive` on a later temporal window.
- Arch link: AGENT_ARCHITECTURE §4; FR-ORC-04.

**RQ2 — Evidence grounding and independent verification (contribution 2).** Does mandatory evidence citation plus independent verification reduce unsupported claims in case files?
- H2a: Grounded generation yields a lower unsupported-claim rate than ungrounded LLM summarisation of the same inputs.
- H2b: Independent verification further reduces unsupported claims reaching the report, at measurable false-rejection cost.
- H2c: Verifier isolation (no investigator reasoning) detects more seeded errors than a verifier given the investigator's reasoning.
- Arch link: EVIDENCE_MODEL §4–5; D-07, D-08.

**RQ3 — Confidence-aware entity resolution and contradiction handling (contribution 3).** Does retaining ER uncertainty and surfacing contradictions improve case-file correctness and analyst trust compared with hard merging and contradiction-blind synthesis?
- H3a: Proposal-only fuzzy linking with dependency flags reduces claims built on wrong merges versus auto-merge at a score threshold.
- H3b: Deterministic contradiction detection recovers a higher share of seeded contradictions than LLM-only detection, at lower cost.
- H3c: Whether self-reported LLM confidence or ER match score is informative about correctness is an open empirical question (measure calibration; no prior assumption).
- Arch link: EVIDENCE_MODEL §6–8.

**RQ4 — Human-in-the-loop feedback (contribution 4).** Does validated analyst feedback improve later investigation or detection performance, and does validation prevent harm from noisy feedback?
- H4a: Models retrained on validated feedback improve on a later temporal window versus no feedback.
- H4b: Unvalidated feedback (with injected label noise) degrades performance more than validated feedback.
- Arch link: DATA_FLOW Flow 5; FR-HITL-03.

**RQ5 — Investigation-level evaluation and short-horizon temporal robustness (contribution 5).** Do detection-level metrics adequately reflect investigation quality, and how do components degrade over a short horizon (days) when evaluated on later windows than they were built on? *(Reframed 2026-11-11, D-22: the primary dataset spans only about 18 days, so long-term drift is out of scope and must not be claimed.)*
- H5a: Rankings of systems by detection metrics differ from rankings by investigation-level metrics.
- H5b: Performance on later day-level windows differs from performance on the window used for development; the change may differ between components (rules vs. ML vs. agents), and calibration may change differently from ranking quality. Whether any change is larger than window-to-window noise is itself tested; with about 18 days, a null result is a plausible and acceptable finding.
- Scope limit: no claim about months-to-years drift, seasonality, or regime change. The paper must state this explicitly.
- Arch link: D-17; FR-EXP-04.

## 3. Study design
### 3.1 Data
Candidate public/synthetic datasets are listed in EVALUATION_FRAMEWORK §2 and **must be verified (availability, licence, schema, label semantics) in Phase 1** before selection (Q-03). Where no investigation-level ground truth exists, it is constructed (see §3.4) and the construction is described as a limitation.

### 3.2 Systems compared
- **B0** rules only · **B1** supervised model only (XGBoost) · **B2** fixed-pipeline specialists + ungrounded LLM summary · **B3** fixed-pipeline + grounded claims · **B4** + verification · **A1** `rule_adaptive` full system · **A2** `learned` policy (optional).
Ablation switches (FR-EXP-05): grounding on/off; verification on/off; verifier isolation on/off; ER mode (auto-merge / proposal-only); contradiction detection on/off; policy; feedback on/off.

### 3.3 Protocol
1. Fix dataset manifest(s) and temporal split: train / validation (tuning, calibration) / test windows in chronological order; later "shift" windows for robustness.
2. Tune only on validation; touch test once per pre-registered configuration (config committed before the run).
3. For LLM-based systems: record all calls; report repeats (≥ pre-declared number) to estimate variance; replay for deterministic reanalysis.
4. Pre-declare primary metrics and comparison tests; secondary analyses labelled exploratory.
5. Report seeds, code commit, image digest, manifest hashes.

### 3.4 Investigation-level ground truth
> **D-23 (approved 2026-11-11):** for IBM HI-Small, investigation-level gold comes only from the 370 pattern blocks (typology + exact member transactions). The 1,968 laundering transactions outside any block are used for detection-level metrics only. ER tests use seeded name perturbations. See DATASET_VERIFICATION.md.
Because correct case files are rarely public, use a layered approach (each layer's limits stated):
- **Synthetic-truth cases:** where the generator provides the laundering structure (e.g. which transactions/accounts form a pattern), the "gold" findings are the structural facts. Claims are scored against them.
- **Seeded perturbations:** inject controlled errors (wrong amount/date/entity, unsupported leaps, contradictory attributes, near-duplicate entities) into inputs/evidence to measure verifier and contradiction recall/precision.
- **Human review (if feasible):** small expert-annotated sample; annotator guidelines, agreement statistic, and conflicts reported. Requires owner decision on who annotates (open).

### 3.5 Statistical treatment
Unit of analysis = case. Report effect sizes with bootstrap confidence intervals; paired comparisons across systems on the same cases (e.g. paired bootstrap or Wilcoxon signed-rank for non-normal metrics, chosen and declared in advance); correct for multiple comparisons on the primary family. No claim of significance without the test being reported.

### 3.6 Threats to validity (to be extended)
- Synthetic data may not reflect real laundering behaviour (external validity).
- LLM versions change; API non-determinism (replay mitigates, but results are tied to a model version).
- Same-vendor verifier shares failure modes with generator (D-08 caveat).
- Seeded errors may be easier/harder than naturally occurring errors.
- Label leakage via features or via prompts (labels are separated, D-04 / DATA_SCHEMA §9).
- Small expert-annotation samples.
- Analyst feedback in experiments will be simulated unless real analysts participate — simulated feedback must be declared as such.

## 4. Reproducibility package
Code at tagged commit; locked environment; Docker image digest; dataset manifests with hashes (data fetched by documented script, not redistributed if licence forbids); experiment configs; MLflow export; LLM record sets (pseudonymised); scripts that regenerate every table and figure.

## 5. Ethics and responsible use
No real customer data in repo; pseudonymisation before external calls; system produces *decision support*, with human approval for consequential actions; documented limitations on false positives/negatives and demographic or geographic bias — bias analysis planned if the dataset has relevant attributes (to be checked).

## 6. Mapping: architecture ↔ contribution
| Contribution | Primary components | Key requirements | Key experiments |
|---|---|---|---|
| 1 Adaptive orchestration | `runtime` policies, AG-01 | FR-ORC-* | RQ1 |
| 2 Grounding + verification | `evidence`, AG-06/07/09 | FR-EVD-*, FR-VER-*, FR-RPT-* | RQ2 |
| 3 ER + contradictions | `entities`, AG-08 | FR-ER-*, FR-CON-* | RQ3 |
| 4 HITL feedback | `services.feedback`, `ml` | FR-HITL-* | RQ4 |
| 5 Evaluation + temporal | `eval` | FR-EXP-* | RQ5 |
