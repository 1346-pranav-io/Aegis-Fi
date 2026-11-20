# Agent Specifications (runtime agents)

Status: DRAFT · Version 0.1. These are **runtime** agents. Development subagents are in `.claude/agents/` and are unrelated (AGENT_ARCHITECTURE.md §1).
Schemas referenced here are defined in DATA_SCHEMA.md / EVIDENCE_MODEL.md.

## 0. Common contract

```python
class AgentTask(BaseModel):
    task_id: str; run_id: str; case_id: str
    agent: AgentName                 # enum of the nine agents
    as_of: datetime                  # UTC; data with ingested_at > as_of is invisible
    objective: str                   # short, from a fixed vocabulary per agent (task_type)
    task_type: str
    params: dict[str, Any]           # validated by per-task_type Pydantic model
    input_refs: list[Ref]            # evidence/claim/entity ids
    budget: Budget                   # max_steps, max_seconds, max_tokens, max_cost

class AgentResult(BaseModel):
    task_id: str
    status: Literal["OK", "PARTIAL", "FAILED", "BUDGET_EXHAUSTED", "DENIED"]
    evidence: list[EvidenceDraft]    # runtime persists these → Evidence
    proposals: list[Proposal]        # ER links, contradictions, follow-up task suggestions
    error: AgentError | None
    usage: Usage                     # duration, tokens, cost, tool calls
    version: str                     # capability implementation version
```
Rules common to all agents: no direct DB writes (tool gateway only); output validated against schema; deterministic agents are pure given inputs; every output item carries provenance; `as_of` enforced by the tool gateway, not by the agent's good behaviour.

Permission classes (see AGENT_ARCHITECTURE §5): `READ_SOURCE`, `READ_DERIVED`, `READ_EVIDENCE`, `WRITE_EVIDENCE`, `WRITE_CLAIM`, `WRITE_PROPOSAL`, `CALL_LLM`, `REQUEST_APPROVAL`.

---

## AG-01 Master Orchestrator
- **Purpose:** Decide which capabilities to run, in what order, within budget; decide when to stop; assemble final run status.
- **Implementation:** deterministic policy object (v1 `rule_adaptive`; baseline `fixed_pipeline`; later `learned`). Not an LLM. Runs inside the LangGraph `plan`/`replan` nodes.
- **Inputs:** `InvestigationState` (case summary, alert signals, completed steps, new evidence refs, budgets), agent catalog (capabilities, cost estimates, preconditions).
- **Outputs:** `Plan` (ordered/parallel `AgentTask`s with rationale codes), stop decision with reason code.
- **Tools:** read-only access to state and catalog. No data tools.
- **Permissions:** `READ_EVIDENCE` (metadata only), `WRITE_PROPOSAL` (plan). Cannot call LLM (v1).
- **Failure conditions:** plan refers to unknown agent/task type; budget infeasible; cycle (same task repeated beyond limit); no applicable agent.
- **Failure handling:** fall back to `fixed_pipeline`; mark run `PARTIAL`; record reason.
- **Acceptance criteria:** (1) every plan validates against schema; (2) never exceeds budget; (3) never schedules an agent without its preconditions; (4) deterministic given the same state (test); (5) identical result schema across policies; (6) on scenario fixtures, selects the specified agents (e.g. cycle-signal case schedules Graph before Behaviour).

## AG-02 Transaction Agent
- **Purpose:** Produce quantified transaction-level facts for a party/account/window.
- **Implementation:** deterministic (`analytics.transactions`).
- **Inputs:** subject (party/account id), window, thresholds from versioned rule pack.
- **Outputs:** Evidence of types `TXN_AGGREGATE`, `TXN_VELOCITY`, `TXN_STRUCTURING_CHECK`, `TXN_RAPID_MOVEMENT`, `TXN_COUNTERPARTY_SUMMARY`, each citing the source transaction ids used.
- **Tools:** `get_transactions(subject, window, as_of)`, `get_rule_pack(version)`, `create_evidence`.
- **Permissions:** `READ_SOURCE`, `READ_DERIVED`, `WRITE_EVIDENCE`.
- **Failure conditions:** no data in window (→ OK with empty-finding evidence, not failure); window too large (limit); missing currency conversion data.
- **Acceptance criteria:** results match hand-computed fixtures exactly; every number traceable to listed source ids; `as_of` leakage test passes; same input ⇒ same output hash.

