"""P-2 (checkpoint tables in a migration) and the ER-review -> re-verification wiring (C6),
on Postgres with the real entity-link reader."""

from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from human_ops import reviewer_conn
from p3_llm import KEY
from p3_runtime import SUBJECT, Kit, big_budget
from test_runtime_pg import llm_config, seed

from aegis.domain.agents import Budget
from aegis.domain.entities import LinkMethod, LinkStatus
from aegis.domain.evidence import Citation, SupportRole
from aegis.entities import repo
from aegis.entities.evidence import er_link_evidence
from aegis.entities.pipeline import LinkProposal
from aegis.eval.phase3.fixture_llm import FixtureLLM
from aegis.llm.gateway import LlmGateway
from aegis.llm.providers import ScriptedProvider
from aegis.llm.pseudonym import Pseudonymiser
from aegis.llm.records import PostgresCallRecorder
from aegis.llm.replay import InMemoryRecordStore
from aegis.llm.types import GatewayMode
from aegis.runtime.checkpoint import postgres_checkpointer
from aegis.runtime.pg_wiring import build_postgres_services
from aegis.runtime.policies import FixedPipeline, Reverify
from aegis.runtime.runner import InvestigationRunner
from aegis.runtime.state import InvestigationState, RunStatus
from aegis.runtime.wiring import default_prompts, default_registry
from aegis.services.er_review import LinkChange, decide_and_reverify

pytestmark = pytest.mark.integration


def pg_runner(conn: psycopg.Connection, saver: Any, policy: Any) -> InvestigationRunner:
    gw = LlmGateway(
        config=llm_config(), prompts=default_prompts(), pseudonymiser=Pseudonymiser(KEY),
        mode=GatewayMode.RECORD, provider=ScriptedProvider(FixtureLLM()),
        store=InMemoryRecordStore(), recorder=PostgresCallRecorder(conn), sleep=lambda _s: None,
    )  # fmt: skip
    return InvestigationRunner(
        build_postgres_services(conn, llm=gw), default_registry(), policy, saver
    )


