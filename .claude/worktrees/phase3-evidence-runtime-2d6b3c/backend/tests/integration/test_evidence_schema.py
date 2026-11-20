"""Evidence schema (migration 0009): the rules live in the database, not only in Python."""

import json
from datetime import UTC, datetime

import psycopg
import pytest

from aegis.services import cases as svc

pytestmark = pytest.mark.integration
NOW = datetime(2022, 9, 10, tzinfo=UTC)


def case(c: psycopg.Connection, acct: str = "A") -> str:
    r = svc.create_alerts(
        c,
        [svc.ScoredItem(f"t-{acct}", acct, 0.5, ("X",))],
        signal_type="S",
        as_of=NOW,
        model_artefact_id="m",
    )
    c.commit()
    return r[0].case_id


def ev(c: psycopg.Connection, eid: str, case_id: str, *, inputs: bool = True, kind: str = "TOOL",
       h: str | None = None) -> None:  # fmt: skip
    c.execute(
        "INSERT INTO evidence.evidence_item (evidence_id, case_id, evidence_type, content, "
        "summary_text, producer, as_of, content_hash, completeness, schema_version) "
        "VALUES (%s,%s,'TXN_AGGREGATE','{}','s',%s,now(),%s,'FULL',1)",
        (eid, case_id, json.dumps({"kind": kind, "name": "t", "version": "1"}), h or f"h-{eid}"),
    )
    if inputs:
        c.execute("INSERT INTO evidence.evidence_input VALUES (%s,'SOURCE','txn_1','hh')", (eid,))


def claim(c: psycopg.Connection, cid: str, case_id: str, *, cites: list[str]) -> None:
    c.execute(
        "INSERT INTO evidence.claim (claim_id, case_id, author_kind, author_name, author_version, "
        "structure, text, claim_kind, self_reported_confidence) "
        "VALUES (%s,%s,'AGENT','inv','1','{}','t','AGGREGATE','NONE')",
        (cid, case_id),
    )
    for e in cites:
        c.execute("INSERT INTO evidence.claim_evidence_link VALUES (%s,%s,'SUPPORTS')", (cid, e))


def test_evidence_provenance_and_idempotency(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id = case(c)
        ev(c, "ev1", case_id)
        c.commit()
        ev(c, "ev_human", case_id, inputs=False, kind="HUMAN")  # humans need no inputs
        c.commit()
        ev(c, "ev_bad", case_id, inputs=False)  # non-human without inputs: refused at commit
        with pytest.raises(psycopg.errors.RaiseException, match="at least one input"):
            c.commit()
        c.rollback()
        with pytest.raises(psycopg.errors.UniqueViolation):  # same content for same case
            ev(c, "ev_dup", case_id, h="h-ev1")
        c.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):  # probability needs a calibrator
            c.execute(
                "INSERT INTO evidence.evidence_item (evidence_id, case_id, evidence_type, "
                "content, summary_text, producer, as_of, content_hash, completeness, "
                "schema_version, scores) VALUES ('e9',%s,'SIGNAL','{}','s','{\"kind\":\"HUMAN\"}',"
                "now(),'h9','FULL',1,'{\"calibrated_probability\": 0.8}')",
                (case_id,),
            )
        c.rollback()


