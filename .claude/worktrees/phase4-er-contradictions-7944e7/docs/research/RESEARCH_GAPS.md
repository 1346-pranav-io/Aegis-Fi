# Research Gaps

Status: DRAFT · Version 0.1.
**These are working hypotheses about gaps, NOT established findings.** No literature is cited here because none has yet been located and verified. Each gap must be confirmed (or discarded) by a systematic literature review performed by the research-analyst, with every citation checked at source. Placeholders: `[CITE-NEEDED]`.

## How a gap is confirmed
1. Define search strings and venues; log them in `docs/research/literature_log.md` (to be created in Phase 1).
2. Screen and record inclusion/exclusion.
3. For each gap: list the closest prior work, state precisely what it does not do, and mark the gap `CONFIRMED`, `PARTIAL`, or `REJECTED`.
4. Update the contribution statements in the paper accordingly. Reject any contribution already covered by prior work.

## Candidate gaps
| ID | Candidate gap (hypothesis) | Contribution | Why we suspect it | Status | Review questions |
|---|---|---|---|---|---|
| G1 | AML research largely evaluates detection (transaction/account classification), while end-to-end *investigation* quality (case-file correctness, evidence completeness) is rarely measured | 5 | Detection datasets provide per-transaction labels, not case files | UNCONFIRMED `[CITE-NEEDED]` | Are there investigation-level benchmarks or metrics? |
| G2 | LLM-agent systems for financial crime may lack enforced claim-to-evidence provenance with independent verification | 2 | LLM pipelines often produce free-text summaries | UNCONFIRMED `[CITE-NEEDED]` | Which systems enforce citation constraints? How is verification done? |
| G3 | Adaptive allocation of specialist analyses under cost budgets in AML investigation is not systematically compared to fixed pipelines | 1 | Typical designs are fixed workflows | UNCONFIRMED `[CITE-NEEDED]` | Related: multi-agent routing, tool-selection, cost-aware planning outside AML |
| G4 | Entity resolution uncertainty is typically resolved before downstream analysis, discarding uncertainty | 3 | ER is commonly a preprocessing step | UNCONFIRMED `[CITE-NEEDED]` | Work on uncertainty-aware/probabilistic ER and downstream propagation |
| G5 | Contradictions between data sources and between generated statements are rarely modelled as first-class objects in investigation tools | 3 | Hypothesis from tool design norms | UNCONFIRMED `[CITE-NEEDED]` | Related: fact-conflict detection, knowledge-graph inconsistency |
| G6 | Treatment of LLM self-reported confidence and model scores as probabilities without calibration evidence in decision-support settings | 2/3 | Common practice concern | UNCONFIRMED `[CITE-NEEDED]` | Calibration literature for LLMs and for fraud/AML scoring |
| G7 | Validated analyst feedback loops with safeguards against noisy/adversarial feedback in AML are under-studied | 4 | Feedback often assumed clean | UNCONFIRMED `[CITE-NEEDED]` | Label-noise, active learning, feedback loops/bias in alert triage |
| G8 | Temporal robustness (drift, calibration decay) is often evaluated with random splits (our own study can address only short-horizon effects, about 18 days) | 5 | Random splits leak time | UNCONFIRMED `[CITE-NEEDED]` | Which AML/fraud papers use temporal splits? Concept drift in finance |
| G9 | Public AML data lack ground truth for investigations, limiting reproducible comparison | 5 | Data sensitivity | UNCONFIRMED `[CITE-NEEDED]` | Availability of synthetic generators with typology annotations |

## Topic areas for the literature review (search topics, not citations)
- Graph-based and GNN AML detection; temporal graph methods.
- Alert triage and case prioritisation in AML operations.
- LLM agents for investigation/analysis; tool-use and planning; multi-agent orchestration.
- Attribution, citation and faithfulness in generation; claim verification / fact-checking; entailment-based verification.
- Self-verification versus independent verification; correlated errors across LLM instances.
- LLM and classifier calibration.
- Entity resolution with uncertainty; record linkage in financial/KYC settings.
- Contradiction detection in knowledge bases.
- Human-in-the-loop learning, active learning, label noise.
- Temporal evaluation, concept drift, dataset shift in fraud/AML.
- Synthetic financial transaction generation and its validity.
- Regulatory expectations on explainability/human oversight (reading regulatory texts directly, not secondary summaries).

## Risks to the research plan
- A gap may be partly covered by existing work → narrow or drop the claim.
- Gap G1/G9 depends on obtaining or constructing investigation-level ground truth → see RESEARCH_METHODOLOGY §3.4.
- A "negative" outcome (no improvement) is a valid contribution if rigorously measured.
