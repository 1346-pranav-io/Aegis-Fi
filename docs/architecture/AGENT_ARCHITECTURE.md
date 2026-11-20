# Agent Architecture

Status: DRAFT · Version 0.1 · Decisions: D-05, D-06, D-07, D-08, D-09, D-16, D-21.

## 1. Two separate agent worlds
| | Runtime investigation agents | Development subagents |
|---|---|---|
| Purpose | Perform investigations inside the product | Help engineers build the product |
| Defined in | `docs/architecture/AGENT_SPECIFICATIONS.md`, code under `backend/src/aegis/capabilities/` | `.claude/agents/*.md` |
| Run by | LangGraph runtime, in worker process | Claude Code, in the developer's session |
| Data access | Via tool gateway, per-agent allow-list | Repo files only; never real data |
| Never | Reuse a dev subagent | Be imported or invoked by product code |

## 2. Definition of "agent" here (D-05)
An agent is a **capability with a typed contract**: `AgentTask → AgentResult`, a declared tool allow-list, declared permissions, budgets, and acceptance criteria. Whether it uses an LLM is an implementation detail declared in its spec.

| Agent | Implementation class |
|---|---|
| Master Orchestrator | Deterministic policy (v1) ; learned policy variant later |
| Transaction | Deterministic |
| Graph | Deterministic |
| Behaviour | Deterministic (statistical) |
| Intelligence | Deterministic lookup + optional LLM extraction |
| Investigation | LLM-based |
| Verification | Deterministic checks + LLM entailment |
| Contradiction | Deterministic detection + optional LLM explanation proposal |
| Reporting | Template + LLM drafting under citation constraint |

## 3. Runtime model (LangGraph)
- One **investigation graph** per run. Nodes: `plan`, `dispatch`, `collect`, `synthesise`, `verify`, `contradict`, `resolve_entities`, `report`, `finalise`.
- **State** (`InvestigationState`, Pydantic): case id, `as_of`, plan, completed steps, evidence ids, claim ids, open contradictions, budget counters, status. State holds **references** (IDs) to evidence, not blobs.
- **Checkpointing:** state persisted to Postgres after each node (LangGraph checkpointer backed by Postgres) → resumable (FR-ORC-05).
- **Determinism:** nodes are pure functions of (state, recorded inputs); non-determinism confined to LLM calls, which are recorded/replayable.
- **Dispatch:** `runtime` looks up capability by name in a registry; capabilities are not imported by each other.
- LangGraph is used for state, branching, and checkpointing only; no domain logic lives in graph definitions.

## 4. Orchestration and adaptive task allocation (research contribution 1)
Orchestration policy interface:
```python
class OrchestrationPolicy(Protocol):
    name: str; version: str
    def plan(self, state: InvestigationState, catalog: AgentCatalog) -> list[AgentTask]: ...
    def replan(self, state: InvestigationState, new_evidence: list[EvidenceRef]) -> list[AgentTask]: ...
```
Policies:
1. `fixed_pipeline` — every case runs the same sequence (baseline).
2. `rule_adaptive` (v1 default) — task selection from typed signals (e.g. high fan-out ⇒ Graph first; behavioural drift ⇒ Behaviour; watchlist hit ⇒ Intelligence), with a cost-aware budget allocator and stopping rule (marginal evidence gain below threshold).
3. `learned` (later) — contextual-bandit or learned ranker over the same action space, trained only on logged runs and validated on a later temporal window. Included only if it beats (2) under the evaluation framework.

All policies emit the same `Plan` schema, so they are directly comparable. Policy name/version is stored on every run.

## 5. Tool gateway and permissions
- Capabilities request data/actions only via the **tool gateway**: a registry of typed tools (e.g. `get_transactions(window)`, `get_neighbourhood(entity, hops)`, `create_evidence(...)`, `create_claim(...)`).
- Each agent has an **allow-list**; each tool has a declared **permission class**: `READ_SOURCE`, `READ_DERIVED`, `WRITE_EVIDENCE`, `WRITE_CLAIM`, `WRITE_PROPOSAL`, `CALL_LLM`. No agent holds `WRITE_SOURCE` (that permission class does not exist for agents).
- Tool arguments and results are validated against schemas; failures return typed errors.
- Tool calls are logged to `agent_step.tool_calls`.
- The tool gateway has no tool that executes consequential actions; it can only create `PendingApproval` records (D-09).

## 6. LLM usage controls (D-16)
Single gateway providing: pseudonymisation/restoration, per-step and per-run token and cost budgets, timeouts, bounded retries, structured output (JSON schema) with validation and repair limit, prompt versioning (prompts are files in `capabilities/*/prompts/` with version + hash), record/replay cache, and logging.
Prompts treat all retrieved text as untrusted data (prompt-injection posture, NFR-SEC-05).

## 7. Evidence-grounding contract
1. Specialist outputs become `Evidence` automatically (runtime wraps `AgentResult`).
2. Investigation Agent may only reference evidence IDs supplied in its context; a post-check rejects claims citing unknown IDs.
3. Claims are structured (`subject`, `predicate`, `object/value`, `time_scope`, `cited_evidence`) to enable deterministic verification checks.
4. Verification is independent (D-08): separate capability, separate prompt, inputs limited to claim + cited evidence.
5. Reporting uses only `VERIFIED` (and flagged `PARTIALLY_SUPPORTED`, in a separate section) claims.

## 8. Confidence semantics (D-06)
- `score`: raw model output, uncalibrated.
- `calibrated_probability`: nullable; requires calibration artefact id + evaluation set id.
- `self_reported_confidence`: ordinal LLM label; never combined arithmetically with scores; evaluated as a research variable (does it correlate with verification outcomes?).
- ER link `score` is a similarity/match score; status (`PROPOSED/CONFIRMED/REJECTED`) is the decision.

## 9. Human-in-the-loop points
Plan approval is **not** required (investigation is read-only analysis). Approval required for D-09 actions. Analysts may add guidance to a run; guidance is recorded as an input, not as evidence.

## 10. Termination and safety
Run ends when: plan exhausted, stopping rule satisfied, budget exhausted, or unrecoverable failure. Maximum steps and depth are hard limits. No recursive agent spawning. Failed steps do not block independent steps. Final status ∈ {`COMPLETE`, `PARTIAL`, `BUDGET_EXHAUSTED`, `FAILED`}.

## 11. Failure handling summary
| Situation | Behaviour |
|---|---|
| Specialist returns error | step failed; orchestrator may retry once or re-plan around it |
| Investigation produces claim citing invalid evidence | claim rejected before storage; logged |
| Verifier unavailable | claims stay `UNVERIFIED`; report excludes them from findings |
| Contradiction detected after report drafted | report regenerated as new version |
| Prompt-injection suspicion (tool call outside allow-list) | call denied, audit event, step continues |

## 12. Observability
Every `agent_step` stores: agent, version, input hash, output ref, tool calls, LLM call ids, tokens, cost, duration, status. Runs can be replayed from these records.
