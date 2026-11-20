"""The investigation graph (LangGraph). It carries state, branching and checkpoints ONLY; every
decision lives in policies/engine so the graph can be swapped or tested without it.

Nodes (AGENT_ARCHITECTURE 3, simplified - see docs/PHASE3_NOTES.md): `plan` -> `dispatch` ->
`collect` -> (`dispatch` | `finalise`). Specialist, synthesis, verification, contradiction and
report work are all tasks run by `dispatch`; they are not separate graph nodes.

State between nodes is a JSON dict of `InvestigationState` (IDs and counters only). With a
checkpointer, state is saved after every node, so a killed run resumes at the node that did not
finish; step idempotency (engine.py) makes re-running that node safe.
"""

from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from aegis.common.logging import get_logger
from aegis.domain.agents import AgentName, ResultStatus
from aegis.runtime.engine import StepEngine
from aegis.runtime.policies import OrchestrationPolicy
from aegis.runtime.registry import CapabilityRegistry
from aegis.runtime.services import RuntimeServices
from aegis.runtime.state import InvestigationState, Plan, RunStatus

log = get_logger("aegis.graph")

DELIBERATE_SKIPS = {"STOP_MARGINAL_GAIN", "DROPPED_BUDGET"}  # policy choices, not failures


class GraphState(TypedDict):
    s: dict[str, Any]


def _load(gs: GraphState) -> InvestigationState:
    return InvestigationState.model_validate(gs["s"])


def _dump(st: InvestigationState) -> GraphState:
    return {"s": st.model_dump(mode="json")}


def budget_reason(st: InvestigationState) -> str | None:
    b = st.budget
    if len(st.steps) >= b.max_steps:
        return "max_steps"
    if b.max_tokens > 0 and st.tokens_used >= b.max_tokens:
        return "max_tokens"
    if st.cost_usd >= b.max_cost_usd > 0:
        return "max_cost_usd"
    if st.seconds_used >= b.max_seconds:
        return "max_seconds"
    return None


def final_status(st: InvestigationState) -> tuple[RunStatus, str]:
    if st.stop_reason == "BUDGET_EXHAUSTED":
        return RunStatus.BUDGET_EXHAUSTED, st.final_reason or "budget exhausted"
    if not any(s.status in (ResultStatus.OK, ResultStatus.PARTIAL) for s in st.steps):
        return RunStatus.FAILED, "no step produced a usable result"
    problems = [s for s in st.steps if s.status is not ResultStatus.OK]
    incomplete = [t for t in st.skipped if t.rationale_code not in DELIBERATE_SKIPS]
    if problems or incomplete:
        return RunStatus.PARTIAL, (
            f"{len(problems)} step(s) not OK, {len(incomplete)} task(s) skipped by preconditions"
        )
    if not st.report_ids and any(s.agent is AgentName.REPORTING for s in st.steps):
        return RunStatus.PARTIAL, "reporting step produced no report"
    return RunStatus.COMPLETE, st.stop_reason or "plan exhausted"


def build_graph(
    *,
    services: RuntimeServices,
    registry: CapabilityRegistry,
    policy: OrchestrationPolicy,
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> Any:
    engine = StepEngine(services, registry)
    catalog = registry.catalog()
    runs = services.backend.runs

    def apply_plan(st: InvestigationState, plan: Plan) -> None:
        st.pending = list(plan.tasks)
        st.skipped = st.skipped + [t for t in plan.skipped if t not in st.skipped]
        if plan.stop is not None and not plan.tasks:
            st.stop_reason = st.stop_reason or plan.stop.reason_code

    def plan_node(gs: GraphState) -> GraphState:
        st = _load(gs)
        if not st.planned:
            plan = policy.plan(st, catalog)
            apply_plan(st, plan)
            st.planned = True
            with services.backend.transaction():
                if runs.status(st.run_id) is RunStatus.QUEUED:
                    runs.set_status(st.run_id, RunStatus.RUNNING, "started")
            st.status = RunStatus.RUNNING
        return _dump(st)

    def dispatch_node(gs: GraphState) -> GraphState:
        st = _load(gs)
        if not st.pending:
            return _dump(st)
        why = budget_reason(st)
        if why:
            st.stop_reason = "BUDGET_EXHAUSTED"
            st.final_reason = f"budget exhausted ({why})"
            st.skipped += [
                t.model_copy(update={"rationale_code": "BUDGET_STOP"}) for t in st.pending
            ]
            st.pending = []
            return _dump(st)
        planned = st.pending[0]
        summary = engine.execute(st, planned, len(st.steps))
        st.steps.append(summary)
        st.pending = st.pending[1:]
        for i in summary.evidence_ids:
            if i not in st.evidence_ids:
                st.evidence_ids.append(i)
        st.claim_ids += [i for i in summary.claim_ids if i not in st.claim_ids]
        st.contradiction_ids += [
            i for i in summary.contradiction_ids if i not in st.contradiction_ids
        ]
        st.report_ids += summary.report_ids
        st.tokens_used += summary.tokens
        st.cost_usd += summary.cost_usd
        rec = runs.get_step(st.run_id, summary.task_id)
        st.seconds_used += (rec.duration_ms / 1000.0) if rec else 0.0
        services.checkpoint("after_dispatch")
        return _dump(st)

    def collect_node(gs: GraphState) -> GraphState:
        st = _load(gs)
        if not st.steps:
            return _dump(st)
        last = st.steps[-1]
        if last.status is ResultStatus.BUDGET_EXHAUSTED:
            st.stop_reason = "BUDGET_EXHAUSTED"
            st.final_reason = "a step ran out of budget"
            st.skipped += [
                t.model_copy(update={"rationale_code": "BUDGET_STOP"}) for t in st.pending
            ]
            st.pending = []
        elif st.pending:
            plan = policy.replan(st, list(last.evidence_ids))
            apply_plan(st, plan)
        return _dump(st)

    def route(gs: GraphState) -> str:
        return "dispatch" if _load(gs).pending else "finalise"

    def finalise_node(gs: GraphState) -> GraphState:
        st = _load(gs)
        status, reason = final_status(st)
        with services.backend.transaction():
            if runs.status(st.run_id) is RunStatus.RUNNING:
                runs.set_status(st.run_id, status, reason)
        st.status, st.final_reason = status, reason
        log.info("run.finished", run_id=st.run_id, status=status.value, steps=len(st.steps))
        return _dump(st)

    g = StateGraph(GraphState)
    g.add_node("plan", plan_node)  # type: ignore[call-overload]
    g.add_node("dispatch", dispatch_node)  # type: ignore[call-overload]
    g.add_node("collect", collect_node)  # type: ignore[call-overload]
    g.add_node("finalise", finalise_node)  # type: ignore[call-overload]
    g.add_edge(START, "plan")
    g.add_edge("plan", "dispatch")
    g.add_edge("dispatch", "collect")
    g.add_conditional_edges("collect", route, {"dispatch": "dispatch", "finalise": "finalise"})
    g.add_edge("finalise", END)
    return g.compile(checkpointer=checkpointer)