## AG-03 Graph Agent
- **Purpose:** Characterise network structure around a subject.
- **Implementation:** deterministic (`analytics.graph`, NetworkX) on a windowed ego-subgraph.
- **Inputs:** subject, hops (≤ configured max), window, `as_of`.
- **Outputs:** Evidence `GRAPH_NEIGHBOURHOOD`, `GRAPH_CYCLES`, `GRAPH_FANIN_FANOUT`, `GRAPH_FLOW_THROUGH`, `GRAPH_COMMUNITY`, `GRAPH_CENTRALITY`; each with subgraph node/edge id list and algorithm + parameters.
- **Tools:** `get_edges(subject, hops, window, as_of)`, `create_evidence`.
- **Permissions:** `READ_SOURCE`, `READ_DERIVED`, `WRITE_EVIDENCE`.
- **Failure conditions:** subgraph exceeds size limit (NFR-PERF-05) → `PARTIAL` with truncation recorded explicitly (never silent); non-convergent algorithm.
- **Acceptance criteria:** matches reference results on fixtures; truncation always flagged; deterministic given seed for stochastic algorithms (seed recorded); respects `as_of`.

## AG-04 Behaviour Agent
- **Purpose:** Quantify deviation of a party's recent behaviour from its own history and peers.
- **Implementation:** deterministic statistical methods (`analytics.behaviour`), optionally uses registered ML models (scores labelled `score`).
- **Inputs:** subject, baseline window, test window, peer-group definition.
- **Outputs:** Evidence `BEHAV_SELF_DEVIATION`, `BEHAV_PEER_DEVIATION`, `BEHAV_MODEL_SCORE` (with model artefact id, `score`, `calibrated_probability` if available).
- **Tools:** `get_features(subject, windows, as_of)`, `get_model(artefact_id)`, `create_evidence`.
- **Permissions:** `READ_SOURCE`, `READ_DERIVED`, `WRITE_EVIDENCE`.
- **Failure conditions:** insufficient history (→ evidence `INSUFFICIENT_HISTORY`, not a guess); unregistered/unversioned model (refuse).
- **Acceptance criteria:** synthetic drift fixtures flagged; insufficient-history path tested; any model output includes artefact id; no probability label unless calibration exists.

## AG-05 Intelligence Agent
- **Purpose:** Bring in external/reference context: watchlists, sanctions, PEP lists, supplied documents (e.g. adverse media text, KYC notes).
- **Implementation:** deterministic lookups over versioned local reference files; optional LLM extraction of structured facts from supplied documents. No web access in v1.
- **Inputs:** entity identifiers/names (pseudonymised for LLM), document refs.
- **Outputs:** Evidence `REF_LIST_MATCH` (match score, list name+version, match method), `DOC_EXTRACTION` (fields extracted with source span references).
- **Tools:** `lookup_reference(list, query)`, `get_document(ref)`, `create_evidence`, `llm.extract` (gateway).
- **Permissions:** `READ_SOURCE`, `READ_DERIVED`, `WRITE_EVIDENCE`, `CALL_LLM`.
- **Failure conditions:** list version missing; fuzzy name matches with low score; extraction fails schema validation; document contains instruction-like text (treated as data, flagged).
- **Acceptance criteria:** every match cites list version; fuzzy matches always labelled as candidates with score; extraction fields each carry a source span; injected-instruction fixture does not alter behaviour. *Priority S in FR-AGT-04; may be deferred.*

## AG-06 Investigation Agent
- **Purpose:** Synthesise gathered evidence into structured claims, a typology hypothesis, and open questions.
- **Implementation:** LLM via gateway, with schema-constrained output.
- **Inputs:** case summary, list of evidence items (pseudonymised excerpts + ids), contradiction list, ER proposals status.
- **Outputs:** `Claim` drafts (structured: subject, predicate, value, time_scope, `cited_evidence`, `support_role` per citation, `self_reported_confidence` ordinal), `typology_hypotheses[]` (each as claim), `open_questions[]`, optional follow-up task suggestions to orchestrator.
- **Tools:** `get_evidence(ids)`, `create_claim`, `llm.generate_structured`.
- **Permissions:** `READ_EVIDENCE`, `WRITE_CLAIM`, `WRITE_PROPOSAL`, `CALL_LLM`. **No** `READ_SOURCE` — it reasons only over evidence (forces provenance).
- **Failure conditions:** cites non-existent / out-of-context evidence id; schema violation; budget exhaustion; hallucinated entities not in evidence.
- **Failure handling:** invalid claims rejected pre-storage and logged; repair retry ≤ N.
- **Acceptance criteria:** 100% stored claims cite ≥1 valid evidence id; claim values pass deterministic entity/number presence check (shared with Verification FR-VER-01) at ≥ the threshold set in Phase 3 on fixtures; no source access; replayable.

