"""Feedback capture, deterministic validation, second review and export (FR-HITL-01/03/04).

Feedback is a separate record. It never changes a claim, an evidence item or a source record
(FR-HITL-04): an EDIT is a proposal stored in the payload. Validation runs fixed rules
(`RULES_VERSION`) and is recorded as history; QUARANTINED feedback cannot be exported. A human
second review (reviewer login) can re-validate it. Exporting validated feedback needs an APPROVED
FEEDBACK_TO_TRAINING decision (D-09f), checked by the database.
"""

import json
from collections.abc import Sequence
from typing import Any

import psycopg

from aegis.common.errors import AegisValidationError, ConflictError, NotFound
from aegis.common.hashing import content_hash
from aegis.common.ids import new_id
from aegis.domain.feedback import (
    RULES_VERSION,
    VERDICTS,
    FeedbackAction,
    FeedbackIn,
    FeedbackSource,
    RuleResult,
    TargetType,
    ValidationStatus,
)

_TARGET_CASE_SQL = {
    TargetType.CLAIM: "SELECT case_id FROM evidence.claim WHERE claim_id = %s",
    TargetType.CONTRADICTION: (
        "SELECT case_id FROM evidence.contradiction WHERE contradiction_id = %s"
    ),
    TargetType.ALERT: "SELECT ca.case_id FROM workflow.case_alert ca WHERE ca.alert_id = %s",
    TargetType.REPORT: "SELECT case_id FROM runtime.report WHERE report_id = %s",
}
_EDITABLE = frozenset({TargetType.CLAIM, TargetType.REPORT})
# structural failures a human may not wave through; conflicts and duplicates are for judgement
_NOT_OVERRIDABLE = frozenset(
    {"TARGET_EXISTS_IN_CASE", "EDIT_TARGET_ALLOWED", "EDIT_HAS_PROPOSAL", "LABEL_HAS_LABEL"}
)
_VERDICT_VALUES = {a.value for a in VERDICTS}


def _target_in_case(conn: psycopg.Connection, fb: FeedbackIn) -> bool:
    if fb.target_type is TargetType.ER_LINK:
        row = conn.execute(
            "SELECT 1 FROM derived.er_link_current l WHERE l.link_id = %s AND EXISTS ("
            "  SELECT 1 FROM evidence.evidence_item e WHERE e.case_id = %s "
            "  AND e.evidence_type = 'ER_LINK_EVIDENCE' AND e.content->>'link_id' = l.link_id)",
            (fb.target_id, fb.case_id),
        ).fetchone()
        return row is not None
    row = conn.execute(_TARGET_CASE_SQL[fb.target_type], (fb.target_id,)).fetchone()
    return row is not None and str(row[0]) == fb.case_id


def _shape_rules(fb: FeedbackIn) -> list[RuleResult]:
    return [
        RuleResult(
            rule="EDIT_TARGET_ALLOWED",
            passed=fb.action is not FeedbackAction.EDIT or fb.target_type in _EDITABLE,
            detail="EDIT is only allowed on CLAIM and REPORT",
        ),
        RuleResult(
            rule="EDIT_HAS_PROPOSAL",
            passed=fb.action is not FeedbackAction.EDIT
            or bool(str(fb.payload.get("proposed_text", "")).strip()),
            detail="an EDIT must carry payload.proposed_text",
        ),
        RuleResult(
            rule="LABEL_HAS_LABEL",
            passed=fb.action is not FeedbackAction.LABEL
            or bool(str(fb.payload.get("label", "")).strip()),
            detail="a LABEL must carry payload.label",
        ),
    ]


