"""Policies and the LangGraph run loop: statuses, budgets (T-INV-15), partial results, denial
handling, marginal-gain stopping, and kill/resume (T-INV-16)."""

import pytest
from p3_runtime import (
    AS_OF,
    CASE,
    FnCap,
    Kit,
    all_caps,
    big_budget,
    ok,
    specialist,
    tool_evidence,
)

from aegis.common.errors import BudgetExceeded
from aegis.domain.agents import AgentName, AgentResult, Permission, ResultStatus
from aegis.runtime.policies import POLICIES, FixedPipeline, RuleAdaptive
from aegis.runtime.state import InvestigationState, Plan, RunStatus, Signal

ORDER = [AgentName.TRANSACTION, AgentName.GRAPH, AgentName.BEHAVIOUR, AgentName.INVESTIGATION,
         AgentName.VERIFICATION, AgentName.REPORTING]  # fmt: skip


def state_for(kit: Kit, signals: list[str], **kw: object) -> InvestigationState:
    return InvestigationState(
        run_id="run_x", case_id=CASE, as_of=AS_OF, subject_ref="s", policy_name="p",
        policy_version="1", budget=big_budget(**kw),  # type: ignore[arg-type]
        signals=[Signal(code=c) for c in signals],
    )  # fmt: skip


# ---------------------------------------------------------------------------- policies
@pytest.mark.parametrize("policy", [FixedPipeline(), RuleAdaptive()])
def test_policies_are_deterministic_and_emit_the_same_plan_schema(policy: object) -> None:
    kit = Kit(policy)  # type: ignore[arg-type]
    st = state_for(kit, ["CYCLE"])
    cat = kit.registry.catalog()
    a, b = policy.plan(st, cat), policy.plan(st, cat)  # type: ignore[attr-defined]
    assert isinstance(a, Plan) and a == b
    assert Plan.model_validate(a.model_dump()) == a
    assert a.policy_name == policy.name  # type: ignore[attr-defined]


def test_adaptive_routes_by_signal_and_fixed_does_not() -> None:
    kit = Kit(RuleAdaptive())
    cat = kit.registry.catalog()
    cyc = RuleAdaptive().plan(state_for(kit, ["CYCLE"]), cat)
    agents = [t.agent for t in cyc.tasks]
    assert agents[0] is AgentName.GRAPH  # cycle signal => Graph first (AG-01 criterion 6)
    assert AgentName.BEHAVIOUR not in agents and AgentName.TRANSACTION not in agents
    assert agents[-3:] == [AgentName.INVESTIGATION, AgentName.VERIFICATION, AgentName.REPORTING]
    drift = RuleAdaptive().plan(state_for(kit, ["BEHAVIOUR_DRIFT", "HIGH_VELOCITY"]), cat)
    assert {t.agent for t in drift.tasks[:2]} == {AgentName.BEHAVIOUR, AgentName.TRANSACTION}
    none = RuleAdaptive().plan(state_for(kit, ["UNKNOWN_CODE"]), cat)
    assert [t.rationale_code for t in none.tasks[:3]] == ["DEFAULT_NO_SIGNAL"] * 3
    fixed = FixedPipeline().plan(state_for(kit, ["CYCLE"]), cat)
    assert [t.agent for t in fixed.tasks] == ORDER


def test_adaptive_allocator_drops_specialists_a_small_budget_cannot_afford() -> None:
    kit = Kit(RuleAdaptive())
    plan = RuleAdaptive().plan(
        state_for(kit, ["CYCLE", "BEHAVIOUR_DRIFT", "HIGH_VELOCITY"], max_steps=5),
        kit.registry.catalog(),
    )  # tail needs 3 steps, leaving 2 specialist slots for 3 matching signals
    assert len(plan.tasks) == 5 and plan.tasks[-1].agent is AgentName.REPORTING  # tail reserved
    assert [t.agent for t in plan.skipped] == [AgentName.BEHAVIOUR]
    assert {t.rationale_code for t in plan.skipped} == {"DROPPED_BUDGET"}
    tiny = RuleAdaptive().plan(
        state_for(kit, ["CYCLE"], max_steps=2), kit.registry.catalog()
    )  # not even the tail fits: no specialists at all (the run will end BUDGET_EXHAUSTED)
    assert all(t.rationale_code == "MANDATORY_TAIL" for t in tiny.tasks)


