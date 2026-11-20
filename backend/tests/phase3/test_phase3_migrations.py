"""Migrations 0010-0011: insert-only tables, run status machine, report versioning, call records.
Also proves the approval gate still refuses the new migrations without an owner approval entry."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from aegis.common.errors import IntegrityError
from aegis.llm.records import CallRecord, PostgresCallRecorder
from aegis.llm.types import CallStatus
from aegis.services import cases as svc
from aegis.store.migrate import apply_migrations

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).parents[2] / "migrations"


def _case(c: psycopg.Connection) -> str:
    r = svc.create_alerts(
        c, [svc.ScoredItem("t1", "A", 0.5, ("X",))], signal_type="S",
        as_of=datetime(2022, 9, 10, tzinfo=UTC), model_artefact_id="m",
    )  # fmt: skip
    c.commit()
    return r[0].case_id


def _run(c: psycopg.Connection, case: str, run_id: str = "run_1") -> None:
    c.execute(
        "INSERT INTO runtime.investigation_run (run_id, case_id, as_of, policy_name, "
        "policy_version, budget) VALUES (%s,%s,now(),'fixed_pipeline','1','{}')",
        (run_id, case),
    )
    c.execute(
        "INSERT INTO runtime.run_status_event (run_id, status) VALUES (%s,'QUEUED')", (run_id,)
    )
    c.commit()


def test_new_migrations_need_owner_approval(tmp_path: Path) -> None:
    """The real gate (no overlay): 0010/0011 must be refused until APPROVALS.json lists them."""
    approvals = json.loads((MIGRATIONS / "APPROVALS.json").read_text())
    pending = [f.name for f in MIGRATIONS.glob("001[0-9]_*.sql") if f.name not in approvals]
    if not pending:
        pytest.skip("owner has approved the Phase 3 migrations")
    for f in MIGRATIONS.glob("*.sql"):
        shutil.copy(f, tmp_path / f.name)
    shutil.copy(MIGRATIONS / "APPROVALS.json", tmp_path / "APPROVALS.json")
    # unit-level check of the gate without a database: the checker rejects the first pending file
    from aegis.store.migrate import _check_approved, _load_approvals, _sha

    text = (tmp_path / pending[0]).read_text(encoding="utf-8")
    with pytest.raises(IntegrityError, match="no approval entry"):
        _check_approved(pending[0], text, _sha(text), _load_approvals(tmp_path))
    assert apply_migrations  # runner imported (full application is covered by the other tests)


def test_run_status_machine_and_insert_only(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case = _case(c)
        _run(c, case)
        for bad in ("COMPLETE", "QUEUED"):  # cannot skip RUNNING / go back
            with pytest.raises(psycopg.errors.RaiseException, match="illegal run status"):
                c.execute(
                    "INSERT INTO runtime.run_status_event (run_id,status) VALUES ('run_1',%s)",
                    (bad,),
                )
            c.rollback()
        for ok in ("RUNNING", "RUNNING", "PARTIAL"):
            c.execute(
                "INSERT INTO runtime.run_status_event (run_id,status) VALUES ('run_1',%s)", (ok,)
            )
        c.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="illegal run status"):
            c.execute(
                "INSERT INTO runtime.run_status_event (run_id,status) VALUES ('run_1','RUNNING')"
            )
        c.rollback()
        assert c.execute("SELECT status FROM runtime.run_current").fetchone() == ("PARTIAL",)
        for stmt in (
            "UPDATE runtime.investigation_run SET policy_name='x'",
            "DELETE FROM runtime.run_status_event",
            "TRUNCATE runtime.agent_step",
        ):
            with pytest.raises(psycopg.Error):
                c.execute(stmt)
            c.rollback()


def test_agent_step_unique_per_task_and_reports_are_versioned(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        case = _case(c)
        _run(c, case)
        step = (
            "INSERT INTO runtime.agent_step (step_id, run_id, task_id, seq, agent, task_type, "
            "capability_version, status, input_hash, output, tool_calls, llm_call_ids, tokens_in, "
            "tokens_out, cost_usd, duration_ms) VALUES (%s,'run_1',%s,0,'GRAPH','x','1','OK','h',"
            "'{}','[]','[]',0,0,0,1)"
        )
        c.execute(step, ("step_1", "task_1"))
        c.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(step, ("step_2", "task_1"))
        c.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):  # FAILED needs an error
            c.execute(step.replace("'OK'", "'FAILED'"), ("step_3", "task_3"))
        c.rollback()

        rep = (
            "INSERT INTO runtime.report (report_id, case_id, run_id, version, body, body_hash, "
            "generator) VALUES (%s,%s,'run_1',%s,'{}','h','{}')"
        )
        with pytest.raises(psycopg.errors.RaiseException, match="must be 1"):
            c.execute(rep, ("rep_1", case, 2))
        c.rollback()
        c.execute(rep, ("rep_1", case, 1))
        c.execute(rep, ("rep_2", case, 2))
        c.commit()
        with pytest.raises(psycopg.Error):
            c.execute("UPDATE runtime.report SET body='{\"x\":1}'")
        c.rollback()


def test_call_record_is_insert_only(app_url: str) -> None:
    rec = CallRecord(
        call_id="call_1", run_id=None, step_id=None, agent="INVESTIGATION", role="investigation",
        prompt_id="p", prompt_version="1", prompt_hash="h", model_id="stub",
        provider="scripted-stub",
        mode="RECORD", request_key="k", input_hash="i", repair_round=0, attempt=1,
        status=CallStatus.OK, from_replay=False, tokens_in=1, tokens_out=2, cost_usd=0.0,
        duration_ms=3, response_text="NAME_ABC", error=None, created_at=datetime.now(UTC),
    )  # fmt: skip
    with psycopg.connect(app_url) as c:
        PostgresCallRecorder(c).record(rec)
        c.commit()
        assert c.execute("SELECT status, response_text FROM llm.call_record").fetchone() == (
            "OK",
            "NAME_ABC",
        )
        for stmt in ("UPDATE llm.call_record SET agent='x'", "DELETE FROM llm.call_record"):
            with pytest.raises(psycopg.Error):
                c.execute(stmt)
            c.rollback()