def validate_rules(
    conn: psycopg.Connection, fb: FeedbackIn, user_id: str, self_id: str | None = None
) -> tuple[ValidationStatus, list[RuleResult], list[str]]:
    """Run every rule; returns (status, results, ids of earlier feedback this conflicts with)."""
    results = _shape_rules(fb)
    results.append(
        RuleResult(
            rule="TARGET_EXISTS_IN_CASE",
            passed=_target_in_case(conn, fb),
            detail=f"{fb.target_type.value} {fb.target_id} must exist in case {fb.case_id}",
        )
    )
    prior = conn.execute(
        "SELECT feedback_id, user_id, action FROM workflow.feedback_current "
        "WHERE case_id=%s AND target_type=%s AND target_id=%s "
        "AND (%s::text IS NULL OR feedback_id <> %s) ORDER BY created_at, feedback_id",
        (fb.case_id, fb.target_type.value, fb.target_id, self_id, self_id),
    ).fetchall()
    duplicate = any(str(p[1]) == user_id and str(p[2]) == fb.action.value for p in prior)
    results.append(
        RuleResult(
            rule="NOT_A_DUPLICATE",
            passed=not duplicate,
            detail="the same user already gave this action on this target",
        )
    )
    conflicts = (
        [str(p[0]) for p in prior if str(p[2]) in _VERDICT_VALUES and str(p[2]) != fb.action.value]
        if fb.action in VERDICTS
        else []
    )
    results.append(
        RuleResult(
            rule="NO_CONFLICTING_VERDICT",
            passed=not conflicts,
            detail="an opposite verdict exists on this target; a second review must decide"
            if conflicts
            else "",
        )
    )
    status = (
        ValidationStatus.VALIDATED
        if all(r.passed for r in results)
        else ValidationStatus.QUARANTINED
    )
    return status, results, conflicts


def _record_validation(
    conn: psycopg.Connection,
    feedback_id: str,
    status: ValidationStatus,
    results: Sequence[RuleResult],
    *,
    validated_by: str,
    kind: str,
    note: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO workflow.feedback_validation (feedback_id, status, checks, note, "
        "validated_by, validator_kind) VALUES (%s,%s,%s,%s,%s,%s)",
        (
            feedback_id, status.value, json.dumps([r.model_dump() for r in results]), note,
            validated_by, kind,
        ),
    )  # fmt: skip


def submit_feedback(
    conn: psycopg.Connection, fb: FeedbackIn, *, user_id: str, user_role: str
) -> tuple[str, ValidationStatus, list[RuleResult]]:
    """Record the feedback and validate it by rules, in one transaction. The caller commits.
    `user_id` and `user_role` must come from the verified identity, never from the request body."""
    if user_role not in ("analyst", "approver"):
        raise AegisValidationError("only analysts and approvers can give feedback")
    feedback_id = new_id("fdb")
    conn.execute(
        "INSERT INTO workflow.feedback (feedback_id, case_id, target_type, target_id, action, "
        "payload, reason, user_id, user_role, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            feedback_id, fb.case_id, fb.target_type.value, fb.target_id, fb.action.value,
            json.dumps(fb.payload), fb.reason, user_id, user_role, fb.source.value,
        ),
    )  # fmt: skip
    status, results, conflicts = validate_rules(conn, fb, user_id, feedback_id)
    _record_validation(
        conn, feedback_id, status, results, validated_by=f"rules:{RULES_VERSION}", kind="RULES"
    )
    for other in conflicts:  # the earlier verdict is no longer safe to use either
        detail = f"conflicts with {feedback_id}"
        _record_validation(
            conn,
            other,
            ValidationStatus.QUARANTINED,
            [RuleResult(rule="NO_CONFLICTING_VERDICT", passed=False, detail=detail)],
            validated_by=f"rules:{RULES_VERSION}",
            kind="RULES",
        )
    return feedback_id, status, results


