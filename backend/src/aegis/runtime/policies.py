"""Orchestration policies (AGENT_SPECIFICATIONS AG-01, AGENT_ARCHITECTURE 4).

Both policies are deterministic functions of (state, catalog) and return the SAME `Plan` schema,
so they can be compared directly (research contribution 1, experiment O1).

- `fixed_pipeline`: every case runs the same sequence. Baseline.
- `rule_adaptive`: routes specialists by the alert's typed signals, drops specialists the budget
  cannot afford (step-slot allocator that always reserves the mandatory tail; ties broken by
  relative cost), and stops adding specialists once marginal evidence gain falls below a threshold.
A `learned` policy is NOT implemented (only if it would beat these; Phase 6).
"""

from typing import Protocol

from aegis.domain.agents import AgentName
from aegis.runtime.registry import AgentCatalog
from aegis.runtime.state import InvestigationState, Plan, PlannedTask, StopDecision

SPECIALISTS = (AgentName.TRANSACTION, AgentName.GRAPH, AgentName.BEHAVIOUR)
TAIL = (
    AgentName.INVESTIGATION,
    AgentName.VERIFICATION,
    AgentName.CONTRADICTION,
    AgentName.REPORTING,
)
TASK_TYPE: dict[AgentName, str] = {
    AgentName.TRANSACTION: "txn_summary",
    AgentName.GRAPH: "ego_metrics",
    AgentName.BEHAVIOUR: "behaviour_profile",
    AgentName.INTELLIGENCE: "reference_lookup",
    AgentName.INVESTIGATION: "synthesise",
    AgentName.VERIFICATION: "verify_claims",
    AgentName.CONTRADICTION: "scan",
    AgentName.REPORTING: "draft_report",
}

# signal code -> specialist that explains it (rule_adaptive routing table)
SIGNAL_ROUTES: dict[str, AgentName] = {
    "FAN_OUT": AgentName.GRAPH,
    "FAN_IN": AgentName.GRAPH,
    "CYCLE": AgentName.GRAPH,
    "FLOW_THROUGH": AgentName.GRAPH,
    "BEHAVIOUR_DRIFT": AgentName.BEHAVIOUR,
    "NEW_COUNTERPARTIES": AgentName.BEHAVIOUR,
    "FRESH_ACCOUNT": AgentName.BEHAVIOUR,
    "HIGH_VELOCITY": AgentName.TRANSACTION,
    "STRUCTURING": AgentName.TRANSACTION,
    "LARGE_AMOUNT": AgentName.TRANSACTION,
    "RAPID_MOVEMENT": AgentName.TRANSACTION,
    "WATCHLIST_HIT": AgentName.INTELLIGENCE,
}


class OrchestrationPolicy(Protocol):
    name: str
    version: str

    def plan(self, state: InvestigationState, catalog: AgentCatalog) -> Plan: ...
    def replan(self, state: InvestigationState, new_evidence_ids: list[str]) -> Plan:
        """Called after each step. `tasks` = the tasks that should remain; `stop` ends the run."""
        ...


def _task(agent: AgentName, why: str) -> PlannedTask:
    return PlannedTask(agent=agent, task_type=TASK_TYPE[agent], rationale_code=why)


def _tail(catalog: AgentCatalog) -> list[PlannedTask]:
    return [_task(a, "MANDATORY_TAIL") for a in TAIL if catalog.has(a)]


class FixedPipeline:
    name = "fixed_pipeline"
    version = "1"

    def plan(self, state: InvestigationState, catalog: AgentCatalog) -> Plan:
        tasks = [_task(a, "FIXED_SEQUENCE") for a in SPECIALISTS if catalog.has(a)] + _tail(catalog)
        if not tasks:
            return Plan(
                policy_name=self.name,
                policy_version=self.version,
                tasks=[],
                stop=StopDecision(reason_code="NO_APPLICABLE_AGENT"),
            )
        return Plan(policy_name=self.name, policy_version=self.version, tasks=tasks)

    def replan(self, state: InvestigationState, new_evidence_ids: list[str]) -> Plan:
        # Fixed: keep what is left. Preconditions only skip steps that cannot run at all.
        keep, skipped = _drop_blocked_head(state)
        return Plan(
            policy_name=self.name,
            policy_version=self.version,
            tasks=keep,
            skipped=skipped,
            stop=None if keep else StopDecision(reason_code="PLAN_EXHAUSTED"),
        )


def _preconditions_hold(agent: AgentName, state: InvestigationState) -> bool:
    if agent is AgentName.INVESTIGATION:
        return bool(state.evidence_ids)
    if agent is AgentName.VERIFICATION:
        return bool(state.claim_ids)
    if agent is AgentName.CONTRADICTION:
        return bool(state.evidence_ids)
    return True


