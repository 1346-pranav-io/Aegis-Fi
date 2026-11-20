"""Contradiction records, human-only status workflow and closure gating (migrations 0021, 0022).

T-INV-18: a case with an OPEN HIGH contradiction cannot be closed until a human has moved it to
EXPLAINED, RESOLVED_BY_HUMAN or DISMISSED. FR-CON-02/03.
"""

import json
from datetime import UTC, datetime

import psycopg
import pytest

from aegis.analytics.contradictions import detect_all, fingerprint
from aegis.capabilities.contradiction import service as csvc
from aegis.common.errors import AegisValidationError, ConflictError, NotFound, PermissionDenied
from aegis.domain.cases import ApprovalAction, CaseStatus, Disposition
from aegis.domain.evidence import (
    ContradictionDraft,
    ContradictionMember,
    ContradictionStatus,
    ContradictionType,
    MemberType,
    Severity,
)
from aegis.services import cases as svc

pytestmark = pytest.mark.integration
NOW = datetime(2022, 9, 10, tzinfo=UTC)
_n = 0


def mk_case(c: psycopg.Connection, acct: str = "A") -> str:
    r = svc.create_alerts(
        c,
        [svc.ScoredItem(f"t-{acct}", acct, 0.5, ("X",))],
        signal_type="S",
        as_of=NOW,
        model_artefact_id="m",
    )
    c.commit()
    return r[0].case_id


def mk_evidence(c: psycopg.Connection, case_id: str, n: int = 2) -> list[str]:
    global _n
    ids = []
    for _ in range(n):
        _n += 1
        eid = f"ev_w{_n}"
        c.execute(
            "INSERT INTO evidence.evidence_item (evidence_id, case_id, evidence_type, content, "
            "summary_text, producer, as_of, content_hash, completeness, schema_version) "
            "VALUES (%s,%s,'TXN_AGGREGATE','{}','s','{\"kind\":\"TOOL\"}',now(),%s,'FULL',1)",
            (eid, case_id, f"h-{eid}"),
        )
        c.execute("INSERT INTO evidence.evidence_input VALUES (%s,'SOURCE','t','h')", (eid,))
        ids.append(eid)
    c.commit()
    return ids


def draft(case_id: str, ev: list[str], sev: Severity = Severity.HIGH) -> ContradictionDraft:
    return ContradictionDraft(
        case_id=case_id,
        type=ContradictionType.AMOUNT_MISMATCH,
        severity=sev,
        detector_name="amount_mismatch",
        detector_version="1.0.0",
        members=tuple(
            ContradictionMember(member_type=MemberType.EVIDENCE, member_id=e) for e in ev
        ),
    )


def to_review(c: psycopg.Connection, case_id: str) -> None:
    svc.transition(c, case_id, CaseStatus.INVESTIGATING, "start")
    svc.transition(c, case_id, CaseStatus.PENDING_REVIEW, "done")
    c.commit()


def approved_closure(app: psycopg.Connection, approver: psycopg.Connection, case_id: str) -> str:
    req = svc.request_approval(app, ApprovalAction.CASE_CLOSURE, case_id, "analyst1", "ok")
    app.commit()
    apv = svc.decide_approval(approver, req, True, "boss", "agreed")
    approver.commit()
    return apv


def try_close(c: psycopg.Connection, case_id: str, apv: str) -> None:
    svc.close_case(c, case_id, Disposition.INCONCLUSIVE, apv, "closing")