def second_review(
    reviewer_conn: psycopg.Connection,
    feedback_id: str,
    status: ValidationStatus,
    reviewer_id: str,
    note: str,
) -> None:
    """A human decides a quarantined item (reviewer login only; the database refuses others)."""
    if status is ValidationStatus.PENDING:
        raise AegisValidationError("a second review must VALIDATE or QUARANTINE")
    if not note.strip():
        raise AegisValidationError("a second review needs a note")
    row = reviewer_conn.execute(
        "SELECT user_id, validation_status, validation_checks FROM workflow.feedback_current "
        "WHERE feedback_id=%s",
        (feedback_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"feedback {feedback_id}")
    author, current, checks = str(row[0]), str(row[1]), row[2] or []
    if reviewer_id.strip().casefold() == author.strip().casefold():
        raise ConflictError("the author of feedback cannot be its second reviewer")
    if current != ValidationStatus.QUARANTINED.value:
        raise ConflictError(f"only QUARANTINED feedback can be second-reviewed (this is {current})")
    failed = {c["rule"] for c in checks if not c.get("passed", True)}
    if status is ValidationStatus.VALIDATED and failed & _NOT_OVERRIDABLE:
        raise ConflictError(
            f"a human cannot validate feedback that failed {sorted(failed & _NOT_OVERRIDABLE)}"
        )
    try:
        _record_validation(
            reviewer_conn,
            feedback_id,
            status,
            [
                RuleResult(
                    rule="HUMAN_SECOND_REVIEW",
                    passed=status is ValidationStatus.VALIDATED,
                    detail=note[:500],
                )
            ],
            validated_by=reviewer_id,
            kind="HUMAN",
            note=note,
        )
    except psycopg.errors.RaiseException as exc:
        raise ConflictError(str(exc).splitlines()[0]) from exc


def validated_records(conn: psycopg.Connection, case_id: str | None = None) -> list[dict[str, Any]]:
    """Training-safe view of validated feedback: no user id, no login; role and source kept."""
    rows = conn.execute(
        "SELECT feedback_id, case_id, target_type, target_id, action, payload, reason, "
        "user_role, source FROM workflow.feedback_current "
        "WHERE validation_status='VALIDATED' AND (%s::text IS NULL OR case_id=%s) "
        "ORDER BY feedback_id",
        (case_id, case_id),
    ).fetchall()
    return [
        {
            "feedback_id": str(r[0]), "case_id": str(r[1]), "target_type": str(r[2]),
            "target_id": str(r[3]), "action": str(r[4]), "payload": dict(r[5]),
            "reason": r[6], "user_role": str(r[7]), "source": str(r[8]),
        }
        for r in rows
    ]  # fmt: skip


EXPORT_SUBJECT_PREFIX = "feedback-export:"


def preview_export(conn: psycopg.Connection, case_id: str | None = None) -> dict[str, Any]:
    """What an export would contain right now: the manifest hash and counts, without records."""
    records = validated_records(conn, case_id)
    if not records:
        raise AegisValidationError("there is no validated feedback to export")
    return {
        "manifest_hash": content_hash({"rules": RULES_VERSION, "records": records}),
        "n_records": len(records),
        "n_simulated": sum(1 for r in records if r["source"] == FeedbackSource.SIMULATED.value),
        "records": records,
    }


def request_export_approval(
    conn: psycopg.Connection, requested_by: str, justification: str, case_id: str | None = None
) -> dict[str, Any]:
    """Ask for a FEEDBACK_TO_TRAINING decision on exactly the export that exists now (its hash is
    the request subject). An approver then decides it through the normal approval route."""
    from aegis.domain.cases import ApprovalAction
    from aegis.services.cases import request_approval

    prev = preview_export(conn, case_id)
    request_id = request_approval(
        conn, ApprovalAction.FEEDBACK_TO_TRAINING,
        EXPORT_SUBJECT_PREFIX + prev["manifest_hash"], requested_by, justification,
    )  # fmt: skip
    return {
        "request_id": request_id, "manifest_hash": prev["manifest_hash"],
        "n_records": prev["n_records"], "n_simulated": prev["n_simulated"],
    }  # fmt: skip


def export_validated(
    conn: psycopg.Connection, approval_id: str, created_by: str, case_id: str | None = None
) -> dict[str, Any]:
    """Create a versioned manifest of validated feedback. Needs an APPROVED FEEDBACK_TO_TRAINING
    decision whose request named this exact manifest hash (both checked by the database).
    Returns the manifest, including the hash and a count of SIMULATED items, which every
    consumer must report."""
    prev = preview_export(conn, case_id)
    records, manifest_hash, n_sim = prev["records"], prev["manifest_hash"], prev["n_simulated"]
    export_id = new_id("fex")
    try:
        conn.execute(
            "INSERT INTO workflow.feedback_export (export_id, approval_id, manifest_hash, "
            "feedback_ids, n_simulated, created_by) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                export_id, approval_id, manifest_hash, [r["feedback_id"] for r in records],
                n_sim, created_by,
            ),
        )  # fmt: skip
    except psycopg.errors.RaiseException as exc:
        raise ConflictError(str(exc).splitlines()[0]) from exc
    except psycopg.errors.UniqueViolation as exc:
        raise ConflictError("this approval was already used for an export") from exc
    return {
        "export_id": export_id, "manifest_hash": manifest_hash, "rules_version": RULES_VERSION,
        "n_records": len(records), "n_simulated": n_sim, "records": records,
    }  # fmt: skip
