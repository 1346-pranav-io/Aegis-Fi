# Functional Requirements

Status: DRAFT · Version 0.1 · Priority: M = must (v1), S = should, C = could (later phase).
Each requirement has a testable acceptance criterion (AC).

## FR-ING — Ingestion
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-ING-01 | Ingest transactions, accounts, parties and account–party links from files via a dataset adapter | M | Fixture batch loads; row counts match manifest |
| FR-ING-02 | Validate every record against the Pydantic schema; invalid rows go to a quarantine table with reason, never silently dropped | M | Bad-row fixture appears in quarantine with error code |
| FR-ING-03 | Compute and store a content hash per record and per batch; record batch manifest | M | Re-ingesting identical file is idempotent (no duplicates) |
| FR-ING-04 | Source tables are append-only; corrections create superseding rows | M | UPDATE/DELETE attempt by app role fails (SAC-02) |
| FR-ING-05 | Store `event_time` and `ingested_at` UTC for each record | M | Schema test |

## FR-DET — Detection and risk signals
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-DET-01 | Rule-based typology detectors (e.g. structuring, rapid movement, fan-in/fan-out, cycles) as pure functions with versioned rule sets | M | Unit tests on synthetic patterns; rule version stored with output |
| FR-DET-02 | Supervised baseline model (scikit-learn / XGBoost) producing an uncalibrated `score` per entity/transaction window | M | Model registered with manifest hash; score stored as `score`, not probability |
| FR-DET-03 | Optional calibration artefact producing `calibrated_probability`, with calibration error reported | S | Field is null unless artefact is registered |
| FR-DET-04 | All detectors take an `as_of` parameter and read only data with `ingested_at <= as_of` | M | Leakage test: future-injected record does not change output |
| FR-DET-05 | Alert creation from detector outputs with reason codes and links to supporting evidence | M | Each alert has ≥1 evidence ref |

## FR-CASE — Case management
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-CASE-01 | Group alerts into cases by deterministic rules (shared entity, time window) | M | Fixture alerts merge as specified |
| FR-CASE-02 | Case lifecycle: `OPEN → INVESTIGATING → PENDING_REVIEW → CLOSED` with every transition logged | M | Illegal transitions rejected |
| FR-CASE-03 | Case closure requires an approval record (D-09) | M | Closure without approval returns 403/409 |
| FR-CASE-04 | Priority queue ordering by score and age; ordering rule documented, not hidden | S | Deterministic ordering test |

## FR-ORC — Orchestration
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-ORC-01 | Master Orchestrator builds an investigation plan from the case's typed signals and available agents | M | Plan object validated against schema and stored |
| FR-ORC-02 | Plans respect per-run budgets: max steps, max wall time, max LLM tokens/cost | M | Run halts with `BUDGET_EXHAUSTED` status and partial results preserved |
| FR-ORC-03 | Orchestrator may re-plan after each step based on new evidence (adaptive) | M | Scenario test: a new signal adds a Graph task |
| FR-ORC-04 | Orchestration policy is pluggable: `fixed_pipeline`, `rule_adaptive` (v1 default), `learned` (later) | M | Config switch; all produce the same result schema |
| FR-ORC-05 | Run state is checkpointed so a failed run can resume | M | Kill-and-resume integration test |
| FR-ORC-06 | Every agent invocation recorded as an `agent_step` with inputs hash, outputs, tool calls, duration, cost | M | Provenance walk test |

## FR-AGT — Specialist capabilities
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-AGT-01 | Transaction Agent: deterministic aggregates, velocity, structuring/rapid-movement checks over a party/account/time window | M | Matches hand-computed fixtures |
| FR-AGT-02 | Graph Agent: build transaction graph, compute neighbourhood, cycles, centrality, community, flow-through metrics (NetworkX) | M | Matches reference results on small graphs |
| FR-AGT-03 | Behaviour Agent: compare party behaviour with its own history and peer group; emit deviation measures | M | Synthetic drift fixture is flagged |
| FR-AGT-04 | Intelligence Agent: look up sanctions/watchlist/PEP and other reference data supplied as local files; extract structured facts from supplied text documents | S | Lookup returns provenance of list version; extraction output validated by schema |
| FR-AGT-05 | Investigation Agent: synthesise evidence into claims and a hypothesised typology, citing evidence IDs | M | 100% claims carry ≥1 evidence ID (schema-enforced) |
| FR-AGT-06 | No agent can invoke a tool outside its allow-list | M | Negative tests per agent |

## FR-EVD — Evidence and provenance
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-EVD-01 | Each evidence item stores type, content, producer (agent/tool/version), inputs, `as_of`, content hash, created_at | M | Schema test |
| FR-EVD-02 | Evidence items are immutable; supersession creates a new item | M | Update attempt fails |
| FR-EVD-03 | Evidence can reference source record IDs and other evidence IDs (derivation DAG) | M | Provenance walk reaches source records |
| FR-EVD-04 | Claims link to evidence with a `support_role` (supports, partially supports, contextual, contradicts) | M | Schema + link test |

