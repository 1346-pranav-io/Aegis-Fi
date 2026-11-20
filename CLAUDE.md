# Aegis-FI — Project Instructions

Adaptive Evidence-Grounded Intelligence System for End-to-End Financial Crime Investigation (AML primary, payment fraud secondary). Research-grade and engineering-grade.

## Current phase
**Phase 1 — platform skeleton and data foundation: DONE (declared by owner 2026-11-11, with carried-forward gaps listed in `decisions.md` and `docs/PHASE1_EXIT_REVIEW.md`).**
**Phase 2 — analytics, detection baselines, alerts/cases: DONE (declared by owner 2026-11-12, with carried-forward gaps in `decisions.md` and `docs/PHASE2_EXIT_REVIEW.md`). Phase 3 (evidence layer, agent runtime, LLM gateway) is NOT authorised: do not start it without owner approval, and confirm the pseudonymisation design (Q-02) before any Claude API call.**
**Phases 3 and 4 — evidence layer, LLM gateway, agent runtime, entity resolution, contradictions: DONE (declared by owner 2026-11-12, open items in `docs/PHASE3_4_EXIT_REVIEW.md` section 5). Live Claude calls remain disabled (D-29); enabling them needs a separate owner decision.**
**Phase 5 — HITL feedback, API with RBAC, async runs, report export, CLI (no UI): DONE (declared by owner 2026-11-12, open items in `docs/PHASE5_EXIT_REVIEW.md` section 4).**
**Phase 6 — evaluation, model-free scope only (D-31, owner 2026-11-12): DONE for that scope (declared by owner 2026-11-12; roadmap exit gaps in `docs/PHASE6_EXIT_REVIEW.md` sections 1 and 4 accepted). E2-E4 on a real model, E7 and retraining are NOT authorised. Live Claude calls remain disabled (D-29). Phase 7 is NOT authorised.**
**Phase 7 (D-32, owner 2026-11-13): GNN comparison (E9) and measured hardening done; payment-fraud adapter skipped by owner. DONE (declared by owner 2026-11-13; results in `docs/research/E9_GNN.md` and `P7_HARDENING.md`). Optional `gnn` dependency group installed. No further phase is defined in the roadmap; anything new needs owner approval.**

**UI (D-34 to D-37, owner 2026-11-13): a web UI in `frontend/` over the existing API; no new endpoints. D-36 (interactive pass) and D-37 (dashboard redesign) await owner review.**

## Read first
`decisions.md` (why) → `docs/architecture/SYSTEM_ARCHITECTURE.md` → `AGENT_ARCHITECTURE.md` → `EVIDENCE_MODEL.md` → `docs/DEVELOPMENT_STANDARDS.md`.

## Governance
- **Major decisions go to the owner with an explanation.** Record them in `decisions.md` as `PROPOSED`; only the owner moves them to `APPROVED`.
- Do not change approved architecture without a new, superseding `decisions.md` entry stating the reason.
- Do not delegate unrestricted repo edits; dev subagents (`.claude/agents/`) have scoped write access. Dev subagents are never used by product code.
- No external deployment. Local Docker Compose only.

## Non-negotiable invariants
1. Source financial records are immutable (insert-only; enforced in DB). LLMs/agents have no write path to them.
2. Every claim cites evidence IDs; evidence carries provenance and a content hash; unsupported claims never appear in report findings.
3. Verification is independent: verifier sees only claim + cited evidence.
4. `score` ≠ probability. `calibrated_probability` is null unless a registered calibration artefact exists. LLM confidence is ordinal and "self-reported".
5. Consequential actions (filing recommendation, case closure, restriction recommendation, non-exact entity merge, model activation, feedback-to-training) require a stored human approval.
6. Deterministic function before agent; agent before LLM. No microservices.
7. All LLM calls via the `llm/` gateway only (pseudonymised, budgeted, recorded). Anthropic SDK imported nowhere else.
8. `as_of` on every analysis; temporal splits for research results.
9. Never fabricate literature, datasets, metrics, or results. Unverified = say so.

## Layout (planned)
`backend/src/aegis/{common,domain,store,ingestion,analytics,entities,evidence,ml,llm,capabilities,runtime,services,api,eval}` · `frontend/` · `experiments/` · `docs/` · `deploy/` · `.claude/agents/`.
Dependency direction: `common → domain → store/analytics/llm → entities/evidence/ml → capabilities → runtime → services → api` (enforced by import-linter).

## Standards (summary — full text in `docs/DEVELOPMENT_STANDARDS.md`)
Python 3.12, uv, ruff, mypy strict, pytest, Pydantic v2 at all boundaries, SQLAlchemy 2 + Alembic, structlog JSON logs, typed errors (no bare `except`), versioned model artefacts (MLflow), config files for experiments with seeds. Every change: tests + docs.

## Commands (Phase 1; no make/just on this platform — one script instead)
Run from `backend/`: `uv run python scripts/dev.py <lint|type|arch|test|test-int|check|up|down|migrate>`.
`up` starts local Postgres (needs Docker running); `test-int` needs it. `check` = lint + type + arch + unit tests.
Ingest the IBM data: `scripts/ingest_ibm.py` (see its docstring). Do not invent other commands.

## Working rules
- Prefer editing existing modules over adding new ones; check the component map before creating a module.
- Ask before: new dependency, new table/schema change, new agent, new external call.
- Report test failures with output; never claim success without running the checks.
