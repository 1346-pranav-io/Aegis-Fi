"""T-INV-01: source/audit tables are insert-only. Needs Postgres (deploy/docker-compose.yml).

Run: AEGIS_TEST_DATABASE_URL=postgresql://aegis_migrator:dev-only-change-me@127.0.0.1:5432/aegis \
     uv run pytest -m integration
Rows are inserted inside a transaction that is always rolled back, so the DB stays clean.
"""

import os
from collections.abc import Iterator

import psycopg
import pytest

pytestmark = pytest.mark.integration

URL = os.environ.get("AEGIS_TEST_DATABASE_URL")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    if not URL:
        pytest.skip("AEGIS_TEST_DATABASE_URL not set")
    c = psycopg.connect(URL, autocommit=False)
    try:
        c.execute(
            "INSERT INTO source.ingestion_batch VALUES "
            "('b1','ds','v1','a','1','{}',NULL,NULL,'RUNNING',now(),NULL)"
        )
        c.execute(
            "INSERT INTO source.account (account_id,batch_id,source_account_ref,content_hash)"
            " VALUES ('acct_1','b1','A','h1')"
        )
        c.execute(
            "INSERT INTO source.account (account_id,batch_id,source_account_ref,content_hash)"
            " VALUES ('acct_2','b1','B','h2')"
        )
        c.execute(
            "INSERT INTO source.transaction (txn_id,batch_id,source_txn_ref,event_time,"
            "origin_account_id,destination_account_id,amount,currency,content_hash) "
            "VALUES ('txn_1','b1','T',now(),'acct_1','acct_2',10,'USD','h3')"
        )
        c.execute(
            "INSERT INTO audit.event (actor_type,actor_id,action,object_type,object_id)"
            " VALUES ('SYSTEM','t','x','y','z')"
        )
        yield c
    finally:
        c.rollback()
        c.close()


PROTECTED = ["source.account", "source.transaction", "audit.event"]


@pytest.mark.parametrize("role", [None, "aegis_ingest", "aegis_app"])
@pytest.mark.parametrize("table", PROTECTED)
@pytest.mark.parametrize(
    "stmt", ["UPDATE {t} SET content_hash = 'x'", "DELETE FROM {t}", "TRUNCATE {t}"]
)
def test_protected_tables_reject_mutation(
    conn: psycopg.Connection, role: str | None, table: str, stmt: str
) -> None:
    if table == "audit.event":
        stmt = stmt.replace("content_hash = 'x'", "action = 'x'")
    if role:
        conn.execute(f"SET LOCAL ROLE {role}")
    with pytest.raises(psycopg.Error):
        conn.execute(stmt.format(t=table))


def test_batch_identity_immutable_but_status_updatable(conn: psycopg.Connection) -> None:
    conn.execute("SET LOCAL ROLE aegis_ingest")
    conn.execute(
        "UPDATE source.ingestion_batch SET status='COMPLETE', completed_at=now() "
        "WHERE batch_id='b1'"
    )
    with pytest.raises(psycopg.Error):
        conn.execute("UPDATE source.ingestion_batch SET dataset_name='other' WHERE batch_id='b1'")


def test_batch_cannot_be_deleted(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.Error):
        conn.execute("DELETE FROM source.ingestion_batch WHERE batch_id='b1'")


def test_app_role_cannot_insert_source(conn: psycopg.Connection) -> None:
    conn.execute("SET LOCAL ROLE aegis_app")
    with pytest.raises(psycopg.Error):
        conn.execute(
            "INSERT INTO source.account (account_id,batch_id,source_account_ref,content_hash)"
            " VALUES ('acct_9','b1','Z','h9')"
        )
