# System Requirements

Status: DRAFT for owner approval · Version 0.1 · See `decisions.md` for decision IDs (D-xx, U-xx).

## 1. Purpose
Aegis-FI supports financial-crime analysts in investigating suspected money laundering (primary) and payment fraud (secondary extension). It produces **evidence-grounded, independently verified investigation case files** and records human decisions to improve future behaviour. It is also a **reproducible research platform** for the five contributions in the project brief.

## 2. Scope
**In scope (v1):** batch ingestion of transaction/party data; deterministic detection and risk signals; alert→case workflow; multi-agent investigation; evidence store; verification; entity resolution; contradiction handling; human review and approval; report drafting; evaluation harness; local deployment.

**Out of scope (v1):** real-time (sub-second) transaction screening; connection to live banking core systems; automatic filing with regulators; automatic account restriction; production deployment or hosting; use of real customer data in the research repository.

## 3. Stakeholders and users
| Role | Needs |
|---|---|
| AML analyst | Triage alerts, receive grounded case summaries, accept/reject findings |
| Senior analyst / approver | Approve consequential actions, review contradictions |
| Model-risk reviewer | Inspect model versions, calibration, drift, evaluation |
| Auditor | Reconstruct any conclusion back to source records |
| Researcher | Run reproducible experiments and ablations |
| Administrator | Manage users, config, data retention |

## 4. System capabilities (summary; detail in FUNCTIONAL_REQUIREMENTS.md)
- SC-01 Ingest and validate financial records into an immutable store.
- SC-02 Compute deterministic transaction, graph and behavioural risk signals.
- SC-03 Raise alerts and open cases.
- SC-04 Orchestrate an adaptive, budgeted investigation across specialist agents.
- SC-05 Record every finding as evidence with provenance, and every statement as a claim linked to evidence.
- SC-06 Independently verify claims; flag unsupported claims.
- SC-07 Resolve entities with explicit uncertainty and reviewable merges.
- SC-08 Detect, represent and escalate contradictory evidence.
- SC-09 Capture human decisions and validated feedback.
- SC-10 Draft reports whose every assertion is cited.
- SC-11 Run versioned, reproducible experiments and investigation-level evaluation.

## 5. Constraints
- C-01 Stack fixed by owner (U-03); additions justified in TECHNOLOGY_DECISIONS.md.
- C-02 Source financial records are immutable; LLMs have no write path to them (D-04).
- C-03 No external deployment; local Docker Compose only (U-08).
- C-04 No real customer data; public/synthetic only unless owner decides otherwise (D-11, Q-01).
- C-05 LLM access only via the gateway with pseudonymisation (D-10, D-16).
- C-06 No fabricated results; all numbers in documents are targets or placeholders until measured.
- C-07 Model confidence is not a probability (D-06).

## 6. Assumptions (to validate)
- A-01 A suitable public/synthetic AML dataset with ground-truth laundering labels (and ideally pattern/typology annotations) is obtainable. *Unverified.*
- A-02 Claude API access, with sufficient quota, is available to the project. *Unverified.*
- A-03 Graph sizes for benchmark data fit in memory for NetworkX on a developer workstation. *Unverified; see R-05.*
- A-04 Ground truth at **investigation level** (what a correct case file contains) is not available publicly and must be constructed or approximated; method defined in EVALUATION_FRAMEWORK.md.

## 7. System-level acceptance criteria
| ID | Criterion | Verification |
|---|---|---|
| SAC-01 | Any statement in any generated report can be traced to source records through claim → evidence → source record hash | Automated provenance-walk test over all generated reports |
| SAC-02 | No code path allows an agent or LLM to UPDATE/DELETE source records | DB permission test + static import check |
| SAC-03 | No consequential action executes without a stored human approval | Integration tests per gated action (D-09) |
| SAC-04 | A run is reproducible: same inputs, config, seeds, model versions, recorded LLM responses ⇒ identical stored outputs | Replay test |
| SAC-05 | Every model artefact used has version, training data manifest hash, and metrics in the registry | Registry completeness check |
| SAC-06 | Unsupported claims never appear in the findings section of a report | Property test |
| SAC-07 | System runs end-to-end locally with one command on a clean machine | CI smoke test |
| SAC-08 | Evaluation harness produces all metrics in EVALUATION_FRAMEWORK.md from stored runs without manual steps | Harness test on fixture data |

## 8. Glossary
- **Alert** — a system-generated signal that something may merit review.
- **Case** — an investigation unit grouping alerts, entities, evidence, claims and decisions.
- **Evidence** — an immutable, provenance-stamped observation (a query result, model output, document excerpt).
- **Claim** — an assertion made by an agent or human that must cite evidence.
- **Verification** — independent check that cited evidence supports a claim.
- **Consequential action** — action with external or irreversible effect (see D-09).
- **as_of** — the timestamp bounding which data an analysis may see.