def test_policy_registry_names() -> None:
    assert set(POLICIES) == {"fixed_pipeline", "rule_adaptive"}


# ------------------------------------------------------------------------------ runs
def test_fixed_pipeline_run_completes_end_to_end() -> None:
    kit = Kit(FixedPipeline())
    rid = kit.start()
    st = kit.runner.run(rid)
    assert st.status is RunStatus.COMPLETE and [s.agent for s in st.steps] == ORDER
    assert all(s.status is ResultStatus.OK for s in st.steps)
    runs = kit.backend.runs
    assert [e[0] for e in runs.status_events[rid]] == [  # type: ignore[attr-defined]
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.COMPLETE,
    ]
    assert len(st.evidence_ids) == 3 and len(st.claim_ids) == 1 and len(st.report_ids) == 1
    assert [s.seq for s in runs.steps(rid)] == list(range(6))
    ev = kit.backend.evidence.get_evidence(st.evidence_ids[0])
    assert ev.draft.run_id == rid and ev.draft.step_id  # stamped by the runtime
    assert kit.backend.evidence.get_claim(st.claim_ids[0]).status is not None  # verified-annotated


def test_terminal_run_is_not_rerun() -> None:
    kit = Kit(FixedPipeline())
    rid = kit.start()
    kit.runner.run(rid)
    n = sum(c.runs for c in kit.caps)
    st = kit.runner.run(rid)
    assert st.status is RunStatus.COMPLETE and sum(c.runs for c in kit.caps) == n


def test_failed_specialist_does_not_block_independent_steps() -> None:
    def boom(task, tools):  # type: ignore[no-untyped-def]
        raise ValueError("detector bug")

    caps = all_caps()
    caps[1] = FnCap(AgentName.GRAPH, boom, frozenset({Permission.READ_SOURCE}))
    kit = Kit(FixedPipeline(), caps)
    st = kit.runner.run(kit.start())
    assert st.status is RunStatus.PARTIAL
    graph = next(s for s in st.steps if s.agent is AgentName.GRAPH)
    assert graph.status is ResultStatus.FAILED
    assert len(st.steps) == 6 and st.report_ids  # everything else still ran
    rec = kit.backend.runs.get_step(st.run_id, graph.task_id)
    assert rec and rec.error and rec.error["code"] == "ValueError"


def test_every_step_failing_gives_failed_run() -> None:
    def boom(task, tools):  # type: ignore[no-untyped-def]
        raise ValueError("x")

    caps = [FnCap(a, boom, frozenset()) for a in (AgentName.TRANSACTION, AgentName.GRAPH)]
    kit = Kit(FixedPipeline(), caps)
    st = kit.runner.run(kit.start())
    assert st.status is RunStatus.FAILED and st.evidence_ids == []


def test_denied_tool_call_is_recorded_and_step_marked_denied() -> None:
    def sneaky(task, tools):  # type: ignore[no-untyped-def]
        tools.call("create_claim", {})  # a specialist has no business creating claims
        return ok(task)

    caps = all_caps()
    caps[0] = FnCap(AgentName.TRANSACTION, sneaky, frozenset(Permission))
    kit = Kit(FixedPipeline(), caps)
    st = kit.runner.run(kit.start())
    assert st.steps[0].status is ResultStatus.DENIED and st.status is RunStatus.PARTIAL
    assert any(e[0] == "app.tool_denied" for e in kit.audit.events)  # audit event written
    assert len(st.steps) == 6  # the run continued (AGENT_ARCHITECTURE 11)


