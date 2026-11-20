# Data Flow

Status: DRAFT · Version 0.1. Names refer to modules in SYSTEM_ARCHITECTURE.md and tables in DATA_SCHEMA.md.

## Flow 1 — Ingestion
```
file(s) → adapter → Pydantic validation ─ok→ hash → INSERT source.* + batch manifest
                              └─bad→ quarantine (reason code)
```
- Idempotent by record content hash + batch hash.
- Nothing downstream runs on a batch until its manifest is `COMPLETE`.
- Failure: partial batch is rolled back as a unit (single transaction per chunk, batch status `FAILED`, quarantine retained).

## Flow 2 — Detection and alerting
```
source.* (as_of) → ml/analytics detectors → derived.signals (score, reason codes, rule/model version)
                                          → evidence (type=SIGNAL) → alerts → case assignment rules → case OPEN
```
Scores stored as `score`; `calibrated_probability` only if calibration artefact exists (D-06).

## Flow 3 — Investigation run
```
case OPEN → API enqueues job → worker picks job → runtime builds LangGraph state
 loop until done/budget:
    Orchestrator.plan(state) → [AgentTask…]
    for each task: tool gateway → capability → AgentResult
        → evidence items created (immutable, provenance) → state updated
    Orchestrator.replan(state)
 Investigation Agent: evidence → claims (each cites evidence ids)
 Verification Agent: (claim, cited evidence) → VerificationResult
 Contradiction Agent: evidence/claims → contradictions
 Entity resolution proposals → pending review
 Reporting Agent: verified claims → report draft (version n)
 case → PENDING_REVIEW
```
Every arrow that creates data writes an `agent_step` record and, where applicable, an `audit` event.

## Flow 4 — Human review and approval
```
analyst opens case → views claims/evidence/contradictions
   accept/reject/edit claim ─────────────→ feedback record (+ claim status annotation; evidence untouched)
   confirm/reject ER link ───────────────→ ER decision record
   request consequential action ─────────→ PendingApproval
approver approves/denies ────────────────→ Approval record → state transition (e.g. CLOSED)
```
No external effect results from approval in v1; the outcome is a recorded decision and exported report.

## Flow 5 — Feedback validation and learning
```
feedback records → validation (role, consistency, duplicates, conflicts) → VALIDATED | QUARANTINED
VALIDATED + approved for training (D-09f) → dataset manifest vN → ml/ retrain → candidate artefact
candidate → offline evaluation on later temporal window → approval → active
```
Feedback never alters earlier evidence or source records; it adds labelled data for subsequent versions.

## Flow 6 — Experiments
```
experiment config + dataset manifest + seeds → eval runner
   → (detection experiments) ml/analytics on temporal splits
   → (investigation experiments) full runtime with policy/ablation switches, LLM record/replay
   → metrics → MLflow + eval tables → report tables for paper
```
Experiment writes go to `eval.*` tables only; production tables are not touched.

## Data classification
| Data | Mutability | Sent to LLM? |
|---|---|---|
| source.* | immutable | only pseudonymised excerpts selected by a capability |
| evidence.* | immutable, supersede-only | pseudonymised excerpts |
| derived.* | recomputable, versioned | no (summaries via evidence) |
| workflow.* | mutable state with audit | no |
| audit.* | append-only | never |

## Failure paths
Covered per flow in SYSTEM_ARCHITECTURE.md §6. Principle: **fail visible, keep partial evidence, never fabricate.**