def test_everything_is_insert_only(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id = case(c)
        ev(c, "ev1", case_id)
        claim(c, "clm1", case_id, cites=["ev1"])
        c.commit()
        for stmt in (
            "UPDATE evidence.evidence_item SET summary_text = 'x'",
            "DELETE FROM evidence.evidence_item",
            "UPDATE evidence.evidence_input SET ref_hash = 'x'",
            "DELETE FROM evidence.evidence_input",
            "UPDATE evidence.claim SET text = 'x'",
            "DELETE FROM evidence.claim",
            "UPDATE evidence.claim_evidence_link SET support_role = 'CONTEXT'",
            "DELETE FROM evidence.claim_evidence_link",
        ):
            with pytest.raises(psycopg.Error):
                c.execute(stmt)
            c.rollback()


def test_claims_need_citations_of_their_own_case(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        a, b = case(c, "A"), case(c, "B")
        ev(c, "evA", a)
        ev(c, "evB", b)
        c.commit()
        claim(c, "clm_ok", a, cites=["evA"])
        c.commit()
        claim(c, "clm_none", a, cites=[])  # no citation: refused at commit (D-07)
        with pytest.raises(psycopg.errors.RaiseException, match="at least one evidence"):
            c.commit()
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="own case"):
            claim(c, "clm_cross", a, cites=["evB"])  # evidence from another case
        c.rollback()


def test_verification_status_rules_and_current_view(app_url: str) -> None:
    ins = (
        "INSERT INTO evidence.verification_result (verification_id, claim_id, status, checks, "
        "rationale, verifier, inputs_seen_hash) VALUES (%s,%s,%s,%s,'r','{}','h')"
    )
    fail = json.dumps([{"check_id": "X", "kind": "DETERMINISTIC", "outcome": "FAIL"}])
    ok = json.dumps([{"check_id": "X", "kind": "DETERMINISTIC", "outcome": "PASS"}])
    with psycopg.connect(app_url) as c:
        case_id = case(c)
        ev(c, "ev1", case_id)
        claim(c, "clm1", case_id, cites=["ev1"])
        c.commit()
        with pytest.raises(psycopg.errors.CheckViolation):  # VERIFIED with a failed check
            c.execute(ins, ("v0", "clm1", "VERIFIED", fail))
        c.rollback()
        c.execute(ins, ("v1", "clm1", "UNSUPPORTED", fail))
        c.commit()
        c.execute(ins, ("v2", "clm1", "VERIFIED", ok))
        c.commit()
        row = c.execute(
            "SELECT status FROM evidence.claim_current_verification WHERE claim_id='clm1'"
        ).fetchone()
        assert row == ("VERIFIED",)  # latest wins; the earlier result is still on record
        n = c.execute("SELECT count(*) FROM evidence.verification_result").fetchone()
        assert n == (2,)


def test_contradictions_members_and_status_machine(app_url: str) -> None:
    def new_ctr(c: psycopg.Connection, cid: str, case_id: str) -> None:
        c.execute(
            "INSERT INTO evidence.contradiction (contradiction_id, case_id, type, severity, detector) "
            "VALUES (%s,%s,'AMOUNT_MISMATCH','HIGH','{}')",
            (cid, case_id),
        )

    def member(c: psycopg.Connection, cid: str, mt: str, mid: str) -> None:
        c.execute("INSERT INTO evidence.contradiction_member VALUES (%s,%s,%s,'')", (cid, mt, mid))

    def status(c: psycopg.Connection, cid: str, st: str, kind: str) -> None:
        c.execute(
            "INSERT INTO evidence.contradiction_status_event (contradiction_id, status, "
            "actor_kind, actor) VALUES (%s,%s,%s,'u')",
            (cid, st, kind),
        )

    with psycopg.connect(app_url) as c:
        a, b = case(c, "A"), case(c, "B")
        ev(c, "e1", a)
        ev(c, "e2", a)
        ev(c, "eB", b)
        claim(c, "c1", a, cites=["e1"])
        c.commit()
        new_ctr(c, "k_one", a)
        member(c, "k_one", "EVIDENCE", "e1")
        with pytest.raises(psycopg.errors.RaiseException, match="at least two"):
            c.commit()
        c.rollback()
        new_ctr(c, "k_x", a)
        with pytest.raises(psycopg.errors.RaiseException, match="does not exist"):
            member(c, "k_x", "EVIDENCE", "eB")  # other case's evidence
        c.rollback()
        new_ctr(c, "k1", a)
        member(c, "k1", "EVIDENCE", "e1")
        member(c, "k1", "CLAIM", "c1")
        c.commit()
        cur = c.execute("SELECT status, actor_kind FROM evidence.contradiction_current").fetchone()
        assert cur == ("OPEN", "SYSTEM")  # opened automatically
        with pytest.raises(psycopg.errors.CheckViolation):  # an agent may not move it past OPEN
            status(c, "k1", "EXPLAINED", "AGENT")
        c.rollback()
        status(c, "k1", "EXPLAINED", "HUMAN")
        status(c, "k1", "RESOLVED_BY_HUMAN", "HUMAN")
        c.commit()
        for st in ("OPEN", "EXPLAINED", "DISMISSED"):  # terminal
            with pytest.raises(psycopg.errors.RaiseException, match="illegal"):
                status(c, "k1", st, "HUMAN")
            c.rollback()
        final = c.execute("SELECT status FROM evidence.contradiction_current").fetchone()
        assert final == ("RESOLVED_BY_HUMAN",)
