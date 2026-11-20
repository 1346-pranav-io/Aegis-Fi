"""Alerts, cases, approvals (FR-DET-05, FR-CASE-01..04, D-06, D-09).

The workflow runs as aegis_app; approval decisions come from the separate aegis_approver login.
"""

from datetime import UTC, datetime
from itertools import product

import psycopg
import pytest

from aegis.common.errors import AegisValidationError, ConflictError
from aegis.domain.cases import LEGAL_TRANSITIONS, ApprovalAction, CaseStatus, Disposition
from aegis.services import cases as svc

pytestmark = pytest.mark.integration
NOW = datetime(2022, 9, 10, tzinfo=UTC)


def item(txn: str, acct: str, score: float) -> svc.ScoredItem:
    return svc.ScoredItem(txn, acct, score, ("MODEL_SCORE_HIGH",))


def make(
    conn: psycopg.Connection, items: list[svc.ScoredItem], window_s: int = 24 * 3600
) -> list[svc.AlertResult]:
    r = svc.create_alerts(
        conn,
        items,
        signal_type="B1_XGB",
        as_of=NOW,
        model_artefact_id="m1",
        grouping_window_s=window_s,
    )
    conn.commit()
    return r


def to_pending_review(conn: psycopg.Connection, case_id: str) -> None:
    svc.transition(conn, case_id, CaseStatus.INVESTIGATING, "start")
    svc.transition(conn, case_id, CaseStatus.PENDING_REVIEW, "done")
    conn.commit()


def approved_closure(app: psycopg.Connection, approver: psycopg.Connection, case_id: str) -> str:
    req = svc.request_approval(app, ApprovalAction.CASE_CLOSURE, case_id, "analyst1", "evidence ok")
    app.commit()
    apv = svc.decide_approval(approver, req, True, "boss", "agreed")
    approver.commit()
    return apv


def test_signal_probability_requires_calibrator_and_producer(app_url: str) -> None:
    ins = (
        "INSERT INTO derived.signal (signal_id, subject_type, subject_ref, account_ref, "
        "signal_type, score, calibrated_probability, calibrator_artefact_id, "
        "rule_pack_version, as_of) VALUES (%s,'TRANSACTION','t','a','X',0.9,%s,%s,%s,now())"
    )
    with psycopg.connect(app_url) as c:
        with pytest.raises(psycopg.errors.CheckViolation):  # T-INV-09
            c.execute(ins, ("s1", 0.8, None, "r1"))
        c.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):  # no producer
            c.execute(ins, ("s2", None, None, None))
        c.rollback()
        c.execute(ins, ("s3", 0.8, "cal1", "r1"))  # allowed: calibrator named
        c.execute(ins, ("s4", None, None, "r1"))  # allowed: score only


