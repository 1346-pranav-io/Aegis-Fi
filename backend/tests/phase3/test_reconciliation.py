"""Phase 3 <-> Phase 4 reconciliation: the Contradiction agent inside a real run, the scan tools,
shared evidence content, ER link evidence, and re-verification after a link changes."""

from datetime import timedelta
from decimal import Decimal

import pytest
from p3_builders import ev_draft
from p3_e2e import E2E
from p3_runtime import AS_OF, CASE, SUBJECT, FnCap, Kit, big_budget

from aegis.capabilities.transaction.agent import TxnSummaryParams
from aegis.domain.agents import AgentName, AgentResult, AgentTask, Permission, Ref
from aegis.domain.entities import ERLinkView, LinkMethod, LinkStatus
from aegis.domain.evidence import (
    Citation,
    ContradictionType,
    EvidenceDraft,
    EvidenceType,
    InputRefType,
    Producer,
    ProducerKind,
    Severity,
    SupportRole,
)
from aegis.domain.evidence_content import events_of, facts_of, numeric_fact, with_facts
from aegis.entities.evidence import er_link_evidence
from aegis.eval.phase3.fixture_llm import StubConfig
from aegis.evidence.drafts import query_input
from aegis.runtime.policies import FixedPipeline, Reverify
from aegis.runtime.runner import InvestigationRunner
from aegis.runtime.state import RunStatus
from aegis.runtime.tools import RuntimeToolGateway, ToolContext, ToolDenied
from aegis.services.reverify import claims_depending_on, link_dependencies, start_reverification


def scan_gateway(kit: Kit, **over: object) -> RuntimeToolGateway:
    cap = FnCap(AgentName.CONTRADICTION, lambda t, g: None, frozenset({Permission.READ_EVIDENCE}))  # type: ignore[arg-type,return-value]
    task = AgentTask(
        task_id="t1", run_id="run_1", case_id=str(over.get("case_id", CASE)),
        agent=AgentName.CONTRADICTION, as_of=AS_OF, task_type="scan", budget=big_budget(),
    )  # fmt: skip
    return RuntimeToolGateway(ToolContext(task, cap, kit.services, "step_1"))


# --------------------------------------------------------------------------- scan tools
def test_scan_tools_return_the_shapes_the_detectors_parse_and_only_for_their_own_scope() -> None:
    kit = Kit(FixedPipeline(), caps=[])
    kit.backend.evidence.create_evidence(ev_draft(CASE, (query_input({"q": 1}),), content={"x": 1}))
    gw = scan_gateway(kit)
    args = {"case_id": CASE, "as_of": AS_OF}
    items = gw.call("list_case_evidence", args)["items"]
    assert len(items) == 1
    EvidenceDraft.model_validate({k: v for k, v in items[0].items() if k != "evidence_id"})
    assert gw.call("list_case_claims", args)["items"] == []
    assert gw.call("list_case_er_links", args)["items"] == []
    with pytest.raises(ToolDenied, match="own case"):
        gw.call("list_case_evidence", {"case_id": "other_case", "as_of": AS_OF})
    with pytest.raises(ToolDenied, match="later than the task"):
        gw.call("list_case_claims", {"case_id": CASE, "as_of": AS_OF + timedelta(days=1)})


def test_only_the_contradiction_agent_may_use_the_scan_tools() -> None:
    kit = Kit(FixedPipeline(), caps=[])
    for agent in AgentName:
        if agent is AgentName.CONTRADICTION:
            continue
        cap = FnCap(agent, lambda t, g: None, frozenset())  # type: ignore[arg-type,return-value]
        task = AgentTask(
            task_id="t", run_id="r", case_id=CASE, agent=agent, as_of=AS_OF, task_type="x",
            budget=big_budget(),
        )  # fmt: skip
        gw = RuntimeToolGateway(ToolContext(task, cap, kit.services, "s"))
        with pytest.raises(ToolDenied):
            gw.call("list_case_evidence", {"case_id": CASE, "as_of": AS_OF})


