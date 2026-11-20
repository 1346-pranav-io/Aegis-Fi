"""Feedback: capture, rule validation, quarantine, human second review, approval-gated export
(FR-HITL-01/03/04, D-09f, D-30). Never touches claims, evidence or source."""

from datetime import UTC, datetime

import psycopg
import pytest
from human_ops import as_reviewer

from aegis.common.errors import AegisValidationError, ConflictError
from aegis.domain.cases import ApprovalAction
from aegis.domain.feedback import (
    FeedbackAction,
    FeedbackIn,
    FeedbackSource,
    TargetType,
    ValidationStatus,
)
from aegis.services import cases as svc
from aegis.services import feedback as fb

pytestmark = pytest.mark.integration
NOW = datetime(2022, 9, 10, tzinfo=UTC)
_n = 0


def mk_case_with_claim(c: psycopg.Connection, acct: str = "A") -> tuple[str, str]:
    global _n
    _n += 1
    r = svc.create_alerts(
        c, [svc.ScoredItem(f"t-{acct}{_n}", f"{acct}{_n}", 0.5, ("X",))],
        signal_type="S", as_of=NOW, model_artefact_id="m",
    )  # fmt: skip
    case_id = r[0].case_id
    eid, cid = f"ev_fb{_n}", f"clm_fb{_n}"
    c.execute(
        "INSERT INTO evidence.evidence_item (evidence_id, case_id, evidence_type, content, "
        "summary_text, producer, as_of, content_hash, completeness, schema_version) "
        "VALUES (%s,%s,'TXN_AGGREGATE','{}','s','{\"kind\":\"TOOL\"}',now(),%s,'FULL',1)",
        (eid, case_id, "h-" + eid),
    )
    c.execute("INSERT INTO evidence.evidence_input VALUES (%s,'SOURCE','t','h')", (eid,))
    c.execute(
        "INSERT INTO evidence.claim (claim_id, case_id, author_kind, author_name, author_version, "
        "structure, text, claim_kind, self_reported_confidence) "
        "VALUES (%s,%s,'AGENT','inv','1','{}','the claim','AGGREGATE','NONE')",
        (cid, case_id),
    )
    c.execute("INSERT INTO evidence.claim_evidence_link VALUES (%s,%s,'SUPPORTS')", (cid, eid))
    c.commit()
    return case_id, cid


def give(
    c: psycopg.Connection, case_id: str, claim_id: str, action: FeedbackAction, user: str,
    reason: str | None = "because", payload: dict[str, object] | None = None,
    source: FeedbackSource = FeedbackSource.HUMAN, target: TargetType = TargetType.CLAIM,
) -> tuple[str, ValidationStatus]:  # fmt: skip
    f = FeedbackIn(
        case_id=case_id, target_type=target, target_id=claim_id, action=action, reason=reason,
        payload=payload or {}, source=source,
    )  # fmt: skip
    fid, status, _ = fb.submit_feedback(c, f, user_id=user, user_role="analyst")
    c.commit()
    return fid, status


