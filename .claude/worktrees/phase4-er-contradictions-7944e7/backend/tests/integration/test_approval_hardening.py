"""Regressions for Phase 2 security findings H-1, M-1, M-2, L-1 (migration 0007)."""

from datetime import UTC, datetime

import psycopg
import pytest

from aegis.common.errors import ConflictError
from aegis.domain.cases import ApprovalAction, CaseStatus, Disposition
from aegis.services import cases as svc

pytestmark = pytest.mark.integration
NOW = datetime(2022, 9, 10, tzinfo=UTC)


def new_case(c: psycopg.Connection, acct: str = "A", score: float = 0.5) -> str:
    r = svc.create_alerts(
        c,
        [svc.ScoredItem(f"t-{acct}", acct, score, ("X",))],
        signal_type="S",
        as_of=NOW,
        model_artefact_id="m",
    )
    c.commit()
    return r[0].case_id


def test_app_login_cannot_write_approval_decisions(app_url: str, approver_url: str) -> None:
    """H-1: the forged-approval attack from the review no longer works."""
    with psycopg.connect(app_url) as c:
        cid = new_case(c)
        req = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "analyst1", "j")
        c.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(
                "INSERT INTO workflow.approval (approval_id, request_id, decision, decided_by, "
                "decider_role, reason) VALUES ('forged', %s, 'APPROVED', 'boss', 'approver', 'x')",
                (req,),
            )
        c.rollback()
        # the approver login can decide, but cannot create requests
    with psycopg.connect(approver_url) as ap:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ap.execute(
                "INSERT INTO workflow.approval_request (request_id, action_type, subject_ref, "
                "requested_by, justification) VALUES ('r','CASE_CLOSURE','c','u','j')"
            )
        ap.rollback()
        svc.decide_approval(ap, req, True, "boss", "ok")
        ap.commit()


def test_requester_cannot_decide_own_request(app_url: str, approver_url: str) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        cid = new_case(c)
        req = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "alice", "j")
        c.commit()
        with pytest.raises(ConflictError, match="cannot decide their own"):
            svc.decide_approval(ap, req, True, "alice", "self-approval")
        ap.rollback()
        svc.decide_approval(ap, req, True, "bob", "ok")  # a different human id is accepted
        ap.commit()


def test_logins_and_times_are_set_by_the_database_not_the_caller(
    app_url: str, approver_url: str
) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        cid = new_case(c)
        c.execute(
            "INSERT INTO workflow.approval_request (request_id, action_type, subject_ref, "
            "requested_by, requested_login, requested_at, justification) "
            "VALUES ('rq1','CASE_CLOSURE',%s,'alice','someone-else','2000-01-01','j')",
            (cid,),
        )
        c.commit()
        ap.execute(
            "INSERT INTO workflow.approval (approval_id, request_id, decision, decided_by, "
            "decider_role, decided_login, decided_at, reason) "
            "VALUES ('ap1','rq1','APPROVED','bob','analyst','someone-else','2000-01-01','r')"
        )
        ap.commit()
        req = c.execute(
            "SELECT requested_login, requested_at > '2020-01-01' FROM workflow.approval_request "
            "WHERE request_id='rq1'"
        ).fetchone()
        dec = ap.execute(
            "SELECT decided_login, decider_role, decided_at > '2020-01-01' FROM workflow.approval "
            "WHERE approval_id='ap1'"
        ).fetchone()
    assert req == ("aegis_app", True)
    assert dec == ("aegis_approver", "approver", True)


def test_approval_requested_before_review_cannot_close_the_case(
    app_url: str, approver_url: str
) -> None:
    """M-2: an approval created before the case reached PENDING_REVIEW is stale."""
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        cid = new_case(c)
        early = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "alice", "early")
        c.commit()
        early_apv = svc.decide_approval(ap, early, True, "bob", "ok")
        ap.commit()
        svc.transition(c, cid, CaseStatus.INVESTIGATING, "start")
        svc.transition(c, cid, CaseStatus.PENDING_REVIEW, "review")
        c.commit()
        with pytest.raises(ConflictError, match="requested after it entered review"):
            svc.close_case(c, cid, Disposition.NOT_SUSPICIOUS, early_apv, "x")
        c.rollback()
        late = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "alice", "after review")
        c.commit()
        late_apv = svc.decide_approval(ap, late, True, "bob", "ok")
        ap.commit()
        svc.close_case(c, cid, Disposition.NOT_SUSPICIOUS, late_apv, "x")
        c.commit()
        # a rework loop makes older approvals stale again only for later re-closures
        # (the latest PENDING_REVIEW entry is what counts); closed cases stay frozen.
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute(
                "UPDATE workflow.investigation_case SET status='OPEN' WHERE case_id=%s", (cid,)
            )


