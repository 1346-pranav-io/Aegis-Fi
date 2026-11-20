"""Security review regressions (S-1, S-3, S-5, S-8). Roles log in directly; no SET ROLE tricks."""

from pathlib import Path

import psycopg
import pytest

from aegis.common.errors import IntegrityError
from aegis.store.migrate import apply_migrations

pytestmark = pytest.mark.integration

BATCH = (
    "INSERT INTO source.ingestion_batch (batch_id,dataset_name,dataset_version,adapter,"
    "adapter_version,file_hashes,status,started_at) "
    "VALUES ('b1','ds','v1','a','1','{}','RUNNING',now())"
)


def _fails(conn: psycopg.Connection, stmt: str) -> None:
    with pytest.raises(psycopg.Error):
        conn.execute(stmt)
    conn.rollback()


@pytest.mark.parametrize("who", ["ingest", "app"])
def test_app_roles_are_not_superusers_and_cannot_disable_guards(
    who: str, ingest_url: str, app_url: str
) -> None:
    url = ingest_url if who == "ingest" else app_url
    with psycopg.connect(url) as c:
        row = c.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert row == (False, False, False)
        _fails(c, "ALTER TABLE source.transaction DISABLE TRIGGER ALL")
        _fails(c, "SET session_replication_role = replica")
        _fails(c, "ALTER TABLE source.transaction DROP CONSTRAINT transaction_pkey")
        _fails(c, "DROP TRIGGER no_mutation ON source.transaction")
        _fails(c, "CREATE TABLE source.sneaky (x int)")


def test_batch_state_machine_and_audit(ingest_url: str, scratch_db_url: str) -> None:
    with psycopg.connect(ingest_url) as c:
        c.execute(BATCH)
        c.commit()
        c.execute(
            "UPDATE source.ingestion_batch SET status='COMPLETE', record_count=3 "
            "WHERE batch_id='b1'"
        )
        c.commit()
        # terminal: cannot regress, cannot rewrite counts, cannot be deleted/truncated
        _fails(c, "UPDATE source.ingestion_batch SET status='RUNNING' WHERE batch_id='b1'")
        _fails(c, "UPDATE source.ingestion_batch SET status='FAILED' WHERE batch_id='b1'")
        _fails(c, "UPDATE source.ingestion_batch SET record_count=999 WHERE batch_id='b1'")
        _fails(c, "DELETE FROM source.ingestion_batch")
        _fails(c, "TRUNCATE source.ingestion_batch")
        # ingest role cannot read the audit log (least privilege)
        _fails(c, "SELECT * FROM audit.event")
    with psycopg.connect(scratch_db_url) as admin:  # admin reads the audit trail
        rows = admin.execute(
            "SELECT actor_id, action, object_id FROM audit.event "
            "WHERE object_type='ingestion_batch'"
        ).fetchall()
    assert rows == [("aegis_ingest", "batch.complete", "b1")]


def test_running_batch_cannot_skip_to_arbitrary_status(ingest_url: str) -> None:
    with psycopg.connect(ingest_url) as c:
        c.execute(BATCH)
        c.commit()
        _fails(c, "UPDATE source.ingestion_batch SET status='RUNNING' WHERE batch_id='b1'")
        _fails(
            c,
            "UPDATE source.ingestion_batch SET dataset_name='x', status='COMPLETE' "
            "WHERE batch_id='b1'",
        )


def test_app_role_cannot_write_source_or_batches(app_url: str) -> None:
    with psycopg.connect(app_url) as c:
        _fails(c, BATCH)
        _fails(c, "DELETE FROM source.account")


def test_migration_tracking_is_protected_and_detects_edits(
    scratch_db_url: str, tmp_path: Path
) -> None:
    with psycopg.connect(scratch_db_url) as admin:
        names = [
            r[0]
            for r in admin.execute(
                "SELECT filename FROM public.schema_migrations ORDER BY filename"
            ).fetchall()
        ]
        assert names[0] == "0001_source_schema.sql" and len(names) >= 4
        _fails(admin, "UPDATE public.schema_migrations SET sha256='x'")  # even for the owner
        _fails(admin, "DELETE FROM public.schema_migrations")
        _fails(admin, "TRUNCATE public.schema_migrations")
    # edited-after-apply detection
    mdir = tmp_path / "m"
    mdir.mkdir()
    (mdir / "0001_a.sql").write_text("CREATE TABLE public.t_a (x int);", encoding="utf-8")
    with psycopg.connect(scratch_db_url) as admin:
        assert apply_migrations(admin, mdir) == ["0001_a.sql"]
        (mdir / "0001_a.sql").write_text(
            "CREATE TABLE public.t_a (x int, y int);", encoding="utf-8"
        )
        with pytest.raises(IntegrityError):
            apply_migrations(admin, mdir)


def test_unapproved_migration_touching_source_is_refused(
    scratch_db_url: str, tmp_path: Path
) -> None:
    mdir = tmp_path / "m2"
    mdir.mkdir()
    (mdir / "0001_bad.sql").write_text(
        "ALTER TABLE source.transaction ADD COLUMN sneaky text;", encoding="utf-8"
    )
    with psycopg.connect(scratch_db_url) as admin, pytest.raises(IntegrityError):
        apply_migrations(admin, mdir)