def test_evidence_for_another_case_is_rejected_and_not_persisted() -> None:
    def wrong_case(task, tools):  # type: ignore[no-untyped-def]
        d = tool_evidence(task, "x", ["txn_1"]).model_copy(update={"case_id": "case_OTHER"})
        return ok(task, evidence=(d,))

    caps = all_caps()
    caps[0] = FnCap(AgentName.TRANSACTION, wrong_case, frozenset({Permission.READ_SOURCE}))
    kit = Kit(FixedPipeline(), caps)
    st = kit.runner.run(kit.start())
    assert st.steps[0].status is ResultStatus.FAILED
    assert kit.backend.evidence.list_evidence("case_OTHER") == []
    rec = kit.backend.runs.get_step(st.run_id, st.steps[0].task_id)
    assert rec and rec.error and rec.error["code"] == "OUTPUT_REJECTED"


def test_future_dated_evidence_is_rejected() -> None:
    from datetime import timedelta

    def future(task, tools):  # type: ignore[no-untyped-def]
        d = tool_evidence(task, "x", ["txn_1"]).model_copy(
            update={"as_of": task.as_of + timedelta(days=1)}
        )
        return ok(task, evidence=(d,))

    caps = all_caps()
    caps[0] = FnCap(AgentName.TRANSACTION, future, frozenset({Permission.READ_SOURCE}))
    kit = Kit(FixedPipeline(), caps)
    st = kit.runner.run(kit.start())
    assert st.steps[0].status is ResultStatus.FAILED


# ------------------------------------------------------------------------- budgets
@pytest.mark.parametrize("max_steps", [1, 2, 3, 4, 5])
def test_step_budget_halts_run_with_partial_results_kept(max_steps: int) -> None:
    """T-INV-15: never more than max_steps steps; BUDGET_EXHAUSTED; earlier results are kept."""
    kit = Kit(FixedPipeline())
    st = kit.runner.run(kit.start(big_budget(max_steps=max_steps)))
    assert st.status is RunStatus.BUDGET_EXHAUSTED
    assert len(st.steps) == max_steps == len(kit.backend.runs.steps(st.run_id))
    assert len(st.evidence_ids) == min(max_steps, 3)  # work done so far is not discarded
    assert st.skipped and all(t.rationale_code == "BUDGET_STOP" for t in st.skipped)
    assert kit.backend.runs.status(st.run_id) is RunStatus.BUDGET_EXHAUSTED


def test_a_step_that_runs_out_of_budget_stops_the_run() -> None:
    def out_of_budget(task, tools):  # type: ignore[no-untyped-def]
        raise BudgetExceeded("token budget would be exceeded")

    caps = all_caps()
    caps[3] = FnCap(AgentName.INVESTIGATION, out_of_budget, frozenset({Permission.CALL_LLM}))
    kit = Kit(FixedPipeline(), caps)
    st = kit.runner.run(kit.start())
    assert st.status is RunStatus.BUDGET_EXHAUSTED
    assert [s.status for s in st.steps][-1] is ResultStatus.BUDGET_EXHAUSTED
    assert len(st.evidence_ids) == 3 and not st.report_ids  # specialists' evidence survives


def test_token_budget_counts_llm_use_from_steps() -> None:
    kit = Kit(FixedPipeline())
    rid = kit.start(big_budget(max_tokens=5))
    st = kit.runner.run(rid)
    assert st.status is RunStatus.COMPLETE  # no LLM used by the fakes => no tokens spent
    assert st.tokens_used == 0


# --------------------------------------------------------------- marginal-gain stop
def test_adaptive_stops_adding_specialists_when_marginal_gain_is_zero() -> None:
    first = specialist(AgentName.TRANSACTION, "same")
    dup = specialist(AgentName.GRAPH, "same")  # produces byte-identical evidence
    behav = specialist(AgentName.BEHAVIOUR, "behav")
    caps = [first, dup, behav, *all_caps()[3:]]
    kit = Kit(RuleAdaptive(min_gain=1), caps)
    st = kit.runner.run(kit.start())
    assert [s.agent for s in st.steps][:2] == [AgentName.TRANSACTION, AgentName.GRAPH]
    assert behav.runs == 0  # dropped by the stopping rule
    assert st.steps[1].new_evidence == 0
    assert any(t.rationale_code == "STOP_MARGINAL_GAIN" for t in st.skipped)
    assert st.status is RunStatus.COMPLETE  # a deliberate policy stop is not a failure


