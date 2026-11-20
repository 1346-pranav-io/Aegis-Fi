"""Contradiction records and their status workflow (FR-CON-02/03). Runs as the aegis_app login.

Recording is idempotent (a unique index on case, fingerprint and severity, migrations 0021/0023).
Only a HUMAN actor may move a contradiction past OPEN: this module refuses anything else before the
database is reached, and the database refuses it again: since migration 0023 the status events can
only be written by the `aegis_reviewer` login, so `set_status` must be given a connection opened
as that role (the app login has no INSERT right on them). Nothing is deleted or
overwritten: a status change is a new event row, and resolution never removes either side.

(Moved here from capabilities/contradiction during reconciliation: it is application logic,
not an agent capability.)
"""

import json
from dataclasses import dataclass

import psycopg

from aegis.analytics.contradictions import fingerprint
from aegis.common.errors import AegisValidationError, ConflictError, NotFound, PermissionDenied
from aegis.common.ids import new_id
from aegis.domain.evidence import ContradictionDraft, ContradictionStatus

HUMAN = "HUMAN"
MAX_NOTE = 2000


@dataclass(frozen=True)
class RecordedContradiction:
    contradiction_id: str
    created: bool  # False when an identical finding already existed


def record_contradiction(
    conn: psycopg.Connection, draft: ContradictionDraft
) -> RecordedContradiction:
    """Insert the finding (status OPEN is set by the database) unless it already exists."""
    fp = fingerprint(draft)
    cid = new_id("ctr")
    detector = {
        "name": draft.detector_name,
        "version": draft.detector_version,
        "fingerprint": fp,
    }
    row = conn.execute(
        "INSERT INTO evidence.contradiction (contradiction_id, case_id, type, severity, detector, "
        "explanation_proposal) VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (case_id, (detector->>'fingerprint'), severity) "
        "WHERE detector ? 'fingerprint' "
        "DO NOTHING RETURNING contradiction_id",
        (
            cid,
            draft.case_id,
            draft.type.value,
            draft.severity.value,
            json.dumps(detector),
            None if draft.explanation_proposal is None else json.dumps(draft.explanation_proposal),
        ),
    ).fetchone()
    if row is None:
        existing = conn.execute(
            "SELECT contradiction_id FROM evidence.contradiction "
            "WHERE case_id=%s AND detector->>'fingerprint'=%s AND severity=%s",
            (draft.case_id, fp, draft.severity.value),
        ).fetchone()
        if existing is None:  # pragma: no cover - the conflict above proves it exists
            raise NotFound("contradiction")
        return RecordedContradiction(str(existing[0]), False)
    for m in draft.members:
        conn.execute(
            "INSERT INTO evidence.contradiction_member (contradiction_id, member_type, member_id, "
            "role) VALUES (%s,%s,%s,%s)",
            (cid, m.member_type.value, m.member_id, m.role),
        )
    return RecordedContradiction(cid, True)


def current_status(conn: psycopg.Connection, contradiction_id: str) -> ContradictionStatus:
    row = conn.execute(
        "SELECT status FROM evidence.contradiction_current WHERE contradiction_id=%s",
        (contradiction_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"contradiction {contradiction_id}")
    return ContradictionStatus(str(row[0]))


def open_contradictions(
    conn: psycopg.Connection, case_id: str, *, severity: str | None = None
) -> list[tuple[str, str, str]]:
    """(contradiction_id, type, severity) of the case's OPEN contradictions (FR-CON-04)."""
    rows = conn.execute(
        "SELECT contradiction_id, type, severity FROM evidence.contradiction_current "
        "WHERE case_id=%s AND status='OPEN' AND (%s::text IS NULL OR severity=%s) "
        "ORDER BY contradiction_id",
        (case_id, severity, severity),
    ).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def set_status(
    conn: psycopg.Connection,
    contradiction_id: str,
    to: ContradictionStatus,
    *,
    actor: str,
    actor_kind: str,
    note: str,
) -> None:
    """Move a contradiction. Only `actor_kind == "HUMAN"` may do so, and a note is required.
    (`actor` is a caller-asserted human id until the API phase supplies verified identity.)"""
    if actor_kind != HUMAN:
        raise PermissionDenied("only a human may change a contradiction's status (FR-CON-03)")
    if not actor.strip():
        raise AegisValidationError("a human decision needs an actor id")
    if not note.strip() or len(note) > MAX_NOTE:
        raise AegisValidationError(f"a note of 1..{MAX_NOTE} characters is required")
    current_status(conn, contradiction_id)  # NotFound for an unknown id
    try:
        conn.execute(
            "INSERT INTO evidence.contradiction_status_event (contradiction_id, status, "
            "actor_kind, actor, note) VALUES (%s,%s,'HUMAN',%s,%s)",
            (contradiction_id, to.value, actor, note),
        )
    except psycopg.errors.RaiseException as exc:
        raise ConflictError(str(exc).splitlines()[0]) from exc
