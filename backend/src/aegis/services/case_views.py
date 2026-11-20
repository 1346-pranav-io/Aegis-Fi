"""Read models for the API: a case with its claims, contradictions, runs and feedback counts.
Read-only; scores are scores, never probabilities."""

from typing import Any

import psycopg

from aegis.common.errors import NotFound


def case_detail(conn: psycopg.Connection, case_id: str) -> dict[str, Any]:
    case = conn.execute(
        "SELECT case_id, status, subject_ref, priority_score, assigned_to, opened_at, "
        "disposition FROM workflow.investigation_case WHERE case_id=%s",
        (case_id,),
    ).fetchone()
    if case is None:
        raise NotFound(f"case {case_id}")
    claims = conn.execute(
        "SELECT c.claim_id, c.text, c.claim_kind, c.self_reported_confidence, "
        "coalesce(v.status, 'DRAFT'), c.depends_on_er_links "
        "FROM evidence.claim c LEFT JOIN evidence.claim_current_verification v USING (claim_id) "
        "WHERE c.case_id=%s ORDER BY c.claim_id",
        (case_id,),
    ).fetchall()
    ctr = conn.execute(
        "SELECT contradiction_id, type, severity, status FROM evidence.contradiction_current "
        "WHERE case_id=%s ORDER BY contradiction_id",
        (case_id,),
    ).fetchall()
    runs = conn.execute(
        "SELECT run_id, status, policy_name FROM runtime.run_current WHERE case_id=%s "
        "ORDER BY at DESC LIMIT 20",
        (case_id,),
    ).fetchall()
    n_alerts = conn.execute(
        "SELECT count(*) FROM workflow.case_alert WHERE case_id=%s", (case_id,)
    ).fetchone()
    fb = conn.execute(
        "SELECT validation_status, count(*) FROM workflow.feedback_current WHERE case_id=%s "
        "GROUP BY 1",
        (case_id,),
    ).fetchall()
    return {
        "case_id": case[0], "status": case[1], "subject_ref": case[2],
        "priority_score": float(case[3]), "assigned_to": case[4],
        "opened_at": case[5].isoformat(), "disposition": case[6],
        "alerts": int(n_alerts[0]) if n_alerts else 0,
        "claims": [
            {"claim_id": r[0], "text": r[1], "claim_kind": r[2],
             "self_reported_confidence": r[3],  # ordinal, self-reported, NOT a probability
             "verification_status": r[4], "depends_on_er_links": list(r[5])}
            for r in claims
        ],
        "contradictions": [
            {"contradiction_id": r[0], "type": r[1], "severity": r[2], "status": r[3]} for r in ctr
        ],
        "runs": [{"run_id": r[0], "status": r[1], "policy": r[2]} for r in runs],
        "feedback_by_validation": {str(r[0]): int(r[1]) for r in fb},
    }  # fmt: skip


def pending_approvals(conn: psycopg.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT r.request_id, r.action_type, r.subject_ref, r.requested_by, r.requested_at, "
        "r.justification FROM workflow.approval_request r "
        "WHERE NOT EXISTS (SELECT 1 FROM workflow.approval a WHERE a.request_id = r.request_id) "
        "ORDER BY r.requested_at, r.request_id"
    ).fetchall()
    return [
        {"request_id": r[0], "action_type": r[1], "subject_ref": r[2], "requested_by": r[3],
         "requested_at": r[4].isoformat(), "justification": r[5]}
        for r in rows
    ]  # fmt: skip


def latest_report(conn: psycopg.Connection, case_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT report_id, case_id, run_id, version, body, body_hash, generator "
        "FROM runtime.report "
        "WHERE case_id=%s ORDER BY version DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"no report for case {case_id}")
    keys = ("report_id", "case_id", "run_id", "version", "body", "body_hash", "generator")
    return dict(zip(keys, row, strict=True))


def recent_audit(conn: psycopg.Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT event_id, ts, actor_id, action, object_type, object_id FROM audit.event "
        "ORDER BY event_id DESC LIMIT %s",
        (max(1, min(limit, 500)),),
    ).fetchall()
    return [
        {"event_id": r[0], "ts": r[1].isoformat(), "actor_id": r[2], "action": r[3],
         "object_type": r[4], "object_id": r[5]}
        for r in rows
    ]  # fmt: skip