## AG-07 Verification Agent
- **Purpose:** Independently decide whether cited evidence supports each claim.
- **Implementation:** (1) deterministic checks; (2) LLM entailment check using a separate prompt, **given only** claim + cited evidence content (D-08).
- **Inputs:** claim ids (resolved to claim + cited evidence).
- **Outputs:** `VerificationResult` per claim: status ∈ {`VERIFIED`, `PARTIALLY_SUPPORTED`, `UNSUPPORTED`, `CONTRADICTED`, `UNVERIFIABLE`}, checks run, per-check outcome, rationale, verifier model/prompt version.
- **Tools:** `get_claim`, `get_evidence`, `verify_hash`, `llm.generate_structured`, `write_verification`.
- **Permissions:** `READ_EVIDENCE`, `READ_SOURCE` (only to re-check referenced source hashes), `WRITE_CLAIM` (verification annotation only), `CALL_LLM`.
- **Must not:** read investigator reasoning, prior LLM transcripts, or other claims' verification results when judging.
- **Failure conditions:** evidence missing/hash mismatch (→ `UNVERIFIABLE` + audit event); LLM unavailable (→ deterministic result only, status capped at `UNVERIFIABLE`/`PARTIALLY_SUPPORTED`; never `VERIFIED` without semantic check unless claim is purely numeric and fully deterministic-checkable).
- **Acceptance criteria:** seeded-error fixtures (wrong amount, wrong date, wrong entity, unsupported leap) detected at rates measured and reported (no preset claim); input-isolation test proves no investigator context reaches verifier; never `VERIFIED` when deterministic check fails.

## AG-08 Contradiction Agent
- **Purpose:** Find and represent conflicts among evidence, claims, and entity attributes.
- **Implementation:** deterministic detectors (attribute conflict, impossible timeline, amount mismatch, claim vs. evidence conflict flagged by verifier); optional LLM to *propose* explanations.
- **Inputs:** case evidence set, claims, ER links, verification results.
- **Outputs:** `Contradiction` records (type, involved evidence/claim ids, severity, status `OPEN`), optional `ExplanationProposal`.
- **Tools:** `get_evidence`, `get_claims`, `get_er_links`, `create_contradiction`, `llm.generate_structured` (optional).
- **Permissions:** `READ_EVIDENCE`, `WRITE_PROPOSAL`, `CALL_LLM`. Cannot set status to a resolved state.
- **Failure conditions:** detector rule error; LLM explanation unsupported by evidence (discarded).
- **Acceptance criteria:** fixture contradictions detected; no false resolution (status only changeable by human); explanation proposals cite evidence; each contradiction links to ≥2 items.

## AG-09 Reporting Agent
- **Purpose:** Draft the case report from verified material.
- **Implementation:** deterministic template assembly + LLM prose drafting constrained to supplied claims.
- **Inputs:** case, verified/partially supported claims, entities, timeline evidence, open contradictions, approvals, versions.
- **Outputs:** `Report` version (sections per FR-RPT-02); every sentence in findings has citations to claim ids (and through them evidence ids).
- **Tools:** `get_claims(status)`, `get_case`, `get_contradictions`, `llm.generate_structured`, `create_report_version`.
- **Permissions:** `READ_EVIDENCE`, `WRITE_PROPOSAL` (report draft), `CALL_LLM`, `REQUEST_APPROVAL` (to create PendingApproval for filing recommendation only).
- **Must not:** introduce facts not in supplied claims; include `UNSUPPORTED`/`UNVERIFIABLE` claims in findings; submit anything externally.
- **Failure conditions:** citation coverage <100% in findings (post-check fails → regenerate or fail); missing mandatory section.
- **Acceptance criteria:** citation coverage = 100% in findings; unsupported claims confined to a labelled section; open contradictions and limitations always present; report immutable once stored; deterministic assembly portions reproducible.

---

## Interaction matrix (who may request whom)
Capabilities never call each other. Only the Orchestrator (via runtime) creates tasks. Investigation/Contradiction/Reporting may *suggest* follow-up tasks as proposals; the Orchestrator decides.

## Version & test requirements for every agent
Each agent has: a version string; a Pydantic input/output model per task type; unit tests with fixtures; a negative-permission test; an `as_of` leakage test (where applicable); a replay test (LLM agents); and an entry in the capability registry.