# ---------------------------------------------------------------- a real run with the agent
def test_faithful_run_includes_the_contradiction_scan_and_finds_nothing() -> None:
    e = E2E(FixedPipeline())
    st = e.run()
    scan = [s for s in st.steps if s.agent is AgentName.CONTRADICTION]
    assert len(scan) == 1 and scan[0].task_type == "scan"
    assert st.status is RunStatus.COMPLETE
    assert e.backend.evidence.unresolved_contradictions(CASE) == []


def test_refuted_claims_give_one_high_finding_each_even_though_two_components_derive_it() -> None:
    """The Verification agent and the Contradiction agent both derive CLAIM_VS_EVIDENCE; they use
    one shared definition, so the stored record is the same and recording is idempotent."""
    e = E2E(FixedPipeline(), StubConfig(fault_rate=1.0, faults=("WRONG_NUMBER",)))
    e.run()
    refuted = {c.claim_id for c in e.claims() if c.status and c.status.value == "CONTRADICTED"}
    found = e.backend.evidence.unresolved_contradictions(CASE)
    assert refuted and len(found) == len(refuted)
    assert {f.type for f in found} == {ContradictionType.CLAIM_VS_EVIDENCE}
    assert {f.severity for f in found} == {Severity.HIGH}  # HIGH blocks closing (migration 0022)


# ------------------------------------------------------------- shared evidence content shape
def test_transaction_evidence_follows_the_shared_facts_and_events_declaration() -> None:
    e = E2E(FixedPipeline())
    e.run()
    agg = next(
        ev for ev in e.backend.evidence.list_evidence(CASE)
        if ev.draft.evidence_type is EvidenceType.TXN_AGGREGATE
    )  # fmt: skip
    facts = facts_of(agg.draft.content)
    assert {f.predicate for f in facts} >= {"txn_count", "total_out"}
    assert all(f.subject_ref == SUBJECT and f.time_scope for f in facts)
    assert all(f.is_numeric() for f in facts)  # decimal strings with a unit, never floats
    assert {ev.kind for ev in events_of(agg.draft.content)} == {"TRANSACTION"}


def test_a_second_source_that_disagrees_is_found_by_the_scan_in_a_reverify_run() -> None:
    e = E2E(FixedPipeline())
    e.run()
    agg = next(
        ev for ev in e.backend.evidence.list_evidence(CASE)
        if ev.draft.evidence_type is EvidenceType.TXN_AGGREGATE
    )  # fmt: skip
    total = next(f for f in facts_of(agg.draft.content) if f.predicate == "total_out")
    doc = EvidenceDraft(
        case_id=CASE,
        evidence_type=EvidenceType.DOC_EXTRACTION,
        content=with_facts(
            {"doc": "kyc-note"},
            [
                numeric_fact(
                    SUBJECT,
                    "total_out",
                    Decimal(total.value) + 1000,
                    total.unit or "",
                    total.time_scope,
                )
            ],
        ),
        summary_text="KYC note states a different outgoing total",
        producer=Producer(kind=ProducerKind.TOOL, name="doc_extractor", version="1"),
        as_of=AS_OF,
        inputs=(query_input({"doc": "kyc-note"}),),
    )
    e.backend.evidence.create_evidence(doc)
    rev = InvestigationRunner(e.services, e.registry, Reverify(), e.saver)
    st = rev.run(rev.start(case_id=CASE, as_of=AS_OF, budget=big_budget(),
                           reverify_claim_ids=[e.claims()[0].claim_id]))  # fmt: skip
    assert st.status is RunStatus.COMPLETE
    types = {f.type for f in e.backend.evidence.unresolved_contradictions(CASE)}
    assert ContradictionType.AMOUNT_MISMATCH in types