def test_priority_and_assignment_changes_are_controlled_and_audited(
    app_url: str, scratch_db_url: str
) -> None:
    """M-1."""
    with psycopg.connect(app_url) as c:
        cid = new_case(c, "A", 0.5)
        for stmt in (
            "UPDATE workflow.investigation_case SET priority_score = -1 WHERE case_id=%s",
            "UPDATE workflow.investigation_case SET last_alert_at = now() + interval '9 days' "
            "WHERE case_id=%s",
            "UPDATE workflow.investigation_case SET assigned_to = 'me' WHERE case_id=%s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt, (cid,))
            c.rollback()
        # alerts raise the priority monotonically; a lower score never lowers it
        new_case(c, "A", 0.9)
        new_case(c, "A", 0.1)
        assert svc.queue(c)[0][1] == 0.9
        svc.assign_case(c, cid, "carol")
        c.commit()
        with pytest.raises(Exception, match="no open case"):
            svc.assign_case(c, "case_nope", "carol")
        c.rollback()
        # inserts cannot smuggle in a closed case or backdated times
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute(
                "INSERT INTO workflow.investigation_case (case_id, status, subject_ref, "
                "priority_score) VALUES ('x','INVESTIGATING','Z',1)"
            )
        c.rollback()
        c.execute(
            "INSERT INTO workflow.investigation_case (case_id, status, subject_ref, "
            "priority_score, opened_at, last_alert_at) "
            "VALUES ('y','OPEN','Z',1,'2000-01-01','2000-01-01')"
        )
        row = c.execute(
            "SELECT opened_at > '2020-01-01', last_alert_at > '2020-01-01' FROM "
            "workflow.investigation_case WHERE case_id='y'"
        ).fetchone()
        assert row == (True, True)
        c.rollback()
    with psycopg.connect(scratch_db_url) as admin:
        a = admin.execute(
            "SELECT actor_id, details->>'assigned_to' FROM audit.event WHERE action='case.assigned'"
        ).fetchall()
        # direct tampering is blocked even for the table owner
        with pytest.raises(psycopg.errors.RaiseException, match="may not decrease"):
            admin.execute(
                "UPDATE workflow.investigation_case SET priority_score = 0 WHERE case_id=%s", (cid,)
            )
    assert a == [("aegis_app", "carol")]


def test_transition_reason_is_capped(app_url: str, scratch_db_url: str) -> None:
    """L-1."""
    with psycopg.connect(app_url) as c:
        cid = new_case(c)
        svc.transition(c, cid, CaseStatus.INVESTIGATING, "x" * 5000)
        c.commit()
    with psycopg.connect(scratch_db_url) as admin:
        n = admin.execute(
            "SELECT length(reason) FROM workflow.case_event WHERE case_id=%s", (cid,)
        ).fetchone()
    assert n == (500,)


def test_rework_loop_makes_earlier_approvals_stale(app_url: str, approver_url: str) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        cid = new_case(c)
        to = CaseStatus
        svc.transition(c, cid, to.INVESTIGATING, "start")
        svc.transition(c, cid, to.PENDING_REVIEW, "first review")
        c.commit()
        r1 = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "alice", "first")
        c.commit()
        a1 = svc.decide_approval(ap, r1, True, "bob", "ok")
        ap.commit()
        svc.transition(c, cid, to.INVESTIGATING, "rework")  # reviewer sends it back
        svc.transition(c, cid, to.PENDING_REVIEW, "second review")
        c.commit()
        with pytest.raises(ConflictError, match="requested after it entered review"):
            svc.close_case(c, cid, Disposition.NOT_SUSPICIOUS, a1, "stale approval")
        c.rollback()
        r2 = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "alice", "second")
        c.commit()
        a2 = svc.decide_approval(ap, r2, True, "bob", "ok")
        ap.commit()
        svc.close_case(c, cid, Disposition.NOT_SUSPICIOUS, a2, "fresh approval")
        c.commit()


def test_guard_triggers_fire_even_when_privileges_are_widened(
    scratch_db_url: str, ingest_url: str, approver_url: str, app_url: str
) -> None:
    """Exercise the trigger branches themselves: widen grants as the owner, then try to cheat."""
    with psycopg.connect(app_url) as c:
        cid = new_case(c)
        req = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "alice", "j")
        c.commit()
    with psycopg.connect(scratch_db_url) as admin:
        admin.execute("GRANT USAGE ON SCHEMA workflow TO aegis_ingest")
        admin.execute("GRANT INSERT ON workflow.approval TO aegis_ingest")
        admin.execute("GRANT INSERT ON workflow.approval_request TO aegis_approver")
        admin.commit()
    # a login that is not an approver member is refused by the trigger even with INSERT rights
    with psycopg.connect(ingest_url) as ing:
        with pytest.raises(psycopg.errors.RaiseException, match="only the approver role"):
            ing.execute(
                "INSERT INTO workflow.approval (approval_id, request_id, decision, decided_by, "
                "decider_role, reason) VALUES ('z', %s, 'APPROVED', 'bob', 'approver', 'x')",
                (req,),
            )
    # an approver who made the request cannot decide it
    with psycopg.connect(approver_url) as ap:
        ap.execute(
            "INSERT INTO workflow.approval_request (request_id, action_type, subject_ref, "
            "requested_by, justification) VALUES ('self1','CASE_CLOSURE',%s,'alice','j')",
            (cid,),
        )
        ap.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="requesting login cannot decide"):
            ap.execute(
                "INSERT INTO workflow.approval (approval_id, request_id, decision, decided_by, "
                "decider_role, reason) VALUES ('z2','self1','APPROVED','bob','approver','x')"
            )


