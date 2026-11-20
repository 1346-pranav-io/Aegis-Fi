"""Entity resolution in the database (migration 0020): FR-ER-01..04, T-INV-17, D-09d."""

import json
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from aegis.common.errors import ConflictError
from aegis.domain.entities import ERLinkReader, LinkMethod, LinkStatus
from aegis.entities import repo
from aegis.entities.pipeline import LinkProposal
from aegis.services import cases as svc

pytestmark = pytest.mark.integration
NOW = datetime(2022, 9, 10, tzinfo=UTC)
AUDIT_ER = "SELECT count(*) FROM audit.event WHERE action LIKE %s"


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


_counter = 0


def mk_claim(
    c: psycopg.Connection,
    case_id: str,
    deps: list[str],
    ev_content: dict[str, Any] | None = None,
) -> str:
    global _counter
    _counter += 1
    eid = f"ev_er{_counter}"
    c.execute(
        "INSERT INTO evidence.evidence_item (evidence_id, case_id, evidence_type, content, "
        "summary_text, producer, as_of, content_hash, completeness, schema_version) "
        "VALUES (%s,%s,%s,%s,'s','{\"kind\":\"TOOL\"}',now(),%s,'FULL',1)",
        (
            eid,
            case_id,
            "ER_LINK_EVIDENCE" if ev_content else "TXN_AGGREGATE",
            json.dumps(ev_content or {}),
            "h-" + eid,
        ),
    )
    c.execute("INSERT INTO evidence.evidence_input VALUES (%s,'SOURCE','t','h')", (eid,))
    cid = f"clm_er{_counter}"
    c.execute(
        "INSERT INTO evidence.claim (claim_id, case_id, author_kind, author_name, author_version, "
        "structure, text, claim_kind, self_reported_confidence, depends_on_er_links) "
        "VALUES (%s,%s,'AGENT','inv','1','{}','t','AGGREGATE','NONE',%s)",
        (cid, case_id, deps),
    )
    c.execute("INSERT INTO evidence.claim_evidence_link VALUES (%s,%s,'SUPPORTS')", (cid, eid))
    c.commit()
    return cid


def setup_pair(c: psycopg.Connection) -> tuple[str, str, str]:
    """A mention plus two entities (the first carries the mention's account number)."""
    m = repo.add_mention(
        c,
        source_table="acct",
        source_id="1",
        raw_name="Corporation #33520",
        identifiers={"account_number": "ACC1"},
    )
    e1 = repo.create_entity(
        c,
        canonical_name="Corporation #33520",
        created_by="u1",
        identifiers={"account_number": "ACC1"},
    )
    e2 = repo.create_entity(c, canonical_name="Corporation #33521", created_by="u1")
    c.commit()
    return m, e1, e2


def proposal(
    m: str,
    e: str,
    method: LinkMethod = LinkMethod.FUZZY_NAME,
    status: LinkStatus = LinkStatus.PROPOSED,
    score: float = 0.9,
    features: dict[str, object] | None = None,
) -> LinkProposal:
    return LinkProposal(m, e, method, score, status, features or {})


def exact(
    m: str, e: str, score: float = 1.0, id_type: str | None = "account_number"
) -> LinkProposal:
    feats: dict[str, object] = {} if id_type is None else {"identifier_type": id_type}
    return proposal(m, e, LinkMethod.EXACT_ID, LinkStatus.CONFIRMED, score, feats)


def approve(approver_url: str, request_id: str, approve_it: bool = True) -> str:
    with psycopg.connect(approver_url) as ac:
        a = svc.decide_approval(ac, request_id, approve_it, "approver1", "reviewed")
        ac.commit()
        return a