# --------------------------------------------------------------- ER evidence + re-verification
def link(status: LinkStatus) -> ERLinkView:
    return ERLinkView(
        link_id="erl_1", mention_id="men_1", entity_id="ent_1", method=LinkMethod.FUZZY_NAME,
        match_score=0.91, status=status, scorer_version="s1",
    )  # fmt: skip


def test_er_link_evidence_is_well_formed_and_never_calls_the_score_a_probability() -> None:
    d = er_link_evidence(CASE, link(LinkStatus.PROPOSED), as_of=AS_OF, merge_ids=("erm_9",))
    assert d.evidence_type is EvidenceType.ER_LINK_EVIDENCE
    assert d.content["link_id"] == "erl_1" and d.content["merge_ids"] == ["erm_9"]
    assert d.content["match_score"] == "0.910000" and "probab" not in str(d.content).lower()
    assert d.inputs and d.inputs[0].ref_type is InputRefType.REFERENCE
    assert (
        d.content_hash()
        == er_link_evidence(
            CASE, link(LinkStatus.PROPOSED), as_of=AS_OF, merge_ids=("erm_9",)
        ).content_hash()
    )


def test_link_change_triggers_reverification_of_exactly_the_dependent_claims() -> None:
    e = E2E(FixedPipeline())
    e.run()
    store = e.backend.evidence
    base = e.claims()[0]
    other = e.claims()[1]
    er_ev, _ = store.create_evidence(
        er_link_evidence(CASE, link(LinkStatus.CONFIRMED), as_of=AS_OF)
    )
    dependent = store.create_claim(
        base.draft.model_copy(
            update={
                "citations": (
                    *base.draft.citations,
                    Citation(evidence_id=er_ev.evidence_id, support_role=SupportRole.CONTEXT),
                ),  # fmt: skip
                "text": base.draft.text + " (via resolved entity)",
            }
        )
    )
    assert link_dependencies(store, dependent.claim_id) == {"erl_1"}
    assert claims_depending_on(store, CASE, {"erl_1"}) == [dependent.claim_id]
    assert claims_depending_on(store, CASE, {"erl_other"}) == []
    before = {c.claim_id: len(store.verifications(c.claim_id)) for c in e.claims()}

    rev = InvestigationRunner(e.services, e.registry, Reverify(), e.saver)
    started = start_reverification(rev, store, [CASE], {"erl_1"}, as_of=AS_OF, budget=big_budget())
    assert set(started) == {CASE}
    st = rev.run(started[CASE])
    assert st.status in (RunStatus.COMPLETE, RunStatus.PARTIAL)
    assert [s.agent for s in st.steps][:1] == [AgentName.VERIFICATION]
    assert not any(s.agent in (AgentName.TRANSACTION, AgentName.GRAPH) for s in st.steps)
    after = {c.claim_id: len(store.verifications(c.claim_id)) for c in e.claims()}
    assert after[dependent.claim_id] == before.get(dependent.claim_id, 0) + 1  # re-verified
    assert after[other.claim_id] == before[other.claim_id]  # unrelated claims untouched
    assert store.get_claim(dependent.claim_id).draft.citations == dependent.draft.citations
    assert len(e.backend.reports.versions(CASE)) >= 2  # a new report version, earlier one kept


def test_no_run_is_started_when_no_claim_depends_on_the_changed_link() -> None:
    e = E2E(FixedPipeline())
    e.run()
    rev = InvestigationRunner(e.services, e.registry, Reverify(), e.saver)
    assert (
        start_reverification(
            rev, e.backend.evidence, [CASE], {"erl_unknown"}, as_of=AS_OF, budget=big_budget()
        )
        == {}
    )