def _drop_blocked_head(state: InvestigationState) -> tuple[list[PlannedTask], list[PlannedTask]]:
    """Preconditions are checked just in time: only the task about to run is tested (later tasks
    depend on what earlier ones will produce). A blocked head is skipped and recorded."""
    keep = list(state.pending)
    skipped: list[PlannedTask] = []
    while keep and not _preconditions_hold(keep[0].agent, state):
        skipped.append(keep.pop(0).model_copy(update={"rationale_code": "PRECONDITION_UNMET"}))
    return keep, skipped


class RuleAdaptive:
    name = "rule_adaptive"
    version = "1"

    def __init__(self, min_gain: int = 1, specialist_cost_share: float = 0.5) -> None:
        self.min_gain = (
            min_gain  # stop adding specialists when a step adds fewer new evidence items
        )
        self.share = specialist_cost_share  # of the cost-unit budget, spendable on specialists

    def plan(self, state: InvestigationState, catalog: AgentCatalog) -> Plan:
        wanted: dict[AgentName, int] = {}
        for sig in state.signals:
            a = SIGNAL_ROUTES.get(sig.code)
            if a is not None and a in (*SPECIALISTS, AgentName.INTELLIGENCE) and catalog.has(a):
                wanted[a] = wanted.get(a, 0) + 1
        why = "SIGNAL_ROUTED"
        if not any(a in SPECIALISTS for a in wanted):  # no usable signal: fall back to all three
            for a in SPECIALISTS:
                if catalog.has(a):
                    wanted.setdefault(a, 0)
            why = "DEFAULT_NO_SIGNAL"
        # cost-aware ordering: more matching signals first, then cheaper, then canonical order
        order = sorted(
            wanted, key=lambda a: (-wanted[a], catalog.cost(a), list(AgentName).index(a))
        )
        tail = _tail(catalog)
        # allocator: the mandatory tail is reserved first; specialists get the remaining step slots
        slots = max(0, state.budget.max_steps - len(tail))
        if order and slots < 1:  # decision P-4 (D-28): refuse visibly instead of gathering nothing
            return Plan(
                policy_name=self.name, policy_version=self.version, tasks=[],
                stop=StopDecision(reason_code="BUDGET_TOO_SMALL"),
            )  # fmt: skip
        chosen = [_task(a, why) for a in order[:slots]]
        dropped = [_task(a, "DROPPED_BUDGET") for a in order[slots:]]  # considered, not scheduled
        tasks = chosen + tail
        if not tasks:
            return Plan(
                policy_name=self.name, policy_version=self.version, tasks=[],
                stop=StopDecision(reason_code="NO_APPLICABLE_AGENT"),
            )  # fmt: skip
        return Plan(
            policy_name=self.name, policy_version=self.version, tasks=tasks, skipped=dropped
        )

    def replan(self, state: InvestigationState, new_evidence_ids: list[str]) -> Plan:
        keep, skipped = _drop_blocked_head(state)
        last = state.steps[-1] if state.steps else None
        if (
            last is not None
            and last.agent in (*SPECIALISTS, AgentName.INTELLIGENCE)
            and last.new_evidence < self.min_gain
        ):
            # marginal evidence gain below threshold: stop adding specialists, go to the tail
            drop = [t for t in keep if t.agent in (*SPECIALISTS, AgentName.INTELLIGENCE)]
            keep = [t for t in keep if t not in drop]
            skipped += [t.model_copy(update={"rationale_code": "STOP_MARGINAL_GAIN"}) for t in drop]
        return Plan(
            policy_name=self.name,
            policy_version=self.version,
            tasks=keep,
            skipped=skipped,
            stop=None if keep else StopDecision(reason_code="PLAN_EXHAUSTED"),
        )


class Reverify:
    """Targeted plan used after an entity-resolution link changed: re-verify the affected claims
    (their ids are preloaded into the run state), rescan for contradictions, and report. No
    specialists run; the evidence is unchanged (reconciliation item C6)."""

    name = "reverify"
    version = "1"

    def plan(self, state: InvestigationState, catalog: AgentCatalog) -> Plan:
        wanted = (AgentName.VERIFICATION, AgentName.CONTRADICTION, AgentName.REPORTING)
        tasks = [_task(a, "REVERIFY_AFTER_LINK_CHANGE") for a in wanted if catalog.has(a)]
        if not tasks:
            return Plan(
                policy_name=self.name, policy_version=self.version, tasks=[],
                stop=StopDecision(reason_code="NO_APPLICABLE_AGENT"),
            )  # fmt: skip
        return Plan(policy_name=self.name, policy_version=self.version, tasks=tasks)

    def replan(self, state: InvestigationState, new_evidence_ids: list[str]) -> Plan:
        keep, skipped = _drop_blocked_head(state)
        return Plan(
            policy_name=self.name,
            policy_version=self.version,
            tasks=keep,
            skipped=skipped,
            stop=None if keep else StopDecision(reason_code="PLAN_EXHAUSTED"),
        )


POLICIES: dict[str, type] = {
    "fixed_pipeline": FixedPipeline,
    "rule_adaptive": RuleAdaptive,
    "reverify": Reverify,
}
