"""D-33: lookback windows end at the case's event-time anchor, not at the knowledge-time as_of.

Found with the IBM data: events from 2022 ingested in 2026, so windows counted back from the
database clock were empty. These tests use events three years before as_of."""

from datetime import timedelta

import pytest
from p3_runtime import AS_OF, CASE, SUBJECT, Kit, big_budget, txn

from aegis.capabilities.behaviour import BehaviourCapability
from aegis.capabilities.graph import GraphCapability
from aegis.capabilities.transaction import TransactionCapability
from aegis.domain.agents import AgentTask, Capability, window_anchor
from aegis.domain.evidence import EvidenceType
from aegis.runtime.policies import FixedPipeline
from aegis.runtime.services import CaseSummary
from aegis.runtime.tools import RuntimeToolGateway, ToolContext

LONG_AGO = 3 * 365 * 24  # hours before AS_OF


def old_rows():  # type: ignore[no-untyped-def]
    return [
        txn(1, SUBJECT, "bank1|B", LONG_AGO + 30),
        txn(2, "bank1|B", SUBJECT, LONG_AGO + 28, "250"),
        txn(3, SUBJECT, "bank1|C", LONG_AGO + 20),
    ]


def kit_with_anchor(anchored: bool) -> Kit:
    kit = Kit(FixedPipeline(), caps=[], txns=old_rows())
    anchor = AS_OF - timedelta(hours=LONG_AGO) if anchored else None
    kit.services.cases._c[CASE] = CaseSummary(  # type: ignore[attr-defined]
        case_id=CASE, subject_ref=SUBJECT, status="OPEN", event_anchor=anchor
    )
    return kit


def run(kit: Kit, cap: Capability):  # type: ignore[no-untyped-def]
    task = AgentTask(
        task_id="t1", run_id="run_1", case_id=CASE, agent=cap.name, as_of=AS_OF,
        task_type="x", params={}, budget=big_budget(),
    )  # fmt: skip
    return cap.run(task, RuntimeToolGateway(ToolContext(task, cap, kit.services, "step_1")))


def test_old_events_are_found_when_the_case_has_an_anchor() -> None:
    res = run(kit_with_anchor(True), TransactionCapability())
    agg = {e.evidence_type: e.content for e in res.evidence}[EvidenceType.TXN_AGGREGATE]
    assert agg["n_txns"] == 3


def test_without_an_anchor_behaviour_is_unchanged_and_old_events_are_invisible() -> None:
    res = run(kit_with_anchor(False), TransactionCapability())
    agg = {e.evidence_type: e.content for e in res.evidence}[EvidenceType.TXN_AGGREGATE]
    assert agg["n_txns"] == 0


@pytest.mark.parametrize("cap_cls", [GraphCapability, BehaviourCapability])
def test_graph_and_behaviour_use_the_anchor_too(cap_cls: type[Capability]) -> None:
    anchored = run(kit_with_anchor(True), cap_cls())
    plain = run(kit_with_anchor(False), cap_cls())
    assert [e.content_hash() for e in anchored.evidence] != [
        e.content_hash() for e in plain.evidence
    ]


def test_window_anchor_never_exceeds_as_of() -> None:
    future = (AS_OF + timedelta(days=5)).isoformat()
    assert window_anchor({"event_anchor": future}, AS_OF) == AS_OF
    assert window_anchor({"event_anchor": None}, AS_OF) == AS_OF
    earlier = AS_OF - timedelta(days=2)
    assert window_anchor({"event_anchor": earlier.isoformat()}, AS_OF) == earlier
