"""Phase 3 evidence store + Phase 4 contradiction workflow + case closure gate, on one database.

A finding recorded by the runtime's store and by the contradiction service is the SAME record
(idempotent across both paths), and it blocks closing until a human has dealt with it (T-INV-18).
"""

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from human_ops import as_reviewer

from aegis.analytics.contradictions import DetectionInput, StoredEvidence, detect_all
from aegis.common.errors import ConflictError
from aegis.domain.cases import ApprovalAction, CaseStatus, Disposition
from aegis.domain.evidence import (
    ContradictionStatus,
    ContradictionType,
    EvidenceDraft,
    EvidenceType,
    Producer,
    ProducerKind,
    Severity,
)
from aegis.domain.evidence_content import numeric_fact, with_facts
from aegis.evidence.drafts import query_input
from aegis.evidence.pg_store import PostgresEvidenceStore
from aegis.evidence.store import DictLeafHashes
from aegis.services import cases as svc
from aegis.services import contradictions as csvc

pytestmark = pytest.mark.integration


def human_set_status(c: psycopg.Connection, *a: object, **kw: object) -> None:
    as_reviewer(c, csvc.set_status, *a, **kw)


AS_OF = datetime(2022, 9, 10, tzinfo=UTC)
SUBJECT = "010|AAA"


def mk_case(c: psycopg.Connection) -> str:
    r = svc.create_alerts(
        c,
        [svc.ScoredItem("t-1", SUBJECT, 0.5, ("X",))],
        signal_type="S",
        as_of=AS_OF,
        model_artefact_id="m",
    )
    c.commit()
    return r[0].case_id


def fact_evidence(case_id: str, source: str, total: str) -> EvidenceDraft:
    return EvidenceDraft(
        case_id=case_id,
        evidence_type=EvidenceType.TXN_AGGREGATE,
        content=with_facts(
            {"source": source},
            [numeric_fact(SUBJECT, "total_out", Decimal(total), "USD", "2022-09-09/2022-09-10")],
        ),
        summary_text=f"{source} states the outgoing total",
        producer=Producer(kind=ProducerKind.TOOL, name=source, version="1"),
        as_of=AS_OF,
        inputs=(query_input({"source": source, "case": case_id}),),
    )


def approved_closure(app: psycopg.Connection, approver: psycopg.Connection, case_id: str) -> str:
    req = svc.request_approval(app, ApprovalAction.CASE_CLOSURE, case_id, "analyst1", "ok")
    app.commit()
    apv = svc.decide_approval(approver, req, True, "boss", "agreed")
    approver.commit()
    return apv


def test_finding_is_one_record_across_both_paths_and_blocks_closing_until_a_human_acts(
    app_url: str, approver_url: str
) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        case_id = mk_case(c)
        store = PostgresEvidenceStore(c, DictLeafHashes())
        e1, _ = store.create_evidence(fact_evidence(case_id, "transaction_agent", "100.00"))
        e2, _ = store.create_evidence(fact_evidence(case_id, "kyc_document", "1100.00"))
        c.commit()

        found = detect_all(
            DetectionInput(
                evidence=(
                    StoredEvidence(e1.evidence_id, e1.draft),
                    StoredEvidence(e2.evidence_id, e2.draft),
                )
            ),
            case_id=case_id,
        )
        mismatch = [d for d in found if d.type is ContradictionType.AMOUNT_MISMATCH]
        assert len(mismatch) == 1 and mismatch[0].severity is Severity.HIGH

        first = store.create_contradiction(mismatch[0])  # the runtime path
        again = store.create_contradiction(mismatch[0])  # re-running a scan changes nothing
        via_service = csvc.record_contradiction(c, mismatch[0])  # the Phase 4 service path
        c.commit()
        assert first.contradiction_id == again.contradiction_id == via_service.contradiction_id
        assert via_service.created is False
        n = c.execute(
            "SELECT count(*) FROM evidence.contradiction WHERE case_id=%s", (case_id,)
        ).fetchone()
        assert n == (1,)

        svc.transition(c, case_id, CaseStatus.INVESTIGATING, "start")
        svc.transition(c, case_id, CaseStatus.PENDING_REVIEW, "done")
        c.commit()
        apv = approved_closure(c, ap, case_id)
        with pytest.raises(ConflictError, match="HIGH"):
            svc.close_case(c, case_id, Disposition.INCONCLUSIVE, apv, "try to close")
        c.rollback()

        human_set_status(
            c,
            first.contradiction_id,
            ContradictionStatus.EXPLAINED,
            actor="analyst-7",
            actor_kind="HUMAN",
            note="KYC note predates the latest statements",
        )
        c.commit()
        svc.close_case(c, case_id, Disposition.INCONCLUSIVE, apv, "closed after review")
        c.commit()


def test_an_agent_cannot_resolve_a_finding(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        store = PostgresEvidenceStore(c, DictLeafHashes())
        e1, _ = store.create_evidence(fact_evidence(case_id, "transaction_agent", "5.00"))
        e2, _ = store.create_evidence(fact_evidence(case_id, "kyc_document", "50.00"))
        c.commit()
        d = detect_all(
            DetectionInput(
                evidence=(
                    StoredEvidence(e1.evidence_id, e1.draft),
                    StoredEvidence(e2.evidence_id, e2.draft),
                )
            ),
            case_id=case_id,
        )[0]
        cid = store.create_contradiction(d).contradiction_id
        c.commit()
        with pytest.raises(Exception, match="human"):
            human_set_status(
                c, cid, ContradictionStatus.DISMISSED, actor="llm", actor_kind="AGENT", note="x"
            )
