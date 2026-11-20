# Phases 3 and 4 - parallel plan

Date: 2026-11-12 · Owner instruction: "Start phase 3 and phase 4 together; if they are independent, later fix the correlations (if any)." · Status: shared contracts built; the two streams are **not yet launched** (waiting for owner confirmation of how to run them, see section 6).

## 1. Are the phases independent? Mostly, with seven coupling points
Phase 3 = evidence layer, LLM gateway, runtime, capabilities. Phase 4 = entity resolution (ER), contradictions, closure gating, perturbation generator. They share data and interfaces but not logic:

| # | Coupling | What each side needs | How it is decoupled |
|---|---|---|---|
| C1 | **Evidence / claim / contradiction tables** | Phase 4 contradictions point at evidence and claims; Phase 3 writes them | **Resolved now:** migration 0009 + `domain/evidence.py` (frozen shared contract, tested) |
| C2 | **ER link status read by Phase 3** | Investigation capability must flag claims that depend on unconfirmed links (FR-ER-03) | **Resolved now:** `domain/entities.py` (`ERLinkReader` protocol). Phase 3 uses an in-memory fake; Phase 4 implements the real one |
| C3 | **Contradiction agent runs inside the Phase 3 runtime** | needs the runtime's capability interface | **Resolved now:** `Capability`, `ToolGateway`, `AgentTask/AgentResult` in `domain/agents.py`; Phase 4 tests its agent against a fake `ToolGateway` |
| C4 | **LLM-backed explanation proposals** | Phase 4 may ask the gateway for an explanation | `ExplanationProposer` protocol (defined); Phase 4 uses a stub; real gateway wired at reconciliation |
| C5 | **Case closure blocked by open HIGH contradictions** (FR-CON, T-INV-18) | modifies the shared `workflow.case_guard` | Phase 4 owns that change; it only needs the `evidence.contradiction_current` view (exists). Serial point: only Phase 4 may touch `case_guard` |
| C6 | **Re-verification when an ER link is rejected** | a rejected link must re-flag dependent claims and queue re-verification (EVIDENCE_MODEL 7) | Phase 4 emits an event/record; Phase 3 consumes it. **Deferred to reconciliation**: no code until both sides exist |
| C7 | **Reports list open contradictions** (FR-CON-04) | Reporting capability reads contradictions | reads the existing `contradiction_current` view; no new dependency |

Independent work: Phase 3 (LLM gateway incl. pseudonymisation and record/replay, evidence store and provenance walk, tool gateway, LangGraph runtime, policies, capability wrappers, Investigation/Verification/Reporting) and Phase 4 (ER mentions/blocking/scoring/link records/merge/unmerge, deterministic contradiction detectors, perturbation generator, `ERLinkReader` implementation).

## 2. What was built to make them independent (done this session)
- `domain/evidence.py` - evidence, claim, verification, contradiction drafts and enums; claims need citations, probabilities need a calibrator, VERIFIED is impossible with a failed check, contradictions need two members.
- `domain/agents.py` - `AgentTask/Result`, `Budget`, `Usage`, `Permission` (no write-source permission exists), `Capability`, `ToolGateway`, `ExplanationProposer`.
- `domain/entities.py` - `LinkMethod`, `LinkStatus`, `ERLinkView`, `ERLinkReader`.
- Migration `0009_evidence_schema.sql` - evidence_item/input, claim, claim_evidence_link, verification_result, contradiction, contradiction_member, contradiction_status_event, plus current-state views. Insert-only; cross-row rules enforced in the database (inputs required for non-human evidence, claims cite same-case evidence, VERIFIED not allowed with a failed check, contradictions link two existing same-case items, legal status transitions, only humans move a contradiction past OPEN).
- Import-linter contracts for the new packages (layering, only `aegis.llm` may import the Anthropic SDK, capabilities independent of each other) and empty package stubs, so neither stream edits `pyproject.toml`.
- 13 new tests (8 unit, 5 integration); full suite now 134 tests (60 unit + 74 integration) green; ruff, mypy strict and 5 import contracts clean.

## 3. Ownership (to avoid edit collisions in one shared folder)
| | Stream A - Phase 3 | Stream B - Phase 4 |
|---|---|---|
| Packages | `evidence/`, `llm/`, `runtime/`, `capabilities/{transaction,graph,behaviour,intelligence,investigation,verification,reporting}/` | `entities/`, `capabilities/contradiction/`, `analytics/contradictions.py`, `eval/perturbation.py` |
| Migrations | `0010`-`0019` (runs, agent_step, jobs/checkpoints, reports, llm_call_record, ml.model_artefact) | `0020`-`0029` (ER tables, contradiction gating in `case_guard`) |
| Tests | `tests/**/test_*` for its packages | likewise |
| Docs | may append to `docs/PHASE3_*` | may append to `docs/PHASE4_*` |
| **Frozen shared files** (change only via the architect + a `decisions.md` entry) | `domain/evidence.py`, `domain/agents.py`, `domain/entities.py`, migration 0009, `pyproject.toml` import contracts, `common/*`, `store/migrate.py`, `APPROVALS.json` (each stream adds only its own entries) |

Every new migration touching protected objects needs an `APPROVALS.json` entry; **a stream may not self-approve** - it lists the migration for the owner.

## 4. Hard gates both streams must respect
- **No live Claude API call** until the owner confirms the pseudonymisation design (Q-02). Phase 3 builds the gateway with record/replay and a stub provider only; no API key is used.
- No external deployment; no secrets in the repo; all LLM text treated as untrusted.
- Source records and evidence are never updated; consequential actions only create approval requests (D-09).
- Deterministic first; LLM only where AGENT_SPECIFICATIONS says so.
- Every new rule tested; `scripts/dev.py check` and `test-int` green before reporting done; measured numbers only.

## 5. Reconciliation checklist (the "fix the correlations later" step)
1. Replace Phase 3's fake `ERLinkReader` with the Phase 4 implementation; run the shared tests.
2. Register `capabilities/contradiction` in the runtime registry; run a fixture investigation end to end.
3. Wire `ExplanationProposer` to the real gateway (recorded responses only).
4. Implement C6 (rejected link -> re-flag claims -> queue re-verification).
5. Check migration order 0010-0029 applies cleanly on a fresh database; re-run security review on all new migrations.
6. Re-run import contracts; add the `capabilities.*` independence check now that submodules exist.
7. Phase-exit reviews for 3 and 4 together: T-INV-02, 04-07, 10-12, 15-18.

## 6. Decision for the owner
Both streams would run as separate Claude sessions editing one folder at the same time. Because the repository has **no commits yet**, they cannot each get an isolated worktree. Options: (a) commit now so each stream gets its own worktree and a clean merge (recommended: merging is where "fix the correlations" would otherwise hurt); (b) run them in the shared folder with the ownership table above and no commits; (c) run them one after the other.