def test_checkpoint_tables_come_from_the_migration_and_cannot_be_deleted_by_the_app(
    scratch_db_url: str, app_url: str
) -> None:
    with psycopg.connect(scratch_db_url) as admin:
        tables = {
            r[0]
            for r in admin.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            ).fetchall()
        }
        assert not tables & {"checkpoints", "checkpoint_blobs", "checkpoint_writes"}
        v = admin.execute("SELECT max(v), count(*) FROM runtime.checkpoint_migrations").fetchone()
        assert v == (9, 10)  # the library's setup() would find nothing to do
    with psycopg.connect(app_url) as c:
        for stmt in (
            "DELETE FROM runtime.checkpoints",
            "TRUNCATE runtime.checkpoint_writes",
            "DELETE FROM runtime.checkpoint_blobs",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
            c.rollback()


def test_a_resumed_state_naming_unknown_records_is_refused() -> None:
    from aegis.common.errors import IntegrityError

    kit = Kit(FixedPipeline(), caps=[])
    st = InvestigationState(
        run_id="r", case_id="case_1", as_of=datetime(2022, 9, 10).astimezone(), subject_ref=SUBJECT,
        policy_name="fixed_pipeline", policy_version="1", budget=big_budget(),
        evidence_ids=["ev_" + "X" * 26],
    )  # fmt: skip

    class _G:
        def get_state(self, _c: Any) -> Any:
            return type("S", (), {"values": {"s": st.model_dump(mode="json")}})()

    kit.runner._graph = _G()  # type: ignore[assignment]
    with pytest.raises(IntegrityError, match="does not exist"):
        kit.runner._check_resume_state({})


def test_rejecting_a_link_queues_reverification_of_exactly_the_dependent_claims(
    scratch_db_url: str, ingest_url: str, app_url: str, tmp_path: Path
) -> None:
    case, as_of = seed(ingest_url, app_url, tmp_path)
    with (
        postgres_checkpointer(app_url) as saver,
        psycopg.connect(app_url, autocommit=True) as conn,
        psycopg.connect(app_url) as app_conn,
    ):
        runner = pg_runner(conn, saver, FixedPipeline())
        st = runner.run(runner.start(case_id=case, as_of=as_of, budget=big_budget()))
        assert st.status is RunStatus.COMPLETE
        store = runner._s.backend.evidence
        base = store.list_claims(case)[0]

        # an entity link the investigation relied on, recorded as evidence and cited by a claim
        m = repo.add_mention(app_conn, source_table="x", source_id="1", raw_name="Corporation #1")
        e = repo.create_entity(app_conn, canonical_name="Corporation #1", created_by="u")
        link = repo.record_proposal(
            app_conn, LinkProposal(m, e, LinkMethod.FUZZY_NAME, 0.9, LinkStatus.PROPOSED, {})
        )
        app_conn.commit()
        view = runner._s.er.latest_links([link])[0]
        with runner._s.backend.transaction():  # deferred provenance/citation rules fire at commit
            er_ev, _ = store.create_evidence(er_link_evidence(case, view, as_of=as_of))
            dependent = store.create_claim(
                base.draft.model_copy(
                    update={
                        "citations": (
                            *base.draft.citations,
                            Citation(
                                evidence_id=er_ev.evidence_id, support_role=SupportRole.CONTEXT
                            ),
                        ),
                        "text": base.draft.text + " (relies on the resolved entity)",
                    }
                )
            )
        before = {c.claim_id: len(store.verifications(c.claim_id)) for c in store.list_claims(case)}

        with reviewer_conn(app_conn) as rc:
            started = decide_and_reverify(
                change=LinkChange.REJECT, target_id=link, actor="analyst-1",
                reason="registry says these are different companies",
                reviewer_conn=rc, app_conn=app_conn,
                make_runner=lambda: pg_runner(conn, saver, Reverify()),
                store=store, as_of=as_of, budget=big_budget(),
            )  # fmt: skip
        assert set(started) == {case}
        rev_state = pg_runner(conn, saver, Reverify()).run(started[case])
        assert rev_state.status in (RunStatus.COMPLETE, RunStatus.PARTIAL)
        after = {c.claim_id: len(store.verifications(c.claim_id)) for c in store.list_claims(case)}
        assert after[dependent.claim_id] == before.get(dependent.claim_id, 0) + 1
        assert all(after[k] == v for k, v in before.items() if k != dependent.claim_id)
        assert store.get_claim(dependent.claim_id).draft == dependent.draft  # never edited

        # confirming a link queues nothing
        with reviewer_conn(app_conn) as rc2:
            again = decide_and_reverify(
                change=LinkChange.REOPEN, target_id=link, actor="analyst-2", reason="unsure",
                reviewer_conn=rc2, app_conn=app_conn,
                make_runner=lambda: pg_runner(conn, saver, Reverify()),
                store=store, as_of=as_of, budget=Budget(**big_budget().model_dump()),
            )  # fmt: skip
        assert set(again) == {case}  # reopening is also a change that needs a fresh look


def test_postgres_name_source_returns_entity_and_account_names(
    app_url: str, ingest_url: str, tmp_path: Path
) -> None:
    from aegis.runtime.pg_wiring import postgres_name_source

    seed(ingest_url, app_url, tmp_path)  # ingests accounts A, B, C, D
    with psycopg.connect(app_url) as c:
        repo.add_mention(c, source_table="x", source_id="1", raw_name="Corporation #33520")
        repo.create_entity(c, canonical_name="Sole Proprietorship #5", created_by="u")
        c.commit()
        names = postgres_name_source(c)()
    assert "Corporation #33520" in names["ORG"] and "Sole Proprietorship #5" in names["ORG"]
    assert set(names) == {"ACCT", "ORG", "ID"}
    # one- and two-character account refs ("A", "B") are skipped: they would mask ordinary words
    assert all(len(x) >= 3 for kind in names.values() for x in kind)
