"""Regressions for the two HIGH findings of the Phase 3/4 security review (migration 0023):
human decisions about contradictions and entity links only through the human-review login."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from human_ops import as_reviewer, reviewer_conn

from aegis.analytics.contradictions import DetectionInput, StoredEvidence, detect_all, fingerprint
from aegis.common.errors import ConflictError
from aegis.domain.cases import ApprovalAction, CaseStatus, Disposition
from aegis.domain.entities import LinkMethod, LinkStatus
from aegis.domain.evidence import (
    ContradictionDraft,
    ContradictionMember,
    ContradictionStatus,
    ContradictionType,
    EvidenceDraft,
    EvidenceType,
    MemberType,
    Producer,
    ProducerKind,
    Severity,
)
from aegis.domain.evidence_content import numeric_fact, with_facts
from aegis.entities import repo
from aegis.entities.pipeline import LinkProposal
from aegis.evidence.drafts import query_input
from aegis.evidence.pg_store import PostgresEvidenceStore
from aegis.evidence.store import DictLeafHashes
from aegis.services import cases as svc
from aegis.services import contradictions as csvc

pytestmark = pytest.mark.integration
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


def two_conflicting(c: psycopg.Connection, case_id: str) -> tuple[ContradictionDraft, str, str]:
    store = PostgresEvidenceStore(c, DictLeafHashes())
    e1, _ = store.create_evidence(fact_evidence(case_id, "transaction_agent", "100.00"))
    e2, _ = store.create_evidence(fact_evidence(case_id, "kyc_document", "1100.00"))
    c.commit()
    drafts = detect_all(
        DetectionInput(
            evidence=(
                StoredEvidence(e1.evidence_id, e1.draft),
                StoredEvidence(e2.evidence_id, e2.draft),
            )
        ),
        case_id=case_id,
    )
    return (
        next(d for d in drafts if d.type is ContradictionType.AMOUNT_MISMATCH),
        e1.evidence_id,
        e2.evidence_id,
    )


# ------------------------------------------------------------------ H-1: contradictions
def test_app_login_cannot_dismiss_a_finding_to_pass_the_closure_gate(
    app_url: str, approver_url: str, reviewer_url: str
) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        case_id = mk_case(c)
        d, _, _ = two_conflicting(c, case_id)
        cid = PostgresEvidenceStore(c, DictLeafHashes()).create_contradiction(d).contradiction_id
        c.commit()
        svc.transition(c, case_id, CaseStatus.INVESTIGATING, "start")
        svc.transition(c, case_id, CaseStatus.PENDING_REVIEW, "done")
        c.commit()
        req = svc.request_approval(c, ApprovalAction.CASE_CLOSURE, case_id, "analyst1", "ok")
        c.commit()
        apv = svc.decide_approval(ap, req, True, "boss", "agreed")
        ap.commit()
        # the attack from the review: write the 'human' dismissal itself
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(
                "INSERT INTO evidence.contradiction_status_event (contradiction_id, status, "
                "actor_kind, actor) VALUES (%s,'DISMISSED','HUMAN','nobody')",
                (cid,),
            )
        c.rollback()
        with pytest.raises(ConflictError, match="open HIGH"):
            svc.close_case(c, case_id, Disposition.INCONCLUSIVE, apv, "x")
        c.rollback()
        as_reviewer(c, csvc.set_status, cid, ContradictionStatus.DISMISSED,
                    actor="analyst-3", actor_kind="HUMAN", note="duplicate of a known KYC gap")  # fmt: skip
        svc.close_case(c, case_id, Disposition.INCONCLUSIVE, apv, "closed after review")
        c.commit()


def test_status_trigger_refuses_logins_outside_the_review_role_even_with_insert_rights(
    scratch_db_url: str, ingest_url: str, app_url: str
) -> None:
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        d, _, _ = two_conflicting(c, case_id)
        cid = PostgresEvidenceStore(c, DictLeafHashes()).create_contradiction(d).contradiction_id
        c.commit()
    with psycopg.connect(scratch_db_url) as admin:  # widen grants as the owner
        admin.execute("GRANT USAGE ON SCHEMA evidence TO aegis_ingest")
        admin.execute("GRANT INSERT ON evidence.contradiction_status_event TO aegis_ingest")
        admin.execute("GRANT SELECT ON evidence.contradiction_status_event TO aegis_ingest")
        admin.execute(
            "GRANT USAGE ON SEQUENCE evidence.contradiction_status_event_event_id_seq "
            "TO aegis_ingest"
        )
        admin.commit()
    with psycopg.connect(ingest_url) as ing:
        with pytest.raises(psycopg.errors.RaiseException, match="human-review role"):
            ing.execute(
                "INSERT INTO evidence.contradiction_status_event (contradiction_id, status, "
                "actor_kind, actor) VALUES (%s,'DISMISSED','HUMAN','x')",
                (cid,),
            )


def test_a_pre_empting_fingerprint_cannot_hide_the_real_high_finding(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        real, _, _ = two_conflicting(c, case_id)
        # attacker: same fingerprint as the real finding, but LOW severity
        low = real.model_copy(update={"severity": Severity.LOW})
        store = PostgresEvidenceStore(c, DictLeafHashes())
        low_id = store.create_contradiction(low).contradiction_id
        c.commit()
        high_id = store.create_contradiction(real).contradiction_id
        c.commit()
        assert high_id != low_id  # the real HIGH record exists next to the LOW one
        rows = c.execute(
            "SELECT severity FROM evidence.contradiction_current WHERE case_id=%s ORDER BY severity",
            (case_id,),
        ).fetchall()
        assert rows == [("HIGH",), ("LOW",)]


def test_a_fingerprint_that_does_not_match_its_members_is_refused_at_commit(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        real, e1, e2 = two_conflicting(c, case_id)
        forged = fingerprint(real)[:-1] + ("0" if fingerprint(real)[-1] != "0" else "1")
        c.execute(
            "INSERT INTO evidence.contradiction (contradiction_id, case_id, type, severity, detector) "
            "VALUES ('ctr_forged',%s,'AMOUNT_MISMATCH','LOW',%s)",
            (case_id, json.dumps({"name": real.detector_name, "version": real.detector_version,
                                  "fingerprint": forged})),
        )  # fmt: skip
        for eid in (e1, e2):
            c.execute(
                "INSERT INTO evidence.contradiction_member VALUES ('ctr_forged','EVIDENCE',%s,'')",
                (eid,),
            )
        with pytest.raises(psycopg.errors.RaiseException, match="does not match"):
            c.commit()
        c.rollback()


def test_the_sql_fingerprint_check_agrees_with_the_python_fingerprint(app_url: str) -> None:
    """Both paths store the Python fingerprint, and the database recomputes the same value, for
    members that need JSON escaping and non-ASCII text too."""
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        store = PostgresEvidenceStore(c, DictLeafHashes())
        e1, _ = store.create_evidence(fact_evidence(case_id, 'säule "one"', "1.00"))
        e2, _ = store.create_evidence(fact_evidence(case_id, "back\\slash", "2.00"))
        c.commit()
        draft = ContradictionDraft(
            case_id=case_id,
            type=ContradictionType.AMOUNT_MISMATCH,
            severity=Severity.HIGH,
            detector_name='détecteur "x"',
            detector_version="1.0.0",
            members=(
                ContradictionMember(member_type=MemberType.EVIDENCE, member_id=e1.evidence_id),
                ContradictionMember(member_type=MemberType.EVIDENCE, member_id=e2.evidence_id),
            ),
        )
        store.create_contradiction(draft)
        c.commit()  # raises if the database computes a different fingerprint


# ------------------------------------------------------------------ H-2: entity resolution
def seed_account(admin_url: str, ref: str = "ACC1") -> str:
    with psycopg.connect(admin_url) as a:
        a.execute(
            "INSERT INTO source.ingestion_batch (batch_id, dataset_name, dataset_version, adapter, "
            "adapter_version, file_hashes, status, started_at) "
            "VALUES ('b_hr','d','v','a','1','{}','RUNNING',now()) ON CONFLICT DO NOTHING"
        )
        a.execute(
            "INSERT INTO source.account (account_id, batch_id, source_account_ref, content_hash) "
            "VALUES ('acct_hr','b_hr',%s,%s) ON CONFLICT DO NOTHING",
            (ref, "h-" + ref),
        )
        a.commit()
    return "acct_hr"


def test_app_login_cannot_confirm_reject_or_unmerge_as_a_human(
    app_url: str, approver_url: str, reviewer_url: str
) -> None:
    with psycopg.connect(app_url) as c:
        m = repo.add_mention(c, source_table="x", source_id="1", raw_name="Corporation #1")
        e = repo.create_entity(c, canonical_name="Corporation #1", created_by="u")
        link = repo.record_proposal(
            c, LinkProposal(m, e, LinkMethod.FUZZY_NAME, 0.9, LinkStatus.PROPOSED, {})
        )
        c.commit()
        for human_call in (
            lambda: repo.confirm_link(c, link, "me", "x"),
            lambda: repo.reject_link(c, link, "me", "x"),
            lambda: repo.record_manual_link(c, m, e, "me", "x"),
        ):
            with pytest.raises(
                (ConflictError, psycopg.errors.RaiseException), match="human-review"
            ):
                human_call()
            c.rollback()
        # the raw attack: a MANUAL link, CONFIRMED, by an asserted human
        with pytest.raises(psycopg.errors.RaiseException, match="human-review"):
            c.execute(
                "INSERT INTO derived.er_link (link_id, mention_id, entity_id, method, match_score, "
                "status, actor_kind, decided_by, scorer_version) VALUES "
                "('forged',%s,%s,'MANUAL',1,'CONFIRMED','HUMAN','boss','v')",
                (m, e),
            )
        c.rollback()
        with reviewer_conn(c) as rc:  # the reviewer login can
            repo.confirm_link(rc, link, "analyst1", "checked the registry")
            rc.commit()
        assert repo.PgERLinkReader(c).unconfirmed_dependencies([link]) == []


def test_unmerge_needs_the_review_role(app_url: str, approver_url: str, reviewer_url: str) -> None:
    with psycopg.connect(app_url) as c, psycopg.connect(approver_url) as ap:
        e1 = repo.create_entity(c, canonical_name="Corporation #1", created_by="u")
        e2 = repo.create_entity(c, canonical_name="Corporation #2", created_by="u")
        c.commit()
        req = repo.request_merge(c, e1, e2, "analyst1", "same company")
        c.commit()
        apv = svc.decide_approval(ap, req, True, "boss", "agreed")
        ap.commit()
        merge_id = repo.merge_entities(c, e1, e2, apv, "analyst1", "approved merge")
        c.commit()
        with pytest.raises((ConflictError, psycopg.errors.RaiseException), match="human-review"):
            repo.unmerge(c, merge_id, "me", "undo")
        c.rollback()
        as_reviewer(c, repo.unmerge, merge_id, "analyst2", "wrong merge")


def test_identifiers_must_be_verifiable_against_source_data(
    app_url: str, scratch_db_url: str
) -> None:
    account_id = seed_account(scratch_db_url, "ACC1")
    with psycopg.connect(app_url) as c:
        # a mention cannot state an identifier that source data does not carry
        with pytest.raises(psycopg.errors.RaiseException, match="cannot be verified"):
            repo.add_mention(
                c, source_table="source.account", source_id=account_id, raw_name="Corp",
                identifiers={"account_number": "NOT-THE-REAL-ONE"},
            )  # fmt: skip
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="cannot be verified"):
            repo.add_mention(  # an unknown source table can never verify anything
                c, source_table="whatever", source_id="1", raw_name="Corp",
                identifiers={"account_number": "ACC1"},
            )  # fmt: skip
        c.rollback()
        m = repo.add_mention(
            c, source_table="source.account", source_id=account_id, raw_name="Corp",
            identifiers={"account_number": "ACC1"},
        )  # fmt: skip
        # an entity cannot be minted with identifiers its founding mention does not carry
        with pytest.raises(psycopg.errors.RaiseException, match="founding mention"):
            repo.create_entity(
                c, canonical_name="Fake", created_by="u", identifiers={"account_number": "ACC1"}
            )
        c.rollback()
        m = repo.add_mention(
            c, source_table="source.account", source_id=account_id, raw_name="Corp",
            identifiers={"account_number": "ACC1"},
        )  # fmt: skip
        with pytest.raises(psycopg.errors.RaiseException, match="not carried"):
            repo.create_entity(
                c, canonical_name="Fake", created_by="u", identifiers={"account_number": "OTHER"},
                founding_mention_id=m,
            )  # fmt: skip
        c.rollback()
        m = repo.add_mention(
            c, source_table="source.account", source_id=account_id, raw_name="Corp",
            identifiers={"account_number": "ACC1"},
        )  # fmt: skip
        e = repo.create_entity(
            c, canonical_name="Real", created_by="u", identifiers={"account_number": "ACC1"},
            founding_mention_id=m,
        )  # fmt: skip
        link = repo.record_proposal(
            c,
            LinkProposal(m, e, LinkMethod.EXACT_ID, 1.0, LinkStatus.CONFIRMED,
                         {"identifier_type": "account_number"}),
        )  # fmt: skip
        c.commit()
        assert repo.PgERLinkReader(c).unconfirmed_dependencies([link]) == []


def test_the_app_cannot_undo_a_human_rejection_or_supersede_a_human_confirmed_link(
    app_url: str, scratch_db_url: str
) -> None:
    account_id = seed_account(scratch_db_url, "ACC9")
    with psycopg.connect(app_url) as c:
        m = repo.add_mention(
            c, source_table="source.account", source_id=account_id, raw_name="Corp",
            identifiers={"account_number": "ACC9"},
        )  # fmt: skip
        e = repo.create_entity(
            c, canonical_name="Corp", created_by="u", identifiers={"account_number": "ACC9"},
            founding_mention_id=m,
        )  # fmt: skip
        exact = LinkProposal(
            m, e, LinkMethod.EXACT_ID, 1.0, LinkStatus.CONFIRMED, {"identifier_type": "account_number"}
        )  # fmt: skip
        link = repo.record_proposal(c, exact)
        c.commit()
        as_reviewer(c, repo.reject_link, link, "analyst1", "registry says no")
        # the app (a SYSTEM actor) may not undo the human's rejection by re-confirming
        with pytest.raises(psycopg.errors.RaiseException, match="human rejected"):
            repo._insert_link_row(
                c, link, m, e, LinkMethod.EXACT_ID, 1.0, {"identifier_type": "account_number"},
                LinkStatus.CONFIRMED, "SYSTEM", None, "name-sim-1.0.0", None,
            )  # fmt: skip
        c.rollback()
        as_reviewer(c, repo.confirm_link, link, "analyst2", "after all, yes")
        # nor may it supersede a human-confirmed link
        with pytest.raises(
            psycopg.errors.RaiseException, match="confirmed can only be changed by a human"
        ):
            repo._insert_link_row(
                c, link, m, e, LinkMethod.EXACT_ID, 1.0, {"identifier_type": "account_number"},
                LinkStatus.SUPERSEDED, "SYSTEM", None, "name-sim-1.0.0", None,
            )  # fmt: skip
        c.rollback()
