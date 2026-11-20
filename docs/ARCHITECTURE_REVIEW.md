# Architecture Review — Phase 0 Foundation

Reviewer: Principal Architect · Date: 2026-11-11 · Status: **awaiting owner approval**

## 1. Completeness vs. brief
| Deliverable | Files | State |
|---|---|---|
| 1 Requirements | `docs/SYSTEM_REQUIREMENTS.md`, `FUNCTIONAL_REQUIREMENTS.md`, `NON_FUNCTIONAL_REQUIREMENTS.md` | Written |
| 2 Architecture | `docs/architecture/{SYSTEM_ARCHITECTURE,DATA_FLOW,AGENT_ARCHITECTURE,TECHNOLOGY_DECISIONS}.md` | Written |
| 3 Agent specs | `docs/architecture/AGENT_SPECIFICATIONS.md` (9 agents) | Written |
| 4 Data | `docs/architecture/{DATA_SCHEMA,EVIDENCE_MODEL}.md` | Written |
| 5 Research | `docs/research/{RESEARCH_METHODOLOGY,RESEARCH_GAPS,EVALUATION_FRAMEWORK}.md` | Written; no citations/results by design |
| 6 Subagents | `.claude/agents/` — 9 definitions | Written |
| 7 Standards | `CLAUDE.md`, `docs/{DEVELOPMENT_STANDARDS,TESTING_STRATEGY,IMPLEMENTATION_ROADMAP}.md` | Written |
| Decision log | `decisions.md` | Written (U-01..09, D-01..21, Q-01..08) |

Not implemented (by instruction): any application code, dependencies, repository initialisation, deployment.

## 2. Consistency check (what was verified)
- One agent set (AG-01..09) is used identically in AGENT_ARCHITECTURE, AGENT_SPECIFICATIONS, SYSTEM_ARCHITECTURE (`capabilities/`) and the roadmap.
- Evidence/claim/verification vocabularies match between EVIDENCE_MODEL, AGENT_SPECIFICATIONS, FUNCTIONAL_REQUIREMENTS (FR-VER-03) and DATA_SCHEMA.
- Score vs calibrated probability treated identically in D-06, DATA_SCHEMA §9, EVIDENCE_MODEL §6, FR-DET-02/03, NFR-USE-02.
- Gated actions (D-09) appear in FR-CASE-03, FR-ER-02, FR-HITL-02/05, and AGENT_SPECIFICATIONS (AG-09 `REQUEST_APPROVAL` only).
- Research questions RQ1–RQ5 map to contributions, requirements and experiments E1–E8 (RESEARCH_METHODOLOGY §6, EVALUATION_FRAMEWORK §5).

Known weaknesses of this check: it was a manual read-through, not automated. The documentation-engineer should run an ID/cross-reference audit once (dangling FR/NFR/D/Q references), and the principal-architect should repeat it at every phase exit.

## 3. Key architectural judgements (all PROPOSED)
1. **Modular monolith** over microservices (D-01).
2. **Three of nine "agents" are deterministic** modules behind an agent interface (D-05). This honours "no agents where functions suffice" while keeping the orchestrator and the adaptive-allocation research question uniform. *Owner should confirm this reading of "agent".*
3. **DB-enforced immutability** rather than convention (D-04).
4. **Independent verifier by input isolation**, not by assumption of independence (D-08).
5. **No aggregated "case confidence"** in v1 (EVIDENCE_MODEL §6) — deliberately avoids the false-probability trap.

## 4. Unresolved decisions (need owner)
| ID | Decision | Blocking |
|---|---|---|
| Q-01 | Real data ever? | Privacy scope, retention policy |
| Q-02 | May pseudonymised data go to Claude API? | LLM gateway design; local-model fallback need |
| Q-03 | Primary benchmark dataset | Phase 1 adapter; paper claims |
| Q-04 | Regulatory regime for report template | Report format, typology list |
| Q-05 | Venue and deadline | Phase prioritisation (e.g. skip UI?) |
| Q-06 | LLM/compute budget | Orchestrator defaults; ablation size |
| Q-07 | Python 3.12 only? | Minor |
| Q-08 | Local Docker only? Git remote/CI host? | CI design |
| New | Who performs human annotation / acts as analyst reviewers, if any? | Validity of HITL claims (RQ4) |
| New | May `HUMAN_NOTE` evidence support suspicion findings? (EVIDENCE_MODEL §11) | Policy |
| New | Is the UI (Phase 5) required for the paper, or is API + CLI enough? | Scope/time |

## 5. Risks
| ID | Risk | Likelihood / Impact (architect judgement, unmeasured) | Mitigation |
|---|---|---|---|
| R-01 | No suitable public dataset with investigation-level truth | High / High | Layered gold construction (METHODOLOGY §3.4); disclose synthetic-data limits; Phase 1 verification spike |
| R-02 | Claimed research gaps already covered by prior work | Medium / High | Systematic review before Phase 6; narrow contributions |
| R-03 | LLM verifier shares errors with generator | Medium / Medium | Isolation, deterministic checks first, ablation, disclosed limitation |
| R-04 | LLM cost/rate limits constrain experiments | Medium / Medium | Budgets, record/replay, smaller ablations, Q-06 |
| R-05 | NetworkX scale limits on large benchmark graphs | Medium / Medium | Windowed ego-subgraphs; measured size limit; revisit tech (decision entry) |
| R-06 | LangGraph API churn / Postgres checkpointer compatibility | Medium / Medium | Isolate behind `runtime/`; version-compat gate in Phase 1 |
| R-07 | Scope too large for timeline | High / High | Roadmap allows dropping UI, Intelligence agent, learned policy, PyG without breaking core claims |
| R-08 | Nondeterministic LLM outputs undermine reproducibility | High / Medium | Record/replay, repeated runs with variance reporting |
| R-09 | Simulated analyst feedback weakens RQ4 | High / Medium | Disclose; seek real reviewers |
| R-10 | Immutability triggers complicate legitimate corrections | Low / Medium | `supersedes` pattern; tested |
| R-11 | Docs drift from code | Medium / Medium | documentation-engineer audits; docs-with-change rule |
| R-12 | Dev subagent write-scope not technically enforced (frontmatter lacks path scoping) | Medium / Medium | Scope in prompts + review; consider hooks/permissions in `.claude/settings.json` in Phase 1 |

## 6. Dependencies
External: Claude API access/quota (A-02); Docker; chosen datasets; compatible wheels for Python 3.12 (xgboost, torch, PyG). Internal sequencing: see ROADMAP dependency graph.

## 7. Recommended next actions
1. **Owner review of `decisions.md`** — approve / reject / modify D-01..D-21; answer Q-01..Q-08 (at minimum Q-02, Q-03, Q-05, Q-06).
2. Decide whether Phase 1 begins; if yes, approve the Phase 1 scope in the roadmap.
3. Initialise a git repository (not done; no repository exists yet) and, optionally, add `.claude/settings.json` permission rules to hard-scope subagent writes (R-12).
4. Run a documentation cross-reference audit (documentation-engineer).
5. Start the literature review and dataset verification in parallel with Phase 1.

## 8. Verdict
Foundation is internally consistent as far as manual review can establish. **Recommend: APPROVE WITH OWNER DECISIONS** on the items in §4 before implementation. No implementation has begun and none will until approved.
