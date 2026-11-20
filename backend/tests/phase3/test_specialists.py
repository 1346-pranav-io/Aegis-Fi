"""Transaction, Graph and Behaviour capabilities against hand-computed fixtures (AG-02..04).
Includes the as_of leakage test, truncation flags, provenance of every number, and permissions."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from p3_runtime import AS_OF, CASE, SUBJECT, Kit, big_budget, txn

from aegis.analytics.graph import GraphLimits
from aegis.capabilities.behaviour import BehaviourCapability
from aegis.capabilities.graph import GraphCapability
from aegis.capabilities.transaction import TransactionCapability
from aegis.domain.agents import AgentResult, AgentTask, Capability, Permission, ResultStatus
from aegis.domain.evidence import Completeness, EvidenceType, InputRefType
from aegis.evidence.drafts import QUERY_PREFIX
from aegis.runtime.policies import FixedPipeline
from aegis.runtime.services import SourceTxn
from aegis.runtime.tools import RuntimeToolGateway, ToolContext


def run_cap(kit: Kit, cap: Capability, params: dict[str, Any] | None = None) -> AgentResult:
    task = AgentTask(
        task_id="t1", run_id="run_1", case_id=CASE, agent=cap.name, as_of=AS_OF,
        task_type="x", params=params or {}, budget=big_budget(),
    )  # fmt: skip
    return cap.run(task, RuntimeToolGateway(ToolContext(task, cap, kit.services, "step_1")))


def by_type(res: AgentResult) -> dict[EvidenceType, dict[str, Any]]:
    return {e.evidence_type: e.content for e in res.evidence}


@pytest.fixture
def kit() -> Kit:
    return Kit(FixedPipeline(), caps=[])


# ------------------------------------------------------------------------ transaction
def test_transaction_agent_matches_hand_computed_values(kit: Kit) -> None:
    res = run_cap(kit, TransactionCapability(), {"rapid_window_s": 36_000})
    assert res.status is ResultStatus.OK and len(res.evidence) == 5
    c = by_type(res)
    agg = c[EvidenceType.TXN_AGGREGATE]
    assert (agg["n_out"], agg["n_in"], agg["n_txns"]) == (2, 1, 3)
    assert agg["total_out"] == {"USD": "200"} and agg["total_in"] == {"USD": "250"}
    assert agg["distinct_counterparties"] == 2
    assert c[EvidenceType.TXN_VELOCITY]["max_txns_in_window"] == 1
    assert c[EvidenceType.TXN_STRUCTURING_CHECK]["n_just_below"] == 0
    rm = c[EvidenceType.TXN_RAPID_MOVEMENT]
    assert (rm["n_out"], rm["n_in"], rm["n_rapid_out"]) == (2, 1, 1)  # txn3 follows txn2 by 8h
    top = c[EvidenceType.TXN_COUNTERPARTY_SUMMARY]["top"]
    assert top[0] == {"counterparty": "bank1|B", "n_txns": 2, "total": {"USD": "350"}}


def test_transaction_agent_cites_exactly_the_source_rows_it_used(kit: Kit) -> None:
    res = run_cap(kit, TransactionCapability())
    for ev in res.evidence:
        ids = [i.ref_id for i in ev.inputs]
        assert ids == ["txn_1", "txn_2", "txn_3"]  # txn_4 does not touch the subject
        assert all(
            i.ref_type is InputRefType.SOURCE and i.ref_hash.startswith("hash_") for i in ev.inputs
        )


def test_structuring_and_velocity_detect_what_they_claim() -> None:
    rows = [txn(i, SUBJECT, f"bank1|X{i}", 3 + i * 0.05, "9500") for i in range(1, 6)]
    rows.append(txn(9, SUBJECT, "bank1|Y", 40, "20000"))
    kit = Kit(FixedPipeline(), caps=[], txns=rows)
    c = by_type(run_cap(kit, TransactionCapability()))
    assert c[EvidenceType.TXN_STRUCTURING_CHECK]["n_just_below"] == 5  # 9500 in [9000, 10000)
    assert c[EvidenceType.TXN_VELOCITY]["max_txns_in_window"] == 5
    assert c[EvidenceType.TXN_AGGREGATE]["total_out"] == {"USD": "67500"}


def test_empty_window_is_ok_with_an_empty_finding_not_a_failure() -> None:
    kit = Kit(FixedPipeline(), caps=[], txns=[])
    res = run_cap(kit, TransactionCapability())
    assert res.status is ResultStatus.OK
    assert by_type(res)[EvidenceType.TXN_AGGREGATE]["n_txns"] == 0
    inp = res.evidence[0].inputs[0]
    assert inp.ref_type is InputRefType.REFERENCE and inp.ref_id.startswith(QUERY_PREFIX)
    # and the store accepts it (self-certifying query reference)
    kit.services.backend.evidence.create_evidence(res.evidence[0])


def test_as_of_leakage_future_ingested_rows_do_not_change_any_output(kit: Kit) -> None:
    """T-INV-03 through the whole path: a row ingested after as_of must not change evidence."""
    base = default_rows()
    leaky = [*base, txn(50, SUBJECT, "bank1|Z", 1, "999999", late=True)]
    for cap_cls in (TransactionCapability, GraphCapability, BehaviourCapability):
        a = run_cap(Kit(FixedPipeline(), caps=[], txns=base), cap_cls())
        b = run_cap(Kit(FixedPipeline(), caps=[], txns=leaky), cap_cls())
        assert [e.content_hash() for e in a.evidence] == [e.content_hash() for e in b.evidence]


def default_rows() -> list[SourceTxn]:
    from p3_runtime import default_txns

    return default_txns()


def test_bad_params_fail_visibly(kit: Kit) -> None:
    res = run_cap(kit, TransactionCapability(), {"lookback_hours": 0})
    assert res.status is ResultStatus.FAILED and res.error and res.error.code == "BAD_PARAMS"
    res = run_cap(kit, TransactionCapability(), {"unexpected": 1})
    assert res.status is ResultStatus.FAILED


def test_specialists_hold_no_llm_or_write_permissions() -> None:
    for cap in (TransactionCapability(), GraphCapability(), BehaviourCapability()):
        assert cap.permissions == {Permission.READ_SOURCE, Permission.READ_DERIVED}


# ----------------------------------------------------------------------------- graph
def test_graph_agent_matches_hand_computed_values(kit: Kit) -> None:
    res = run_cap(kit, GraphCapability())
    assert res.status is ResultStatus.OK
    c = by_type(res)
    assert (
        c[EvidenceType.GRAPH_NEIGHBOURHOOD]["n_nodes"],
        c[EvidenceType.GRAPH_NEIGHBOURHOOD]["n_edges"],
    ) == (4, 4)
    assert c[EvidenceType.GRAPH_CYCLES]["n_cycles_through_center"] == 1  # S->B->S
    assert c[EvidenceType.GRAPH_CYCLES]["shortest_cycle_len"] == 2
    fan = c[EvidenceType.GRAPH_FANIN_FANOUT]
    assert (fan["center_out_txns"], fan["center_in_txns"], fan["center_out_distinct"]) == (2, 1, 2)
    assert Decimal(str(c[EvidenceType.GRAPH_FLOW_THROUGH]["flow_through_fraction"])) == 0
    assert "pagerank_of_center" in c[EvidenceType.GRAPH_CENTRALITY]
    assert {e.evidence_type for e in res.evidence} == {
        EvidenceType.GRAPH_NEIGHBOURHOOD, EvidenceType.GRAPH_CYCLES,
        EvidenceType.GRAPH_FANIN_FANOUT, EvidenceType.GRAPH_FLOW_THROUGH,
        EvidenceType.GRAPH_COMMUNITY, EvidenceType.GRAPH_CENTRALITY,
    }  # fmt: skip


def test_graph_agent_is_deterministic(kit: Kit) -> None:
    a = [e.content_hash() for e in run_cap(kit, GraphCapability()).evidence]
    b = [
        e.content_hash() for e in run_cap(Kit(FixedPipeline(), caps=[]), GraphCapability()).evidence
    ]
    assert a == b


def test_graph_truncation_is_flagged_never_silent() -> None:
    rows = [txn(i, SUBJECT, f"bank1|N{i}", 1 + i * 0.01) for i in range(30)]
    kit = Kit(FixedPipeline(), caps=[], txns=rows)
    res = run_cap(kit, GraphCapability(GraphLimits(max_edges_per_node=5)))
    assert res.status is ResultStatus.PARTIAL
    for ev in res.evidence:
        assert ev.completeness is Completeness.TRUNCATED and ev.content["truncated"] is True
        assert ev.content["truncation_reasons"] and ev.quality_notes
    assert by_type(res)[EvidenceType.GRAPH_NEIGHBOURHOOD]["n_edges"] == 5
    assert len(res.evidence[0].inputs) == 5  # provenance covers only edges actually used


def test_graph_agent_unknown_subject_has_no_edges(kit: Kit) -> None:
    kit2 = Kit(FixedPipeline(), caps=[], txns=[txn(1, "bank1|P", "bank1|Q", 5)])
    res = run_cap(kit2, GraphCapability())
    assert by_type(res)[EvidenceType.GRAPH_NEIGHBOURHOOD]["n_edges"] == 0
    assert res.evidence[0].inputs[0].ref_id.startswith(QUERY_PREFIX)
    assert kit is not None


# -------------------------------------------------------------------------- behaviour
def test_behaviour_insufficient_history_is_reported_not_guessed(kit: Kit) -> None:
    res = run_cap(kit, BehaviourCapability())
    self_ev = next(e for e in res.evidence if e.evidence_type is EvidenceType.BEHAV_SELF_DEVIATION)
    assert self_ev.content["status"] == "INSUFFICIENT_HISTORY"
    assert self_ev.completeness is Completeness.INSUFFICIENT
    assert "z_out_count" not in self_ev.content and "no deviation is claimed" in (
        self_ev.quality_notes or ""
    )


def test_behaviour_flags_a_synthetic_drift_fixture() -> None:
    """AG-04: a clear jump in activity and counterparties in the test window is quantified."""
    rows: list[SourceTxn] = []
    n = 0
    for h in range(60, 280, 8):  # steady: one txn per 8h with the same two counterparties
        n += 1
        rows.append(txn(n, SUBJECT, "bank1|REG1" if n % 2 else "bank1|REG2", h))
    for k in range(25):  # last 24h: burst to many new counterparties
        n += 1
        rows.append(txn(n, SUBJECT, f"bank1|NEW{k}", 1 + k * 0.5))
    kit = Kit(FixedPipeline(), caps=[], txns=rows)
    res = run_cap(kit, BehaviourCapability())
    ev = next(e for e in res.evidence if e.evidence_type is EvidenceType.BEHAV_SELF_DEVIATION)
    assert ev.content["status"] == "OK" and ev.completeness is Completeness.FULL
    assert Decimal(str(ev.content["z_out_count"])) > 3
    assert Decimal(str(ev.content["new_counterparty_share"])) == 1
    assert any(e.evidence_type is EvidenceType.BEHAV_PEER_DEVIATION for e in res.evidence)


def test_all_specialist_evidence_stores_and_walks_cleanly(kit: Kit) -> None:
    from aegis.evidence.provenance import walk

    be = kit.backend.evidence
    for cap in (TransactionCapability(), GraphCapability(), BehaviourCapability()):
        for d in run_cap(kit, cap).evidence:
            ev, _ = be.create_evidence(d)
            from p3_builders import claim_draft

            c = be.create_claim(claim_draft(CASE, [ev.evidence_id]))
            assert walk(be, kit.services.leaves, c.claim_id).ok
    assert AS_OF - timedelta(days=1) < AS_OF