def test_adaptive_without_matching_capability_still_runs_the_tail() -> None:
    kit = Kit(RuleAdaptive(), caps=[specialist(AgentName.GRAPH, "g"), *all_caps()[3:]])
    st = kit.runner.run(kit.start())
    assert AgentName.GRAPH in [s.agent for s in st.steps] and st.status is RunStatus.COMPLETE


# ---------------------------------------------------------------------- kill / resume
class Crash(Exception):
    """Simulates the process being killed (not an AegisError, so nothing handles it)."""


def normalise(kit: Kit, st: InvestigationState) -> dict[str, object]:
    ev = kit.backend.evidence
    return {
        "status": st.status,
        "steps": [(s.agent, s.status, s.new_evidence, len(s.claim_ids)) for s in st.steps],
        "evidence": sorted(ev.get_evidence(i).content_hash for i in st.evidence_ids),
        "n_claims": len(ev.list_claims(CASE)),
        "n_reports": len(kit.backend.reports.versions(CASE)),
        "skipped": [t.rationale_code for t in st.skipped],
    }


@pytest.mark.parametrize("point", ["after_capability", "before_commit", "after_dispatch"])
@pytest.mark.parametrize("crash_at", [0, 3, 5])
def test_killed_run_resumes_to_the_same_final_state(point: str, crash_at: int) -> None:
    """T-INV-16: kill at any persistence point of any step; resume equals an uninterrupted run,
    with nothing duplicated (no repeated claims/reports, steps reused not re-run)."""
    ref_kit = Kit(FixedPipeline())
    ref = normalise(ref_kit, ref_kit.runner.run(ref_kit.start()))

    kit = Kit(FixedPipeline())
    seen = {"n": 0}

    def fault(p: str) -> None:
        if p == point:
            if seen["n"] == crash_at:
                seen["n"] += 1
                raise Crash(p)
            seen["n"] += 1

    kit.services.fault = fault
    rid = kit.start()
    with pytest.raises(Crash):
        kit.runner.run(rid)
    assert kit.backend.runs.status(rid) is RunStatus.RUNNING  # died mid-run
    st = kit.runner.run(rid)  # resume from the checkpoint
    assert normalise(kit, st) == ref
    assert st.status is RunStatus.COMPLETE
    executed = sum(c.runs for c in kit.caps)
    # a step killed before its commit is rolled back and re-executed once; others never twice
    assert executed == 6 + (1 if point in ("after_capability", "before_commit") else 0)
    assert any(s.reused for s in st.steps) == (point == "after_dispatch")


def test_resume_without_a_checkpointer_still_does_not_repeat_committed_steps() -> None:
    kit = Kit(FixedPipeline(), checkpointer=None)
    state = {"crashed": False}

    def fault(p: str) -> None:
        if p == "after_dispatch" and len(kit.backend.runs.steps(rid)) == 3 and not state["crashed"]:
            state["crashed"] = True
            raise Crash(p)

    kit.services.fault = fault
    rid = kit.start()
    with pytest.raises(Crash):
        kit.runner.run(rid)
    st = kit.runner.run(rid)
    assert st.status is RunStatus.COMPLETE
    assert sum(c.runs for c in kit.caps) == 6  # first three steps were reused, not repeated


def test_unknown_run_is_a_typed_error() -> None:
    from aegis.common.errors import NotFound

    kit = Kit(FixedPipeline())
    with pytest.raises(NotFound):
        kit.runner.run("run_nope")
    assert AgentResult  # imported for typing parity with other tests