## FR-VER — Verification
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-VER-01 | Deterministic checks: evidence exists, hash intact, entities/amounts/dates in claim appear in cited evidence | M | Seeded-error fixtures caught |
| FR-VER-02 | Semantic check (LLM) of entailment, given only claim + cited evidence | M | Verifier input contains no investigator reasoning (test) |
| FR-VER-03 | Verification result per claim: `VERIFIED`, `PARTIALLY_SUPPORTED`, `UNSUPPORTED`, `CONTRADICTED`, `UNVERIFIABLE`, with rationale and checks run | M | Enum validated |
| FR-VER-04 | Claims not `VERIFIED` are excluded from report findings (SAC-06) | M | Property test |

## FR-ER — Entity resolution
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-ER-01 | Maintain entity mentions separate from resolved entities; links between them are versioned records with method, score, status | M | Un-merge restores prior state |
| FR-ER-02 | Auto-link only on exact verified identifiers; all fuzzy links are `PROPOSED` until approved | M | Test with near-duplicate names |
| FR-ER-03 | Uncertainty is propagated: claims depending on an unapproved link inherit "depends on unconfirmed link" | M | Claim shows dependency flag |
| FR-ER-04 | Merge/unmerge reversible and audited | M | Round-trip test |

## FR-CON — Contradictions
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-CON-01 | Deterministic contradiction detectors (e.g. conflicting attributes, impossible timelines, amount mismatch) | M | Fixture contradictions found |
| FR-CON-02 | Contradiction records link the conflicting evidence/claims and a status (`OPEN`, `EXPLAINED`, `RESOLVED_BY_HUMAN`, `DISMISSED`) | M | Lifecycle test |
| FR-CON-03 | LLM may propose an explanation; a proposal never resolves a contradiction without human action when it affects a finding | M | Test |
| FR-CON-04 | Open contradictions are shown in the case and reports | M | Report test |

## FR-HITL — Human-in-the-loop
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-HITL-01 | Analysts can accept / reject / edit claims and ER links with a reason | M | Feedback record created |
| FR-HITL-02 | Consequential actions create `PendingApproval`; only approver role can approve (D-09) | M | RBAC test |
| FR-HITL-03 | Feedback is validated before use as training/eval signal (consistency checks, reviewer role, conflict detection, optional second review) | M | Invalid feedback quarantined |
| FR-HITL-04 | Feedback never edits source records or original evidence | M | Test |
| FR-HITL-05 | Feedback-driven model updates produce a new versioned artefact requiring approval to activate | S | Registry test |

## FR-RPT — Reporting
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-RPT-01 | Generate case report from verified claims using a template; each sentence in findings carries claim/evidence citations | M | Citation coverage = 100% on findings |
| FR-RPT-02 | Report includes: summary, entities, timeline, findings, open contradictions, unsupported/unverified items, limitations, model versions, approvals | M | Template completeness test |
| FR-RPT-03 | Reports are versioned; each version is immutable | M | Test |
| FR-RPT-04 | Export as JSON and PDF/Markdown; no automatic external submission | M | Export test |

## FR-AUD — Audit and observability
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-AUD-01 | Append-only audit log for all state changes, approvals, LLM calls, data access | M | Tamper test: audit rows immutable |
| FR-AUD-02 | Structured JSON logs with correlation IDs (`case_id`, `run_id`, `step_id`) | M | Log schema test |

## FR-API — Interfaces
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-API-01 | REST API (FastAPI) with OpenAPI; all request/response bodies are Pydantic models | M | OpenAPI snapshot test |
| FR-API-02 | Authenticated, role-based endpoints | M | RBAC matrix test |
| FR-API-03 | Async long-running runs: submit → poll/stream status | M | Integration test |
| FR-API-04 | Web UI (Next.js/TypeScript): queue, case view with evidence/claims, approvals, contradictions | S (Phase 5) | E2E tests |

## FR-EXP — Experiments and evaluation
| ID | Requirement | P | AC |
|---|---|---|---|
| FR-EXP-01 | Experiment runner takes a config file, dataset manifest, seeds; logs to MLflow | M | Re-run reproduces metrics (within stated tolerance for non-deterministic LLM unless replay) |
| FR-EXP-02 | LLM call record/replay mode | M | Replay yields identical outputs |
| FR-EXP-03 | Detection-level and investigation-level metrics per EVALUATION_FRAMEWORK.md | M | Harness test |
| FR-EXP-04 | Temporal-split and drift evaluation utilities | M | Test with fixtures |
| FR-EXP-05 | Ablation switches for each research contribution | M | Config test |