def test_alerts_need_producer_and_reason_codes(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        with pytest.raises(AegisValidationError):
            svc.create_alerts(c, [item("t", "a", 1.0)], signal_type="X", as_of=NOW)
        with pytest.raises(AegisValidationError):
            svc.create_alerts(
                c,
                [svc.ScoredItem("t", "a", 1.0, ())],
                signal_type="X",
                as_of=NOW,
                model_artefact_id="m",
            )


def test_grouping_and_queue_order_are_deterministic(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        res = make(
            c,
            [
                item("t1", "A", 0.5),
                item("t2", "A", 0.9),
                item("t3", "B", 0.7),
                item("t4", "C", 0.7),
            ],
        )
        assert len({r.signal_id for r in res}) == 4
        assert len({r.case_id for r in res}) == 3  # A's two alerts share one case
        q = svc.queue(c)
        # priority: A=0.9 first, then B and C tie at 0.7 -> oldest first, then case id
        assert [score for _, score, _ in q] == [0.9, 0.7, 0.7]
        assert q == svc.queue(c)  # repeatable
        n_alerts = c.execute(
            "SELECT count(*) FROM workflow.case_alert WHERE case_id=%s", (q[0][0],)
        ).fetchone()
        assert n_alerts is not None and n_alerts[0] == 2
        # a zero-length grouping window means an alert never joins an older case
        late = make(c, [item("t9", "A", 0.4)], window_s=0)
        assert late[0].opened_new_case


def test_every_state_transition_matches_the_python_table(app_url: str) -> None:
    states = list(CaseStatus)
    with psycopg.connect(app_url) as c:
        for i, (a, b) in enumerate(product(states, states)):
            if a is CaseStatus.CLOSED or a is b or b is CaseStatus.CLOSED:
                continue  # closing and frozen states are covered separately
            case_id = make(c, [item(f"tx{i}", f"acct{i}", 0.1)])[0].case_id
            path = {
                CaseStatus.OPEN: [],
                CaseStatus.INVESTIGATING: [CaseStatus.INVESTIGATING],
                CaseStatus.PENDING_REVIEW: [CaseStatus.INVESTIGATING, CaseStatus.PENDING_REVIEW],
            }[a]
            for step in path:
                svc.transition(c, case_id, step, "setup")
            c.commit()
            if (a, b) in LEGAL_TRANSITIONS:
                svc.transition(c, case_id, b, "legal")
                c.commit()
            else:
                with pytest.raises(psycopg.errors.RaiseException):
                    svc.transition(c, case_id, b, "illegal")
                c.rollback()


def test_closure_needs_matching_approved_decision_and_freezes_case(
    app_url: str, approver_url: str, scratch_db_url: str
) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        cid = make(c, [item("t1", "A", 0.5)])[0].case_id
        other = make(c, [item("t2", "B", 0.5)])[0].case_id
        to_pending_review(c, cid)
        to_pending_review(c, other)
        # 1. an approval id that does not exist
        with pytest.raises(ConflictError):  # the gate trigger fires before the FK is checked
            svc.close_case(c, cid, Disposition.NOT_SUSPICIOUS, "apv_missing", "x")
        c.rollback()
        # 2. a DENIED decision
        req = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "a1", "j")
        c.commit()
        denied = svc.decide_approval(ap, req, False, "boss", "no")
        ap.commit()
        with pytest.raises(ConflictError):
            svc.close_case(c, cid, Disposition.NOT_SUSPICIOUS, denied, "x")
        c.rollback()
        # 3. an APPROVED decision for a different case, and one for a different action type
        wrong_case = approved_closure(c, ap, other)
        with pytest.raises(ConflictError):
            svc.close_case(c, cid, Disposition.NOT_SUSPICIOUS, wrong_case, "x")
        c.rollback()
        req2 = svc.request_approval(c, ApprovalAction.MODEL_ACTIVATION, cid, "a1", "j")
        c.commit()
        wrong_action = svc.decide_approval(ap, req2, True, "boss", "ok")
        ap.commit()
        with pytest.raises(ConflictError):
            svc.close_case(c, cid, Disposition.NOT_SUSPICIOUS, wrong_action, "x")
        c.rollback()
        # 4. the correct approval closes it
        good = approved_closure(c, ap, cid)
        svc.close_case(c, cid, Disposition.CONFIRMED_SUSPICIOUS, good, "final")
        c.commit()
        # frozen afterwards
        for stmt in (
            "UPDATE workflow.investigation_case SET status='INVESTIGATING' WHERE case_id=%s",
            "UPDATE workflow.investigation_case SET disposition='INCONCLUSIVE' WHERE case_id=%s",
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                c.execute(stmt, (cid,))
            c.rollback()
        # a new alert for A after closure opens a new case, and the closed one leaves the queue
        assert make(c, [item("t3", "A", 0.5)])[0].opened_new_case
        assert cid not in [x[0] for x in svc.queue(c)]
    with psycopg.connect(scratch_db_url) as admin:
        events = admin.execute(
            "SELECT from_status, to_status, actor, reason FROM workflow.case_event "
            "WHERE case_id=%s ORDER BY event_id",
            (cid,),
        ).fetchall()
        audit = admin.execute(
            "SELECT action, actor_id FROM audit.event WHERE object_id=%s ORDER BY event_id", (cid,)
        ).fetchall()
    assert [(e[0], e[1]) for e in events] == [
        ("OPEN", "INVESTIGATING"),
        ("INVESTIGATING", "PENDING_REVIEW"),
        ("PENDING_REVIEW", "CLOSED"),
    ]
    assert events[-1][2] == "aegis_app" and events[-1][3] == "final"
    assert [a[0] for a in audit] == ["case.investigating", "case.pending_review", "case.closed"]
    assert {a[1] for a in audit} == {"aegis_app"}


def test_approval_rules(app_url: str, approver_url: str) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        cid = make(c, [item("t1", "A", 0.5)])[0].case_id
        req = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "a1", "j")
        c.commit()
        svc.decide_approval(ap, req, False, "boss", "I deny")
        ap.commit()
        with pytest.raises(ConflictError):  # one decision per request
            svc.decide_approval(ap, req, True, "boss2", "changed my mind")
        ap.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):  # unknown action type
            c.execute(
                "INSERT INTO workflow.approval_request (request_id, action_type, subject_ref, "
                "requested_by, justification) VALUES ('x','DELETE_EVERYTHING','s','u','j')"
            )


def test_records_are_insert_only_and_cases_undeletable(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        cid = make(c, [item("t1", "A", 0.5)])[0].case_id
        for stmt in (
            "UPDATE derived.signal SET score = 0",
            "DELETE FROM derived.signal",
            "UPDATE workflow.alert SET account_ref='z'",
            "DELETE FROM workflow.case_alert",
            "UPDATE workflow.case_event SET actor='z'",
        ):
            with pytest.raises(psycopg.Error):
                c.execute(stmt)
            c.rollback()
        with pytest.raises(psycopg.Error):
            c.execute("DELETE FROM workflow.investigation_case WHERE case_id=%s", (cid,))