def _blocked_by_trigger(conn: psycopg.Connection, stmt: str, text: str) -> None:
    """As table owner (no privilege failures possible): the TRIGGER must raise (SQLSTATE P0001)."""
    with pytest.raises(psycopg.errors.RaiseException) as exc:
        conn.execute(stmt)
    conn.rollback()
    assert text in str(exc.value)


def test_triggers_themselves_block_even_the_owner(scratch_db_url: str) -> None:
    with psycopg.connect(scratch_db_url) as admin:
        admin.execute(BATCH)
        admin.commit()
        _blocked_by_trigger(
            admin,
            "UPDATE source.ingestion_batch SET dataset_name='x', status='COMPLETE' "
            "WHERE batch_id='b1'",
            "identity fields are immutable",
        )
        _blocked_by_trigger(
            admin,
            "UPDATE source.ingestion_batch SET status='RUNNING' WHERE batch_id='b1'",
            "may only move",
        )
        _blocked_by_trigger(admin, "DELETE FROM source.ingestion_batch", "cannot be deleted")
        # TRUNCATE of ingestion_batch is rejected earlier by Postgres itself (FK-referenced);
        # the truncate trigger is proven on a table with no inbound FK instead:
        _blocked_by_trigger(admin, "TRUNCATE audit.event", "insert-only")
        admin.execute("UPDATE source.ingestion_batch SET status='COMPLETE' WHERE batch_id='b1'")
        admin.commit()
        _blocked_by_trigger(
            admin,
            "UPDATE source.ingestion_batch SET record_count=999 WHERE batch_id='b1'",
            "terminal",
        )
        _blocked_by_trigger(admin, "UPDATE audit.event SET action='x'", "insert-only")


def test_audit_rows_cannot_be_forged_by_application_roles(
    ingest_url: str, app_url: str, scratch_db_url: str
) -> None:
    forged = (
        "INSERT INTO audit.event (actor_type, actor_id, action, object_type, object_id) "
        "VALUES ('USER', 'approver-1', 'approval.granted', 'case', 'c1')"
    )
    for url in (ingest_url, app_url):
        with psycopg.connect(url) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(forged)
            c.rollback()
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT nextval('audit.event_event_id_seq')")  # cannot burn ids
            c.rollback()
            # the sanctioned path works, and the actor is the login role, not caller-chosen
            c.execute("SELECT audit.record_app('app.test.event', 'thing', 't1', '{}'::jsonb)")
            c.commit()
    with psycopg.connect(scratch_db_url) as admin:
        actors = sorted(
            r[0]
            for r in admin.execute(
                "SELECT actor_id FROM audit.event WHERE action='app.test.event'"
            ).fetchall()
        )
    assert actors == ["aegis_app", "aegis_ingest"]


def _protected_migration(tmp_path: Path, name: str, body: str) -> Path:
    mdir = tmp_path / name
    mdir.mkdir()
    (mdir / "0001_x.sql").write_text(body, encoding="utf-8")
    return mdir


def test_approval_gate_is_bound_to_file_hash_and_ignores_headers(
    scratch_db_url: str, tmp_path: Path
) -> None:
    import hashlib
    import json

    body = "-- OWNER-APPROVED: yes\nGRANT SELECT ON source.account TO aegis_app;\n"
    mdir = _protected_migration(tmp_path, "g", body)
    with psycopg.connect(scratch_db_url) as admin:
        with pytest.raises(IntegrityError):  # header comment alone is not an approval
            apply_migrations(admin, mdir)
        digest = hashlib.sha256(body.encode()).hexdigest()
        (mdir / "APPROVALS.json").write_text(
            json.dumps({"0001_x.sql": {"status": "approved", "sha256": "0" * 64}}), encoding="utf-8"
        )
        with pytest.raises(IntegrityError):  # approval for a different hash is refused
            apply_migrations(admin, mdir)
        (mdir / "APPROVALS.json").write_text(
            json.dumps({"0001_x.sql": {"status": "approved", "sha256": digest}}), encoding="utf-8"
        )
        assert apply_migrations(admin, mdir) == ["0001_x.sql"]  # exact approval works


@pytest.mark.parametrize(
    "body",
    [
        "GRANT DELETE ON source.account TO aegis_app;",
        "CREATE OR REPLACE FUNCTION audit.reject_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;",
        "ALTER ROLE aegis_app SUPERUSER;",
        'ALTER TABLE "source"."transaction" ADD COLUMN x int;',
        "DO $$ BEGIN EXECUTE 'drop trigger no_mutation on source.account'; END $$;",
    ],
)
def test_constructs_the_old_regex_missed_now_require_approval(
    scratch_db_url: str, tmp_path: Path, body: str
) -> None:
    mdir = _protected_migration(tmp_path, "p", body)
    with psycopg.connect(scratch_db_url) as admin, pytest.raises(IntegrityError):
        apply_migrations(admin, mdir)
