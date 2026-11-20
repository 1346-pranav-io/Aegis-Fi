"""Alert -> case workflow (FR-DET-05, FR-CASE-01..04). Runs as the aegis_app database role.

The database enforces the state machine and the closure approval gate; this module adds the
deterministic grouping rule and the queue ordering. Role names passed in (`decider_role`) are
asserted by the caller until the API/auth phase supplies verified identities.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg

from aegis.common.errors import AegisValidationError, ConflictError, NotFound
from aegis.common.ids import new_id
from aegis.domain.cases import (
    GROUPING_WINDOW_SECONDS,
    ApprovalAction,
    CaseStatus,
    Disposition,
)


@dataclass(frozen=True)
class ScoredItem:
    """One detector output that deserves an alert."""

    subject_ref: str  # transaction ref (D-25 unit)
    account_ref: str  # account the case will be about
    score: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class AlertResult:
    alert_id: str
    signal_id: str
    case_id: str
    opened_new_case: bool


def create_alerts(
    conn: psycopg.Connection,
    items: Sequence[ScoredItem],
    *,
    signal_type: str,
    as_of: datetime,
    rule_pack_version: str | None = None,
    model_artefact_id: str | None = None,
    window: tuple[datetime, datetime] | None = None,
    grouping_window_s: int = GROUPING_WINDOW_SECONDS,
) -> list[AlertResult]:
    """Record a signal + alert per item (requires a producer, FR-DET-05) and attach each alert to
    a case. Items are processed in descending score order, ties by subject_ref, so the outcome is
    deterministic. Scores are stored as scores; no probability is written here (D-06)."""
    if not (rule_pack_version or model_artefact_id):
        raise AegisValidationError("a signal needs a rule pack version or a model artefact id")
    results: list[AlertResult] = []
    for it in sorted(items, key=lambda i: (-i.score, i.subject_ref)):
        if not it.reason_codes:
            raise AegisValidationError("an alert needs at least one reason code")
        signal_id, alert_id = new_id("sig"), new_id("alert")
        conn.execute(
            "INSERT INTO derived.signal (signal_id, subject_type, subject_ref, account_ref, "
            "signal_type, score, reason_codes, rule_pack_version, model_artefact_id, as_of, "
            "window_start, window_end) VALUES (%s,'TRANSACTION',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                signal_id, it.subject_ref, it.account_ref, signal_type, it.score,
                list(it.reason_codes), rule_pack_version, model_artefact_id, as_of,
                window[0] if window else None, window[1] if window else None,
            ),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO workflow.alert (alert_id, signal_id, account_ref, reason_codes) "
            "VALUES (%s,%s,%s,%s)",
            (alert_id, signal_id, it.account_ref, list(it.reason_codes)),
        )
        case_id, is_new = _attach_to_case(
            conn, alert_id, it.account_ref, it.score, grouping_window_s
        )
        results.append(AlertResult(alert_id, signal_id, case_id, is_new))
    return results


def _attach_to_case(
    conn: psycopg.Connection, alert_id: str, account_ref: str, score: float, window_s: int
) -> tuple[str, bool]:
    row = conn.execute(
        "SELECT case_id FROM workflow.investigation_case "
        "WHERE subject_ref=%s AND status <> 'CLOSED' "
        "AND last_alert_at >= now() - make_interval(secs => %s) "
        "ORDER BY opened_at DESC, case_id DESC LIMIT 1 FOR UPDATE",
        (account_ref, window_s),
    ).fetchone()
    if row:
        case_id, is_new = str(row[0]), False
    else:
        case_id, is_new = new_id("case"), True
        conn.execute(
            "INSERT INTO workflow.investigation_case "
            "(case_id, status, subject_ref, priority_score) VALUES (%s,'OPEN',%s,%s)",
            (case_id, account_ref, score),
        )
    conn.execute(
        "INSERT INTO workflow.case_alert (case_id, alert_id) VALUES (%s,%s)", (case_id, alert_id)
    )
    if not is_new:  # the database requires the attached alert as justification for a bump
        conn.execute("SELECT workflow.bump_case(%s, %s, %s)", (case_id, score, alert_id))
    return case_id, is_new


def queue(conn: psycopg.Connection, limit: int = 100) -> list[tuple[str, float, str]]:
    """Open work ordered by priority score (desc), then age (oldest first), then case id.
    The ordering is documented and deterministic (FR-CASE-04); the score is not a probability."""
    rows = conn.execute(
        "SELECT case_id, priority_score, status FROM workflow.investigation_case "
        "WHERE status <> 'CLOSED' "
        "ORDER BY priority_score DESC, opened_at ASC, case_id ASC LIMIT %s",
        (limit,),
    ).fetchall()
    return [(str(r[0]), float(r[1]), str(r[2])) for r in rows]


def transition(conn: psycopg.Connection, case_id: str, to: CaseStatus, reason: str) -> None:
    """Move a case along the allowed path. Closing must go through close_case."""
    if to is CaseStatus.CLOSED:
        raise AegisValidationError("use close_case(): closing needs an approval")
    _set_reason(conn, reason)
    cur = conn.execute(
        "UPDATE workflow.investigation_case SET status=%s WHERE case_id=%s", (to.value, case_id)
    )
    if cur.rowcount == 0:
        raise NotFound(f"case {case_id}")


def request_approval(
    conn: psycopg.Connection,
    action: ApprovalAction,
    subject_ref: str,
    requested_by: str,
    justification: str,
) -> str:
    request_id = new_id("apr")
    conn.execute(
        "INSERT INTO workflow.approval_request (request_id, action_type, subject_ref, "
        "requested_by, justification) VALUES (%s,%s,%s,%s,%s)",
        (request_id, action.value, subject_ref, requested_by, justification),
    )
    return request_id


def decide_approval(
    conn: psycopg.Connection, request_id: str, approve: bool, decided_by: str, reason: str
) -> str:
    """Record a decision. `conn` must be the aegis_approver login: only it may write decisions,
    the database records that login, and `decided_by` (a human id, asserted until the API phase)
    must differ from the requester's."""
    approval_id = new_id("apv")
    try:
        conn.execute(
            "INSERT INTO workflow.approval (approval_id, request_id, decision, decided_by, "
            "decider_role, reason) VALUES (%s,%s,%s,%s,'approver',%s)",
            (approval_id, request_id, "APPROVED" if approve else "DENIED", decided_by, reason),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise ConflictError(f"request {request_id} already has a decision") from exc
    except psycopg.errors.RaiseException as exc:
        raise ConflictError(str(exc).splitlines()[0]) from exc
    return approval_id


def assign_case(conn: psycopg.Connection, case_id: str, user: str) -> None:
    """Assign an open case (audited by the database)."""
    try:
        conn.execute("SELECT workflow.assign_case(%s, %s)", (case_id, user))
    except psycopg.errors.RaiseException as exc:
        raise NotFound(str(exc).splitlines()[0]) from exc


def close_case(
    conn: psycopg.Connection,
    case_id: str,
    disposition: Disposition,
    approval_id: str,
    reason: str,
) -> None:
    """Close a PENDING_REVIEW case. The database refuses unless the approval is an APPROVED
    CASE_CLOSURE decision for this very case."""
    _set_reason(conn, reason)
    try:
        cur = conn.execute(
            "UPDATE workflow.investigation_case SET status='CLOSED', disposition=%s, "
            "closed_at=now(), closure_approval_id=%s WHERE case_id=%s",
            (disposition.value, approval_id, case_id),
        )
    except psycopg.errors.RaiseException as exc:
        raise ConflictError(str(exc).splitlines()[0]) from exc
    if cur.rowcount == 0:
        raise NotFound(f"case {case_id}")


def _set_reason(conn: psycopg.Connection, reason: str) -> None:
    conn.execute("SELECT set_config('aegis.transition_reason', %s, true)", (reason,))
