# Implementation Roadmap

Status: DRAFT · Version 0.1. **No phase beyond 0 starts without owner approval.** Durations are deliberately omitted (unknown team size, Q-05). Each phase ends with a phase-exit review (principal-architect + security-reviewer + owner sign-off).

## Phase 0 — Engineering foundation (this deliverable)
Docs, decisions log, subagents, standards. **Exit:** owner approves/revises decisions D-01..D-21 and answers Q-01..Q-08 as needed.

## Phase 1 — Platform skeleton and data foundation
- Repo scaffolding, tooling (uv, ruff, mypy, pytest, import-linter), Docker Compose (postgres, mlflow), CI definition.
- `common`, `domain` models, `store` (schemas, roles, immutability triggers, migrations), job queue, audit log, structured logging.
- Ingestion framework + **dataset verification spike** (Q-03): verify candidate datasets, write adapter for the chosen one, manifests and hashes.
- Dependency compatibility checklist (TECHNOLOGY_DECISIONS).
- Literature log started (research-analyst).
- **Exit:** T-INV-01, 03 (repo layer), 13, 14 green; ingestion of fixture + chosen dataset sample; one-command local start (SAC-07).

## Phase 2 — Deterministic analytics and detection baselines
- `analytics`: transaction, graph, behaviour modules; typology rule packs v1.
- `ml`: features, XGBoost/sklearn baselines (B0, B1), temporal split utilities, MLflow registry, calibration tooling.
- Signals/alerts/cases workflow (FR-DET, FR-CASE).
- Experiment E1 (detection baselines) and measured performance of analytics (replaces NFR-PERF targets).
- **Exit:** fixture-exact analytics; T-INV-03, 09 green; E1 reproducible from config; graph size limit measured (R-05).

## Phase 3 — Evidence layer and agent runtime (core research contributions 1–2)
- `evidence` store, provenance walk, claim model; `llm` gateway (pseudonymisation, budgets, record/replay).
- `runtime` (LangGraph), tool gateway, capability registry, `fixed_pipeline` and `rule_adaptive` policies.
- Capabilities: Transaction, Graph, Behaviour wrappers; Investigation; Verification; Reporting; (Intelligence if prioritised).
- **Exit:** T-INV-02, 04, 05, 06, 07, 10, 11, 12, 15, 16 green; E2 (grounding), E3 (verification), E4 (orchestration) run on fixtures; first measured results recorded (whatever they are).

## Phase 4 — Entity resolution and contradictions (contribution 3)
- `entities` ER pipeline (blocking, scorer, proposals, merge/unmerge); dependency flags on claims.
- Contradiction detectors and agent; closure gating.
- Perturbation generator (seeded duplicates/contradictions).
- **Exit:** T-INV-17, 18 green; E5, E6 run.

## Phase 5 — HITL, API completion, UI (contribution 4)
> **Owner decision Q-11: no web UI. API + CLI only. The Next.js UI bullet below is dropped (see decisions.md).**
- Feedback capture and validation; approvals workflow; RBAC; async runs API; report export.
- ~~Next.js UI: queue, case view, evidence/claims, contradictions, approvals.~~ (dropped, Q-11)
- Feedback → retraining path (candidate artefact, approval to activate).
- **Exit:** T-INV-08 green; UI E2E; E7 (feedback) run, with simulated-feedback disclosure unless real reviewers available.

## Phase 6 — Evaluation, temporal robustness, paper artefacts (contribution 5)
- Complete investigation-level metrics; rolling-origin temporal evaluation; learned orchestration policy (optional, only if baseline results justify); human annotation study if approved.
- Full experiment matrix E1–E8; repro package; tables/figures regeneration.
- **Exit:** every reported number traces to a stored run; reproduction from clean checkout verified; limitations section complete.

## Phase 7 — Extensions (optional, decided by owner after Phase 6)
- Payment-fraud typologies and dataset adapter (same contracts).
- PyG/GNN models vs. baselines (D-20).
- Hardening: performance work against measured bottlenecks; scaling decisions (e.g. graph store) driven by evidence.

## Dependency graph
```
P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7
          └──── P2 baselines feed P6 comparisons
P1 dataset verification gates P2, P3 experiments
P3 llm gateway gates P4 (ER explanations), P5 (feedback)
```
Research track runs in parallel from P1 (literature, gaps) and must complete gap confirmation before Phase 6 drafting.

## Role assignment (dev subagents)
| Phase | Lead | Support |
|---|---|---|
| 1 | backend-architect | test-engineer, security-reviewer, research-analyst |
| 2 | ml-graph-engineer | backend-architect, test-engineer |
| 3 | agent-systems-engineer | backend-architect, ml-graph-engineer, test-engineer, security-reviewer |
| 4 | ml-graph-engineer, agent-systems-engineer | test-engineer |
| 5 | backend-architect, frontend-engineer | security-reviewer, test-engineer |
| 6 | research-analyst, ml-graph-engineer | all |
| all | principal-architect reviews; documentation-engineer maintains docs | |

## Change control
Scope changes between phases → `decisions.md` entry, owner approval. Phase exit criteria are testable; a phase is not "done" on opinion.