def test_reverify_plan_has_no_specialists() -> None:
    from aegis.runtime.state import InvestigationState
    from aegis.runtime.wiring import default_registry

    state = InvestigationState(
        run_id="r", case_id=CASE, as_of=AS_OF, subject_ref=SUBJECT, policy_name="reverify",
        policy_version="1", budget=big_budget(),
    )  # fmt: skip
    plan = Reverify().plan(state, default_registry().catalog())
    assert [t.agent for t in plan.tasks] == [
        AgentName.VERIFICATION,
        AgentName.CONTRADICTION,
        AgentName.REPORTING,
    ]
    assert TxnSummaryParams().lookback_hours > 0


# ------------------------------------------------------------------ review findings M-3, M-4
def test_a_contradiction_for_another_case_is_rejected_not_stored() -> None:
    from p3_runtime import all_caps

    from aegis.domain.agents import ResultStatus
    from aegis.domain.evidence import (
        ContradictionDraft,
        ContradictionMember,
        MemberType,
    )

    def rogue(task, tools):  # type: ignore[no-untyped-def]
        d = ContradictionDraft(
            case_id="case_OTHER",
            type=ContradictionType.AMOUNT_MISMATCH,
            severity=Severity.LOW,
            detector_name="rogue",
            detector_version="1",
            members=(
                ContradictionMember(member_type=MemberType.EVIDENCE, member_id="a"),
                ContradictionMember(member_type=MemberType.EVIDENCE, member_id="b"),
            ),
        )
        return AgentResult(
            task_id=task.task_id, status=ResultStatus.OK, version="t1", contradictions=(d,)
        )

    caps = [
        *all_caps(),
        FnCap(AgentName.CONTRADICTION, rogue, frozenset({Permission.READ_EVIDENCE})),
    ]
    kit = Kit(FixedPipeline(), caps)
    st = kit.runner.run(kit.start())
    scan = next(s for s in st.steps if s.agent is AgentName.CONTRADICTION)
    assert scan.status is ResultStatus.FAILED
    rec = kit.backend.runs.get_step(st.run_id, scan.task_id)
    assert rec and rec.error and rec.error["code"] == "OUTPUT_REJECTED"
    assert kit.backend.evidence.unresolved_contradictions("case_OTHER") == []


def test_claim_dependencies_on_er_links_are_derived_from_the_cited_evidence() -> None:
    """An agent that forgets (or declines) to declare `er_link_ids` still gets the dependency
    recorded, because the claim cites ER_LINK_EVIDENCE naming the link."""
    kit = Kit(FixedPipeline(), caps=[])
    er_ev, _ = kit.backend.evidence.create_evidence(
        er_link_evidence(CASE, link(LinkStatus.PROPOSED), as_of=AS_OF)
    )
    cap = FnCap(
        AgentName.INVESTIGATION, lambda t, g: None,  # type: ignore[arg-type,return-value]
        frozenset({Permission.READ_EVIDENCE, Permission.WRITE_CLAIM}),
    )  # fmt: skip
    task = AgentTask(
        task_id="t1", run_id="run_1", case_id=CASE, agent=AgentName.INVESTIGATION, as_of=AS_OF,
        task_type="synthesise", budget=big_budget(),
        input_refs=(Ref(kind="evidence", id=er_ev.evidence_id),),
    )  # fmt: skip
    gw = RuntimeToolGateway(ToolContext(task, cap, kit.services, "step_1"))
    gw.call("get_evidence", {"ids": [er_ev.evidence_id]})
    out = gw.call(
        "create_claim",
        {
            "structure": {
                "subject_ref": SUBJECT,
                "predicate": "same_entity_as",
                "object_value": "ent_1",
            },
            "text": "The mention resolves to ent_1",
            "claim_kind": "INFERENCE",
            "citations": [{"evidence_id": er_ev.evidence_id, "support_role": "SUPPORTS"}],
        },
    )
    assert out["depends_on_unconfirmed_links"] == ["erl_1"]
    stored = kit.backend.evidence.get_claim(out["claim_id"])
    assert stored.draft.depends_on_er_links == ("erl_1",)