def test_bump_case_validates_and_audits(app_url: str, scratch_db_url: str) -> None:
    """NEW-1: non-finite scores and unattached alerts are refused; raises are audited."""
    with psycopg.connect(app_url) as c:
        first = svc.create_alerts(
            c,
            [svc.ScoredItem("t1", "A", 0.5, ("X",))],
            signal_type="S",
            as_of=NOW,
            model_artefact_id="m",
        )[0]
        c.commit()
        cid = first.case_id
        for bad in ("'NaN'", "'Infinity'", "'-Infinity'", "NULL"):
            with pytest.raises(psycopg.errors.RaiseException, match="finite score"):
                c.execute(
                    f"SELECT workflow.bump_case(%s, {bad}::float8, %s)", (cid, first.alert_id)
                )
            c.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="not attached"):
            c.execute("SELECT workflow.bump_case(%s, 9, 'alert_nope')", (cid,))
        c.rollback()
        # an alert attached to a DIFFERENT case cannot justify a bump
        other = svc.create_alerts(
            c, [svc.ScoredItem("t2", "B", 0.2, ("X",))], signal_type="S", as_of=NOW,
            model_artefact_id="m",
        )[0]  # fmt: skip
        c.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="not attached"):
            c.execute("SELECT workflow.bump_case(%s, 9, %s)", (cid, other.alert_id))
        c.rollback()
        # a normal second alert raises the priority and is audited; a lower one is not
        svc.create_alerts(
            c, [svc.ScoredItem("t3", "A", 0.9, ("X",))], signal_type="S", as_of=NOW,
            model_artefact_id="m",
        )  # fmt: skip
        svc.create_alerts(
            c, [svc.ScoredItem("t4", "A", 0.1, ("X",))], signal_type="S", as_of=NOW,
            model_artefact_id="m",
        )  # fmt: skip
        c.commit()
    with psycopg.connect(scratch_db_url) as admin:
        raised = admin.execute(
            "SELECT actor_id, (details->>'to')::float8 FROM audit.event "
            "WHERE action='case.priority_raised' AND object_id=%s",
            (cid,),
        ).fetchall()
    assert raised == [("aegis_app", 0.9)]


def test_case_insert_and_signal_constraints(app_url: str) -> None:
    """NEW-2."""
    ins = (
        "INSERT INTO workflow.investigation_case (case_id, status, subject_ref, priority_score, "
        "assigned_to) VALUES (%s,'OPEN','Z',%s,%s)"
    )
    with psycopg.connect(app_url) as c:
        with pytest.raises(psycopg.errors.RaiseException, match="assign_case"):
            c.execute(ins, ("k1", 1.0, "mallory"))
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="finite"):
            c.execute(ins, ("k2", float("nan"), None))
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="finite"):
            c.execute(ins, ("k3", float("inf"), None))
        c.rollback()
        c.execute(ins, ("k4", 1.0, None))  # a normal insert still works
        with pytest.raises(psycopg.errors.CheckViolation):  # signal scores must be finite too
            c.execute(
                "INSERT INTO derived.signal (signal_id, subject_type, subject_ref, account_ref, "
                "signal_type, score, rule_pack_version, as_of) "
                "VALUES ('s','TRANSACTION','t','a','X','NaN','r',now())"
            )


def test_audit_namespace_and_approval_audit_rows(
    app_url: str, approver_url: str, scratch_db_url: str
) -> None:
    """NEW-3."""
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # free-text writer is internal
            c.execute("SELECT audit.record('case.closed', 'investigation_case', 'x')")
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="start with app"):
            c.execute("SELECT audit.record_app('case.closed', 'investigation_case', 'x')")
        c.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # approver has no audit access
            ap.execute("SELECT audit.record_app('app.x', 'approval_request', 'x')")
        ap.rollback()
        cid = new_case(c)
        req = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, cid, "alice", "j")
        c.commit()
        svc.decide_approval(ap, req, True, "bob", "ok")
        ap.commit()
    with psycopg.connect(scratch_db_url) as admin:
        rows = admin.execute(
            "SELECT action, actor_id FROM audit.event WHERE action LIKE 'approval.%%' "
            "ORDER BY event_id"
        ).fetchall()
    assert rows == [("approval.requested", "aegis_app"), ("approval.approved", "aegis_approver")]