def test_recording_is_idempotent_and_opens_the_contradiction(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        ev = mk_evidence(c, case_id)
        d = draft(case_id, ev)
        first = csvc.record_contradiction(c, d)
        c.commit()
        again = csvc.record_contradiction(c, d)
        c.commit()
        assert first.created and not again.created
        assert first.contradiction_id == again.contradiction_id
        assert csvc.current_status(c, first.contradiction_id) is ContradictionStatus.OPEN
        n = c.execute("SELECT count(*) FROM evidence.contradiction_member").fetchone()
        assert n == (2,)
        stored = c.execute("SELECT detector FROM evidence.contradiction").fetchone()
        assert stored is not None and stored[0]["fingerprint"] == fingerprint(d)
        # the same members under another case do not collide
        assert csvc.open_contradictions(c, case_id) == [
            (first.contradiction_id, "AMOUNT_MISMATCH", "HIGH")
        ]
        assert csvc.open_contradictions(c, case_id, severity="LOW") == []


def test_unique_fingerprint_index_blocks_a_raw_duplicate(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        ev = mk_evidence(c, case_id)
        d = draft(case_id, ev)
        csvc.record_contradiction(c, d)
        c.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(
                "INSERT INTO evidence.contradiction (contradiction_id, case_id, type, severity, "
                "detector) VALUES ('ctr_dup',%s,'AMOUNT_MISMATCH','HIGH',%s)",
                (case_id, json.dumps({"fingerprint": fingerprint(d)})),
            )


def test_only_humans_move_a_contradiction_past_open(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        cid = csvc.record_contradiction(c, draft(case_id, mk_evidence(c, case_id))).contradiction_id
        c.commit()
        for kind in ("AGENT", "SYSTEM", "human", ""):
            with pytest.raises(PermissionDenied):
                csvc.set_status(
                    c, cid, ContradictionStatus.DISMISSED, actor="x", actor_kind=kind, note="n"
                )
        with pytest.raises(AegisValidationError):
            csvc.set_status(
                c, cid, ContradictionStatus.EXPLAINED, actor="h", actor_kind="HUMAN", note=" "
            )
        with pytest.raises(NotFound):
            csvc.set_status(
                c,
                "ctr_none",
                ContradictionStatus.EXPLAINED,
                actor="h",
                actor_kind="HUMAN",
                note="n",
            )
        c.rollback()
        # the database refuses a non-human even if this module is bypassed
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(
                "INSERT INTO evidence.contradiction_status_event (contradiction_id, status, "
                "actor_kind, actor) VALUES (%s,'DISMISSED','AGENT','bot')",
                (cid,),
            )
        c.rollback()
        csvc.set_status(
            c, cid, ContradictionStatus.EXPLAINED, actor="h1", actor_kind="HUMAN", note="period"
        )
        csvc.set_status(
            c, cid, ContradictionStatus.RESOLVED_BY_HUMAN, actor="h1", actor_kind="HUMAN", note="ok"
        )
        c.commit()
        with pytest.raises(ConflictError, match="illegal"):  # terminal
            csvc.set_status(
                c, cid, ContradictionStatus.OPEN, actor="h1", actor_kind="HUMAN", note="again"
            )
        c.rollback()
        # resolution never deletes either side: members and every event are still there
        assert c.execute("SELECT count(*) FROM evidence.contradiction_member").fetchone() == (2,)
        assert c.execute("SELECT count(*) FROM evidence.contradiction_status_event").fetchone() == (
            3,
        )


def test_explanation_proposal_is_stored_but_status_stays_open(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        ev = mk_evidence(c, case_id)
        d = draft(case_id, ev).model_copy(
            update={
                "explanation_proposal": {"text": "periods differ", "cited_evidence_ids": ev[:1]}
            }
        )
        rec = csvc.record_contradiction(c, d)
        c.commit()
        assert csvc.current_status(c, rec.contradiction_id) is ContradictionStatus.OPEN
        row = c.execute("SELECT explanation_proposal FROM evidence.contradiction").fetchone()
        assert row is not None and row[0]["cited_evidence_ids"] == ev[:1]


def test_open_high_contradiction_blocks_closure_until_a_human_deals_with_it(
    app_url: str, approver_url: str
) -> None:
    for outcome in (
        ContradictionStatus.EXPLAINED,
        ContradictionStatus.RESOLVED_BY_HUMAN,
        ContradictionStatus.DISMISSED,
    ):
        with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ac:
            case_id = mk_case(c, f"acct-{outcome.value}")
            cid = csvc.record_contradiction(
                c, draft(case_id, mk_evidence(c, case_id))
            ).contradiction_id
            c.commit()
            to_review(c, case_id)
            apv = approved_closure(c, ac, case_id)
            with pytest.raises(ConflictError, match="open HIGH contradiction"):
                try_close(c, case_id, apv)
            c.rollback()
            # an agent cannot clear it, and nothing else about the case changed
            with pytest.raises(PermissionDenied):
                csvc.set_status(c, cid, outcome, actor="bot", actor_kind="AGENT", note="n")
            with pytest.raises(ConflictError, match="open HIGH contradiction"):
                try_close(c, case_id, apv)
            c.rollback()
            csvc.set_status(c, cid, outcome, actor="human1", actor_kind="HUMAN", note="reviewed")
            c.commit()
            try_close(c, case_id, apv)
            c.commit()
            row = c.execute(
                "SELECT status FROM workflow.investigation_case WHERE case_id=%s", (case_id,)
            ).fetchone()
            assert row == ("CLOSED",)


def test_only_open_high_contradictions_of_this_case_block(app_url: str, approver_url: str) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ac:
        case_id, other = mk_case(c, "A"), mk_case(c, "B")
        ev_a, ev_b = mk_evidence(c, case_id, 4), mk_evidence(c, other)
        csvc.record_contradiction(c, draft(case_id, ev_a[:2], Severity.MEDIUM))
        csvc.record_contradiction(c, draft(case_id, ev_a[2:], Severity.LOW))
        csvc.record_contradiction(c, draft(other, ev_b, Severity.HIGH))  # another case
        c.commit()
        to_review(c, case_id)
        try_close(c, case_id, approved_closure(c, ac, case_id))
        c.commit()  # not blocked by MEDIUM / LOW / another case's HIGH
        row = c.execute(
            "SELECT status FROM workflow.investigation_case WHERE case_id=%s", (case_id,)
        ).fetchone()
        assert row == ("CLOSED",)


def test_reopened_contradiction_blocks_again(app_url: str, approver_url: str) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ac:
        case_id = mk_case(c)
        cid = csvc.record_contradiction(c, draft(case_id, mk_evidence(c, case_id))).contradiction_id
        csvc.set_status(
            c, cid, ContradictionStatus.EXPLAINED, actor="h", actor_kind="HUMAN", note="n"
        )
        c.commit()
        to_review(c, case_id)
        apv = approved_closure(c, ac, case_id)
        csvc.set_status(
            c, cid, ContradictionStatus.OPEN, actor="h", actor_kind="HUMAN", note="redo"
        )
        c.commit()
        with pytest.raises(ConflictError, match="open HIGH contradiction"):
            try_close(c, case_id, apv)
        c.rollback()
        csvc.set_status(
            c, cid, ContradictionStatus.DISMISSED, actor="h", actor_kind="HUMAN", note="x"
        )
        c.commit()
        try_close(c, case_id, apv)
        c.commit()


def test_earlier_guards_still_hold_with_the_new_gate(app_url: str, approver_url: str) -> None:
    """Spot checks next to the full regression run of test_cases_workflow / test_approval_hardening
    against the replaced case_guard."""
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ac:
        case_id = mk_case(c)
        # illegal transition and the closure approval gate still apply with NO contradiction
        with pytest.raises(psycopg.errors.RaiseException, match="illegal case transition"):
            c.execute("UPDATE workflow.investigation_case SET status='PENDING_REVIEW' "
                      "WHERE case_id=%s", (case_id,))  # fmt: skip
        c.rollback()
        to_review(c, case_id)
        with pytest.raises(ConflictError, match="needs an APPROVED CASE_CLOSURE"):
            try_close(c, case_id, "apv_none")
        c.rollback()
        # priority may not decrease, and cannot be written directly any more
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE workflow.investigation_case SET priority_score = 0 WHERE case_id=%s",
                      (case_id,))  # fmt: skip
        c.rollback()
        apv = approved_closure(c, ac, case_id)
        try_close(c, case_id, apv)
        c.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="CLOSED and frozen"):
            c.execute("UPDATE workflow.investigation_case SET closed_at = now() WHERE case_id=%s",
                      (case_id,))  # fmt: skip
        c.rollback()


def test_detectors_through_the_service_into_the_gate(app_url: str, approver_url: str) -> None:
    """End to end on the database: seeded corpus -> detectors -> recorded -> case blocked."""
    from aegis.eval.perturbation import generate_contradiction_corpus, to_detection_input

    corpus = generate_contradiction_corpus(seed=31, n_per_type=1, n_decoys_per_type=0)
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ac:
        case_id = mk_case(c)
        # store the corpus evidence/claims under this case so members exist
        for eid, e in corpus.evidence.items():
            c.execute(
                "INSERT INTO evidence.evidence_item (evidence_id, case_id, evidence_type, content,"
                " summary_text, producer, as_of, content_hash, completeness, schema_version) "
                "VALUES (%s,%s,%s,%s,'s',%s,%s,%s,'FULL',1)",
                (
                    eid, case_id, e.evidence_type.value, json.dumps(e.content),
                    json.dumps(e.producer.model_dump(mode="json")), e.as_of,
                    e.content_hash() + case_id,
                ),
            )  # fmt: skip
            c.execute(
                "INSERT INTO evidence.evidence_input VALUES (%s,'SOURCE','seed','seed')", (eid,)
            )
        for cl_id, cl in corpus.claims.items():
            c.execute(
                "INSERT INTO evidence.claim (claim_id, case_id, author_kind, author_name, "
                "author_version, structure, text, claim_kind, self_reported_confidence) "
                "VALUES (%s,%s,'AGENT','seed','1',%s,%s,%s,'NONE')",
                (cl_id, case_id, cl.structure.model_dump_json(), cl.text, cl.claim_kind.value),
            )
            for cit in cl.citations:
                c.execute(
                    "INSERT INTO evidence.claim_evidence_link VALUES (%s,%s,%s)",
                    (cl_id, cit.evidence_id, cit.support_role.value),
                )
        c.commit()
        inp = to_detection_input(corpus)
        drafts = [
            d.model_copy(update={"case_id": case_id}) for d in detect_all(inp, case_id=case_id)
        ]
        ids = [csvc.record_contradiction(c, d).contradiction_id for d in drafts]
        c.commit()
        assert len(ids) == len(corpus.seeded) == 5
        high = csvc.open_contradictions(c, case_id, severity="HIGH")
        assert high  # some seeded findings are HIGH
        to_review(c, case_id)
        apv = approved_closure(c, ac, case_id)
        with pytest.raises(ConflictError, match="open HIGH contradiction"):
            try_close(c, case_id, apv)
        c.rollback()
        for cid, _, _ in high:
            csvc.set_status(
                c, cid, ContradictionStatus.DISMISSED, actor="h", actor_kind="HUMAN", note="n"
            )
        c.commit()
        try_close(c, case_id, apv)  # MEDIUM / LOW findings may stay open
        c.commit()