def test_valid_feedback_is_recorded_validated_and_leaves_claims_untouched(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id, claim = mk_case_with_claim(c)
        before = c.execute("SELECT text, claim_kind FROM evidence.claim").fetchall()
        fid, status = give(c, case_id, claim, FeedbackAction.ACCEPT, "alice", reason=None)
        eid, estatus = give(
            c, case_id, claim, FeedbackAction.EDIT, "alice", payload={"proposed_text": "better"}
        )
        assert status is estatus is ValidationStatus.VALIDATED
        assert c.execute("SELECT text, claim_kind FROM evidence.claim").fetchall() == before
        row = c.execute(
            "SELECT user_login, validation_status FROM workflow.feedback_current WHERE feedback_id=%s",
            (fid,),
        ).fetchone()
        assert row == ("aegis_app", "VALIDATED")  # login set by the database


def test_rules_quarantine_bad_feedback_with_the_reason(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id, claim = mk_case_with_claim(c)
        other_case, other_claim = mk_case_with_claim(c)
        cases = [
            (
                case_id,
                "clm_nope",
                FeedbackAction.ACCEPT,
                None,
                {},
                TargetType.CLAIM,
                "TARGET_EXISTS",
            ),
            (
                case_id,
                other_claim,
                FeedbackAction.ACCEPT,
                None,
                {},
                TargetType.CLAIM,
                "TARGET_EXISTS",
            ),
            (case_id, claim, FeedbackAction.EDIT, "r", {}, TargetType.CLAIM, "EDIT_HAS_PROPOSAL"),
            (case_id, claim, FeedbackAction.LABEL, None, {}, TargetType.CLAIM, "LABEL_HAS_LABEL"),
        ]
        for cid, target, action, reason, payload, ttype, rule in cases:
            _, status = give(c, cid, target, action, "alice", reason, payload, target=ttype)
            assert status is ValidationStatus.QUARANTINED, rule
        # EDIT on an alert is not allowed
        alert = c.execute(
            "SELECT alert_id FROM workflow.case_alert WHERE case_id=%s", (case_id,)
        ).fetchone()
        assert alert is not None
        _, st = give(c, case_id, str(alert[0]), FeedbackAction.EDIT, "alice",
                     payload={"proposed_text": "x"}, target=TargetType.ALERT)  # fmt: skip
        assert st is ValidationStatus.QUARANTINED
        checks = c.execute(
            "SELECT validation_checks FROM workflow.feedback_current "
            "WHERE validation_status='QUARANTINED' ORDER BY created_at LIMIT 1"
        ).fetchone()
        assert checks is not None and any(not r["passed"] for r in checks[0])


def test_database_requires_a_reason_for_reject_and_edit(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id, claim = mk_case_with_claim(c)
        for action in ("REJECT", "EDIT"):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(
                    "INSERT INTO workflow.feedback (feedback_id, case_id, target_type, target_id, "
                    "action, user_id, user_role, source) VALUES "
                    "('f',%s,'CLAIM',%s,%s,'u','analyst','HUMAN')",
                    (case_id, claim, action),
                )
            c.rollback()
        with pytest.raises(AegisValidationError):  # only analysts and approvers
            fb.submit_feedback(
                c, FeedbackIn(case_id=case_id, target_type=TargetType.CLAIM, target_id=claim,
                              action=FeedbackAction.ACCEPT), user_id="x", user_role="auditor",
            )  # fmt: skip


def test_duplicates_and_conflicting_verdicts_are_quarantined_until_a_human_decides(
    app_url: str, reviewer_url: str
) -> None:
    with psycopg.connect(app_url) as c:
        case_id, claim = mk_case_with_claim(c)
        a1, s1 = give(c, case_id, claim, FeedbackAction.ACCEPT, "alice")
        assert s1 is ValidationStatus.VALIDATED
        _, dup = give(c, case_id, claim, FeedbackAction.ACCEPT, "alice")  # same user, same action
        assert dup is ValidationStatus.QUARANTINED
        b1, s2 = give(c, case_id, claim, FeedbackAction.REJECT, "bob")  # opposite verdict
        assert s2 is ValidationStatus.QUARANTINED
        cur = dict(
            c.execute(
                "SELECT feedback_id, validation_status FROM workflow.feedback_current"
            ).fetchall()
        )
        assert cur[a1] == "QUARANTINED"  # the earlier verdict is no longer safe either
        # the app login cannot do a human second review; the reviewer login can
        with pytest.raises(ConflictError, match="human-review"):
            fb.second_review(c, b1, ValidationStatus.VALIDATED, "boss", "checked")
        c.rollback()
        as_reviewer(c, fb.second_review, b1, ValidationStatus.VALIDATED, "boss", "bob is right")
        as_reviewer(
            c, fb.second_review, a1, ValidationStatus.QUARANTINED, "boss", "alice was wrong"
        )
        cur = dict(
            c.execute(
                "SELECT feedback_id, validation_status FROM workflow.feedback_current"
            ).fetchall()
        )
        assert cur[b1] == "VALIDATED" and cur[a1] == "QUARANTINED"
        with pytest.raises(AegisValidationError):
            as_reviewer(c, fb.second_review, b1, ValidationStatus.VALIDATED, "boss", " ")
        # history is kept: every validation row is still there
        n = c.execute(
            "SELECT count(*) FROM workflow.feedback_validation WHERE feedback_id=%s", (a1,)
        ).fetchone()
        assert n is not None and n[0] >= 3


def test_feedback_records_are_insert_only(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id, claim = mk_case_with_claim(c)
        fid, _ = give(c, case_id, claim, FeedbackAction.ACCEPT, "alice")
        for stmt in (
            "UPDATE workflow.feedback SET reason='x'",
            "DELETE FROM workflow.feedback",
            "UPDATE workflow.feedback_validation SET status='VALIDATED'",
            "DELETE FROM workflow.feedback_validation",
            "TRUNCATE workflow.feedback_export",
        ):
            with pytest.raises(psycopg.Error):
                c.execute(stmt)
            c.rollback()
        n = c.execute(
            "SELECT count(*) FROM audit.event WHERE action LIKE 'feedback.%%' AND object_id=%s",
            (fid,),
        ).fetchone()
        assert n is not None and n[0] >= 2  # the submission and its validation were audited


def test_export_needs_an_approved_decision_and_only_exports_validated_feedback(
    app_url: str, approver_url: str
) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        case_id, claim = mk_case_with_claim(c)
        good, _ = give(
            c, case_id, claim, FeedbackAction.ACCEPT, "alice", source=FeedbackSource.SIMULATED
        )
        give(c, case_id, claim, FeedbackAction.ACCEPT, "alice")  # duplicate -> quarantined

        def approval(action: ApprovalAction, approve: bool, subject: str = "feedback") -> str:
            req = svc.request_approval(c, action, subject, "analyst1", "for training")
            c.commit()
            a = svc.decide_approval(ap, req, approve, "boss", "reviewed")
            ap.commit()
            return a

        with pytest.raises(ConflictError, match="APPROVED FEEDBACK_TO_TRAINING"):
            fb.export_validated(c, approval(ApprovalAction.MODEL_ACTIVATION, True), "admin1")
        c.rollback()
        with pytest.raises(ConflictError):
            fb.export_validated(c, approval(ApprovalAction.FEEDBACK_TO_TRAINING, False), "admin1")
        c.rollback()
        # an approval whose subject is not this export's hash does not cover it (review H-2)
        with pytest.raises(ConflictError, match="does not cover this export"):
            fb.export_validated(c, approval(ApprovalAction.FEEDBACK_TO_TRAINING, True), "admin1")
        c.rollback()
        asked = fb.request_export_approval(c, "analyst1", "for training")
        c.commit()
        ok = svc.decide_approval(ap, asked["request_id"], True, "boss", "reviewed")
        ap.commit()
        out = fb.export_validated(c, ok, "admin1")
        assert out["manifest_hash"] == asked["manifest_hash"]
        c.commit()
        assert [r["feedback_id"] for r in out["records"]] == [good]
        assert out["n_simulated"] == 1 and len(out["manifest_hash"]) == 64
        assert all("user_id" not in r for r in out["records"])  # no personal id in the export
        with pytest.raises(ConflictError, match="already used"):
            fb.export_validated(c, ok, "admin1")  # one export per approval
        c.rollback()
        # the database refuses an export that names quarantined feedback or lies about simulation
        bad = approval(ApprovalAction.FEEDBACK_TO_TRAINING, True, "feedback-export:" + "0" * 64)
        quarantined = c.execute(
            "SELECT feedback_id FROM workflow.feedback_current WHERE validation_status='QUARANTINED'"
        ).fetchone()
        assert quarantined is not None
        with pytest.raises(psycopg.errors.RaiseException, match="not VALIDATED"):
            c.execute(
                "INSERT INTO workflow.feedback_export (export_id, approval_id, manifest_hash, "
                "feedback_ids, n_simulated, created_by) VALUES ('x',%s,%s,%s,0,'u')",
                (bad, "0" * 64, [str(quarantined[0])]),
            )
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="n_simulated"):
            c.execute(
                "INSERT INTO workflow.feedback_export (export_id, approval_id, manifest_hash, "
                "feedback_ids, n_simulated, created_by) VALUES ('x',%s,%s,%s,0,'u')",
                (bad, "0" * 64, [good]),
            )
        c.rollback()