def test_mention_is_idempotent_and_normalised(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        a = repo.add_mention(c, source_table="acct", source_id="1", raw_name="Corp. No. 5")
        b = repo.add_mention(c, source_table="acct", source_id="1", raw_name="Corp. No. 5")
        c.commit()
        assert a == b
        row = c.execute("SELECT normalised_name FROM derived.entity_mention").fetchone()
        assert row == ("corporation 5",)


def test_exact_id_may_auto_confirm_only_when_identifiers_are_equal(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        m, e1, e2 = setup_pair(c)
        good = repo.record_proposal(c, exact(m, e1))
        c.commit()
        assert repo.PgERLinkReader(c).unconfirmed_dependencies([good]) == []
        for bad in (
            exact(m, e2),  # that entity does not carry the identifier
            exact(m, e1, id_type="colour"),  # type is not on the allow-list
            exact(m, e1, id_type=None),  # no identifier stated
            exact(m, e1, score=0.9),  # exact links have score 1
        ):
            with pytest.raises(ConflictError):
                repo.record_proposal(c, bad)
            c.rollback()


def test_fuzzy_link_cannot_be_auto_confirmed(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        m, e1, _ = setup_pair(c)
        for method in (LinkMethod.FUZZY_NAME, LinkMethod.ATTRIBUTE_MATCH, LinkMethod.MANUAL):
            p = proposal(m, e1, method, LinkStatus.CONFIRMED, 0.99)
            with pytest.raises(ConflictError, match="EXACT_ID"):
                repo.record_proposal(c, p)  # SYSTEM actor
            c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):  # nor through the raw table
            c.execute(
                "INSERT INTO derived.er_link (link_id, mention_id, entity_id, method, match_score,"
                " status, actor_kind, decided_by, scorer_version) VALUES "
                "('x',%s,%s,'FUZZY_NAME',0.99,'CONFIRMED','HUMAN','me','v')",
                (m, e1),
            )
        c.rollback()


def test_history_is_appended_and_only_humans_decide(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        m, e1, _ = setup_pair(c)
        link = repo.record_proposal(c, proposal(m, e1, score=0.91))
        c.commit()
        for to in (LinkStatus.REJECTED, LinkStatus.CONFIRMED):  # a system actor may not
            with pytest.raises(psycopg.errors.RaiseException, match="human"):
                repo._insert_link_row(
                    c,
                    link,
                    m,
                    e1,
                    LinkMethod.FUZZY_NAME,
                    0.91,
                    {},
                    to,
                    "SYSTEM",
                    None,
                    "name-sim-1.0.0",
                    None,
                )
            c.rollback()
        repo.confirm_link(c, link, "analyst1", "looks right")
        repo.reject_link(c, link, "analyst2", "second look: wrong")
        repo.reopen_link(c, link, "analyst2", "unsure")
        c.commit()
        hist = [h[0] for h in repo.link_history(c, link)]
        assert hist == ["PROPOSED", "CONFIRMED", "REJECTED", "PROPOSED"]
        view = repo.PgERLinkReader(c).latest_links([link])[0]
        assert view.status is LinkStatus.PROPOSED and view.match_score == pytest.approx(0.91)
        with pytest.raises(psycopg.errors.RaiseException, match="only the status"):
            repo._insert_link_row(  # facts of a link never change
                c, link, m, e1, LinkMethod.FUZZY_NAME, 0.5, {}, LinkStatus.CONFIRMED, "HUMAN", "u",
                "v", None,
            )  # fmt: skip
        c.rollback()
        with pytest.raises(ConflictError, match="illegal"):  # PROPOSED -> PROPOSED
            repo.reopen_link(c, link, "analyst2", "again")
        c.rollback()
        repo.supersede_link(c, link, "re-scored")
        c.commit()
        with pytest.raises(ConflictError, match="illegal"):  # SUPERSEDED is terminal
            repo.confirm_link(c, link, "analyst", "x")
        c.rollback()
        n = c.execute(AUDIT_ER, ("er.link.%",)).fetchone()
        assert n == (5,)  # every row written was audited by the database


def test_tables_are_insert_only(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        m, e1, _ = setup_pair(c)
        repo.record_proposal(c, proposal(m, e1))
        c.commit()
        tables = ("entity_mention", "resolved_entity", "er_link", "entity_merge")
        for t in (*tables, "er_exact_identifier_type"):
            with pytest.raises(psycopg.Error):
                c.execute(f"DELETE FROM derived.{t}")  # noqa: S608 - fixed table names
            c.rollback()
        for stmt in (
            "UPDATE derived.er_link SET match_score = 0.1",
            "UPDATE derived.resolved_entity SET canonical_name = 'x'",
            "UPDATE derived.entity_mention SET raw_name = 'x'",
            "TRUNCATE derived.er_link",
        ):
            with pytest.raises(psycopg.Error):
                c.execute(stmt)
            c.rollback()
        with pytest.raises(psycopg.Error):  # the app login cannot extend the verified-id list
            c.execute("INSERT INTO derived.er_exact_identifier_type VALUES ('colour')")
        c.rollback()


def test_match_score_is_a_bounded_similarity_with_no_probability_column(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        m, e1, _ = setup_pair(c)
        for bad in (1.5, -0.1, float("nan")):
            with pytest.raises(psycopg.errors.CheckViolation):
                repo.record_proposal(c, proposal(m, e1, score=bad))
            c.rollback()
        rows = c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='derived' AND table_name IN ('er_link','er_link_current')"
        ).fetchall()
        assert not any("prob" in str(r[0]) for r in rows)


def test_merge_needs_a_stored_human_approval_and_unmerge_restores(
    app_url: str, approver_url: str
) -> None:
    with psycopg.connect(app_url) as c:
        _, e1, e2 = setup_pair(c)
        with pytest.raises(psycopg.errors.RaiseException, match="APPROVED ENTITY_MERGE"):  # none
            c.execute(
                "INSERT INTO derived.entity_merge (merge_id, from_entity_id, into_entity_id, "
                "action, actor, reason) VALUES ('erm_x',%s,%s,'MERGE','u','r')",
                (e2, e1),
            )
        c.rollback()
        req = repo.request_merge(c, e2, e1, "analyst1", "same company, typo in registry")
        c.commit()
        with pytest.raises(ConflictError, match="APPROVED ENTITY_MERGE"):  # nothing decided yet
            repo.merge_entities(c, e2, e1, "apv_missing", "analyst1", "r")
        c.rollback()
        denied = approve(approver_url, req, approve_it=False)
        with pytest.raises(ConflictError, match="APPROVED ENTITY_MERGE"):  # denied is not approval
            repo.merge_entities(c, e2, e1, denied, "analyst1", "r")
        c.rollback()
        other = repo.request_merge(c, e1, e2, "analyst1", "reverse pair")
        c.commit()
        wrong_pair = approve(approver_url, other)
        with pytest.raises(
            ConflictError, match="APPROVED ENTITY_MERGE"
        ):  # approval for another pair
            repo.merge_entities(c, e2, e1, wrong_pair, "analyst1", "r")
        c.rollback()
        req2 = repo.request_merge(c, e2, e1, "analyst1", "again")
        c.commit()
        ok = approve(approver_url, req2)
        merge_id = repo.merge_entities(c, e2, e1, ok, "analyst1", "approved by approver1")
        c.commit()
        assert repo.root_entity(c, e2) == e1 and repo.root_entity(c, e1) == e1
        assert repo.PgERLinkReader(c).unconfirmed_dependencies([merge_id]) == []
        with pytest.raises(psycopg.errors.RaiseException, match="already merged"):  # reuse
            c.execute(
                "INSERT INTO derived.entity_merge (merge_id, from_entity_id, into_entity_id, "
                "action, approval_id, actor, reason) VALUES ('erm_y',%s,%s,'MERGE',%s,'u','r')",
                (e2, e1, ok),
            )
        c.rollback()
        repo.unmerge(c, merge_id, "analyst1", "was wrong")  # reversible
        c.commit()
        assert repo.root_entity(c, e2) == e2
        assert repo.PgERLinkReader(c).unconfirmed_dependencies([merge_id]) == [merge_id]
        rows = c.execute(
            "SELECT action FROM derived.entity_merge WHERE merge_id=%s ORDER BY event_id",
            (merge_id,),
        ).fetchall()
        assert [r[0] for r in rows] == ["MERGE", "UNMERGE"]  # history kept, nothing overwritten
        with pytest.raises(ConflictError, match="not active"):
            repo.unmerge(c, merge_id, "analyst1", "twice")
        c.rollback()
        assert c.execute(AUDIT_ER, ("er.merge",)).fetchone() == (1,)
        assert c.execute(AUDIT_ER, ("er.unmerge",)).fetchone() == (1,)


def test_merge_chains_cannot_form_cycles(app_url: str, approver_url: str) -> None:
    with psycopg.connect(app_url) as c:
        _, a, b = setup_pair(c)
        c3 = repo.create_entity(c, canonical_name="Corporation #99", created_by="u")
        c.commit()

        def do_merge(frm: str, into: str) -> str:
            req = repo.request_merge(c, frm, into, "analyst1", "j")
            c.commit()
            return repo.merge_entities(c, frm, into, approve(approver_url, req), "analyst1", "r")

        do_merge(b, a)  # b -> a
        with pytest.raises(ConflictError, match="itself merged"):  # target b is not a root
            do_merge(c3, b)
        c.rollback()
        with pytest.raises(ConflictError, match="already merged"):
            do_merge(b, c3)
        c.rollback()
        do_merge(a, c3)  # chain b -> a -> c3
        c.commit()
        assert repo.root_entity(c, b) == c3


def test_dependency_flags_follow_rejected_links_and_unmerge(
    app_url: str, approver_url: str
) -> None:
    """T-INV-17: unmerge restores the prior state and dependents are re-flagged."""
    with psycopg.connect(app_url) as c:
        case_id = mk_case(c)
        m, e1, e2 = setup_pair(c)
        reader = repo.PgERLinkReader(c)
        proposed = repo.record_proposal(c, proposal(m, e2, score=0.88))
        confirmed = repo.record_manual_link(c, m, e1, "analyst1", "checked registry")
        c.commit()
        deps = reader.unconfirmed_dependencies([proposed, confirmed])
        assert deps == [proposed]  # what Phase 3 stores in depends_on_er_links
        assert reader.unconfirmed_dependencies(["erl_nope"]) == ["erl_nope"]  # fails closed
        claim_on_proposed = mk_claim(c, case_id, deps)
        link_ev = {"link_id": confirmed, "mention_id": m, "entity_id": e1}
        claim_on_confirmed = mk_claim(c, case_id, [], link_ev)  # relied on a CONFIRMED link
        assert {f.claim_id for f in reader.flagged_claims(case_id)} == {claim_on_proposed}
        repo.confirm_link(c, proposed, "analyst1", "ok")
        c.commit()
        assert reader.flagged_claims(case_id) == []
        repo.reject_link(c, confirmed, "analyst2", "registry was wrong")
        c.commit()
        assert [f.claim_id for f in reader.flagged_claims(case_id)] == [claim_on_confirmed]
        repo.confirm_link(c, confirmed, "analyst2", "after all it was right")  # restores
        c.commit()
        assert reader.flagged_claims(case_id) == []
        req = repo.request_merge(c, e2, e1, "analyst1", "same")
        c.commit()
        merge_id = repo.merge_entities(c, e2, e1, approve(approver_url, req), "analyst1", "r")
        claim_on_merge = mk_claim(c, case_id, [], {**link_ev, "merge_ids": [merge_id]})
        assert reader.flagged_claims(case_id) == []
        repo.unmerge(c, merge_id, "analyst1", "split")
        c.commit()
        flagged = reader.flagged_claims(case_id)
        assert [(f.claim_id, f.unconfirmed) for f in flagged] == [(claim_on_merge, (merge_id,))]
        assert c.execute("SELECT count(*) FROM evidence.claim").fetchone() == (3,)  # untouched


def test_er_link_reader_satisfies_the_shared_protocol(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        reader: ERLinkReader = repo.PgERLinkReader(c)
        assert reader.latest_links([]) == [] and reader.unconfirmed_dependencies([]) == []
