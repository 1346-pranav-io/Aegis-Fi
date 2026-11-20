"""Tool gateway: allow-lists, permission classes, schema-validated args, as_of enforcement,
logging (T-INV-11, T-INV-02) and the claim-context rule (T-INV-05 at the gateway)."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from p3_builders import ev_draft
from p3_runtime import AS_OF, CASE, SUBJECT, FnCap, Kit, big_budget
from pydantic import BaseModel

from aegis.common.errors import BudgetExceeded
from aegis.domain.agents import (
    AgentName,
    AgentTask,
    Permission,
    Ref,
)
from aegis.runtime.policies import FixedPipeline
from aegis.runtime.tools import (
    ALLOW_LISTS,
    TOOLS,
    RuntimeToolGateway,
    ToolArgumentError,
    ToolContext,
    ToolDenied,
)

ALL_PERMS = frozenset(Permission)


def gateway(
    kit: Kit,
    agent: AgentName,
    perms: frozenset[Permission] = ALL_PERMS,
    refs: tuple[Ref, ...] = (),
    **task_kw: object,
) -> RuntimeToolGateway:
    cap = FnCap(agent, lambda t, g: None, perms)  # type: ignore[arg-type,return-value]
    task = AgentTask(
        task_id="t1", run_id="run_1", case_id=CASE, agent=agent, as_of=AS_OF, task_type="x",
        input_refs=refs, budget=big_budget(**task_kw),
    )  # fmt: skip
    return RuntimeToolGateway(ToolContext(task, cap, kit.services, "step_1"))


@pytest.fixture
def kit() -> Kit:
    return Kit(FixedPipeline(), caps=[])


WINDOW = {"start": AS_OF - timedelta(days=3), "end": AS_OF}


@pytest.mark.parametrize(
    ("agent", "tool"),
    [(a, t) for a in AgentName for t in TOOLS if t not in ALLOW_LISTS[a]],
)
def test_every_agent_is_denied_every_tool_outside_its_allow_list(
    kit: Kit, agent: AgentName, tool: str
) -> None:
    """T-INV-11: exhaustive over agents x tools, even for an agent holding ALL permissions."""
    gw = gateway(kit, agent)
    with pytest.raises(ToolDenied, match="allow-list"):
        gw.call(tool, {})
    assert gw.calls[-1].status == "DENIED"
    assert kit.audit.events[-1][0] == "app.tool_denied"


def test_allow_listed_tool_still_needs_the_permission_class(kit: Kit) -> None:
    gw = gateway(kit, AgentName.INVESTIGATION, perms=frozenset({Permission.CALL_LLM}))
    with pytest.raises(ToolDenied, match="lacks permission READ_EVIDENCE"):
        gw.call("get_contradictions", {})


def test_unknown_tools_and_bad_arguments(kit: Kit) -> None:
    gw = gateway(kit, AgentName.TRANSACTION)
    with pytest.raises(ToolDenied, match="unknown tool"):
        gw.call("write_source", {})
    with pytest.raises(ToolArgumentError):
        gw.call("get_transactions", {"start": "not a date", "end": AS_OF})
    with pytest.raises(ToolArgumentError):  # extra arguments are refused, not ignored
        gw.call("get_transactions", {**WINDOW, "case_id": "other_case"})
    assert [c.status for c in gw.calls] == ["DENIED", "ERROR", "ERROR"]


def test_there_is_no_tool_that_writes_source_or_executes_actions() -> None:
    """T-INV-02 (part): the permission vocabulary has no source-write class and no tool maps
    to one; approvals are request-only."""
    assert not any("SOURCE" in p.value and p.value.startswith("WRITE") for p in Permission)
    assert {t.permission for t in TOOLS.values()} <= set(Permission)
    assert not any(
        re.search(r"(close|decide|approve|delete|update|freeze)", name) for name in TOOLS
    )


def test_no_runtime_code_writes_to_the_source_schema() -> None:
    """T-INV-02 (part): static scan of every package agents run through."""
    root = Path(__file__).parents[2] / "src" / "aegis"
    pat = re.compile(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE)\s+(TABLE\s+)?source\.", re.I)
    offenders = [
        str(f)
        for pkg in ("capabilities", "runtime", "llm", "evidence")
        for f in (root / pkg).rglob("*.py")
        if pat.search(f.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_as_of_is_enforced_by_the_gateway_not_the_agent(kit: Kit) -> None:
    from p3_runtime import txn

    from aegis.runtime.services import FixtureSource

    kit.services.source = FixtureSource(
        [txn(1, SUBJECT, "bank1|B", 5), txn(2, SUBJECT, "bank1|C", 4, late=True)]
    )
    gw = gateway(kit, AgentName.TRANSACTION)
    rows = gw.call("get_transactions", {**WINDOW})["transactions"]
    assert [r["txn_id"] for r in rows] == ["txn_1"]  # txn_2 was ingested after as_of: invisible
    later = {**WINDOW, "as_of": AS_OF + timedelta(days=2)}
    with pytest.raises(ToolDenied, match="later than"):
        gw.call("get_transactions", later)  # an agent cannot widen its horizon
    earlier = gw.call("get_transactions", {**WINDOW, "as_of": AS_OF - timedelta(hours=100)})
    assert earlier["transactions"] == [] and earlier["as_of"].startswith("2022-09-05")


def test_evidence_must_be_in_the_tasks_context(kit: Kit) -> None:
    be = kit.backend.evidence
    e1, _ = be.create_evidence(ev_draft(CASE, (kit.source.leaf_hashes() and _src(kit, 1),)))
    e2, _ = be.create_evidence(ev_draft(CASE, (_src(kit, 2),), content={"other": 1}, as_of=AS_OF))
    gw = gateway(kit, AgentName.INVESTIGATION, refs=(Ref(kind="evidence", id=e1.evidence_id),))
    assert gw.call("get_evidence", {"ids": [e1.evidence_id]})["evidence"]
    with pytest.raises(ToolDenied, match="not in this task's context"):
        gw.call("get_evidence", {"ids": [e2.evidence_id]})
    # a claim may only cite evidence from the author's context (AG-06 / T-INV-05 at the gateway)
    cite2 = {
        "structure": {"subject_ref": "a", "predicate": "p"}, "text": "t",
        "claim_kind": "AGGREGATE",
        "citations": [{"evidence_id": e2.evidence_id, "support_role": "SUPPORTS"}],
    }  # fmt: skip
    with pytest.raises(ToolArgumentError, match="outside its context"):
        gw.call("create_claim", cite2)
    cite_fake = {**cite2, "citations": [{"evidence_id": "ev_invented", "support_role": "SUPPORTS"}]}
    with pytest.raises(ToolArgumentError):
        gw.call("create_claim", cite_fake)
    ok = {**cite2, "citations": [{"evidence_id": e1.evidence_id, "support_role": "SUPPORTS"}]}
    out = gw.call("create_claim", ok)
    assert (
        out["claim_id"]
        and kit.backend.evidence.list_claims(CASE)[0].draft.author_name == "investigation"
    )
    assert len(kit.backend.evidence.list_claims(CASE)) == 1  # the rejected ones left no trace


def _src(kit: Kit, n: int):  # type: ignore[no-untyped-def]
    from aegis.domain.evidence import EvidenceInput, InputRefType

    return EvidenceInput(ref_type=InputRefType.SOURCE, ref_id=f"txn_{n}", ref_hash=f"hash_txn_{n}")


def test_verifier_reads_claims_without_verification_status(kit: Kit) -> None:
    """AG-07: get_claim never exposes other verification results or investigator context."""
    be = kit.backend.evidence
    e1, _ = be.create_evidence(ev_draft(CASE, (_src(kit, 1),)))
    from p3_builders import claim_draft

    c = be.create_claim(claim_draft(CASE, [e1.evidence_id]))
    gw = gateway(kit, AgentName.VERIFICATION, refs=(Ref(kind="claim", id=c.claim_id),))
    out = gw.call("get_claim", {"ids": [c.claim_id]})["claims"][0]
    assert "status" not in out and "reasoning" not in out
    assert set(out) == {"claim_id", "structure", "text", "claim_kind", "citations",
                        "self_reported_confidence", "depends_on_er_links", "author"}  # fmt: skip
    with pytest.raises(ToolDenied):  # the status-bearing reader is not on its allow-list
        gw.call("get_claims", {})


def test_llm_tool_is_limited_to_the_agents_own_prompts_and_known_schemas(kit: Kit) -> None:
    gw = gateway(kit, AgentName.VERIFICATION)
    with pytest.raises(ToolDenied, match="no LLM gateway"):
        gw.call("llm.generate_structured", dict(prompt_id="verification.x", role="r",
                variables={}, schema_name="s"))  # fmt: skip
    kit.services.llm = object()  # type: ignore[assignment]
    with pytest.raises(ToolDenied, match="not an VERIFICATION prompt"):
        gw.call("llm.generate_structured", dict(prompt_id="investigation.synth", role="r",
                variables={}, schema_name="s"))  # fmt: skip
    with pytest.raises(ToolArgumentError, match="unknown output schema"):
        gw.call("llm.generate_structured", dict(prompt_id="verification.entail", role="r",
                variables={}, schema_name="nope"))  # fmt: skip
    assert issubclass(BaseModel, object)


def test_approval_tool_creates_a_pending_request_only(kit: Kit) -> None:
    gw = gateway(kit, AgentName.REPORTING)
    out = gw.call(
        "request_approval",
        {
            "action": "REGULATORY_FILING_RECOMMENDATION",
            "justification": "verified claims support filing",
        },
    )
    assert out["status"] == "PENDING" and len(kit.approvals.requests) == 1
    assert kit.approvals.requests[0]["requested_by"] == "agent:reporting"
    with pytest.raises(ToolArgumentError):  # only the filing recommendation is requestable
        gw.call("request_approval", {"action": "CASE_CLOSURE", "justification": "x"})


def test_tool_call_budget_is_bounded(kit: Kit) -> None:
    gw = gateway(kit, AgentName.TRANSACTION, max_steps=1)
    for _ in range(50):
        gw.call("get_case", {})
    with pytest.raises(BudgetExceeded):
        gw.call("get_case", {})
    assert AS_OF.tzinfo is not None
