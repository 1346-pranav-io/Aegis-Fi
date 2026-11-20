"""Throwaway database per test: source tables are insert-only, so tests that commit need their own
database, dropped afterwards. Schema comes from the real checksummed migration runner, and tests
log in DIRECTLY as the application roles (no superuser SET ROLE shortcuts).

Role passwords are cluster-wide; tests set them to the same dev-only values scripts/migrate.py uses.
"""

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from aegis.store.migrate import apply_migrations, set_role_passwords

MIGRATIONS = Path(__file__).parents[2] / "migrations"
INGEST_PW = "dev-only-ingest"  # local test value, matches scripts/migrate.py default
APP_PW = "dev-only-app"
APPROVER_PW = "dev-only-approver"
REVIEWER_PW = "dev-only-reviewer"


def _migrations_dir() -> Iterator[Path]:
    """The real migrations directory. With AEGIS_TEST_PROVISIONAL_APPROVALS=1, a temporary copy in
    which every migration that has no entry in APPROVALS.json gets a provisional one. This lets a
    stream test migrations that are still awaiting the owner. Nothing is written to the repo, and
    once the owner has approved a file its real entry is used unchanged."""
    if os.environ.get("AEGIS_TEST_PROVISIONAL_APPROVALS") != "1":
        yield MIGRATIONS
        return
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for f in MIGRATIONS.glob("*.sql"):
            shutil.copy(f, d / f.name)
        approvals = json.loads((MIGRATIONS / "APPROVALS.json").read_text(encoding="utf-8"))
        for f in sorted(d.glob("*.sql")):
            if f.name not in approvals:
                digest = hashlib.sha256(f.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                approvals[f.name] = {
                    "status": "approved",
                    "sha256": digest,
                    "decision": "TEST ONLY",
                }
        (d / "APPROVALS.json").write_text(json.dumps(approvals), encoding="utf-8")
        yield d


def _with_user(url: str, user: str, password: str) -> str:
    host_and_db = url.split("@", 1)[1]
    return f"postgresql://{user}:{password}@{host_and_db}"


@pytest.fixture
def scratch_db_url() -> Iterator[str]:
    """Admin URL of a fresh, fully migrated database."""
    base = os.environ.get("AEGIS_TEST_DATABASE_URL")
    if not base:
        pytest.skip("AEGIS_TEST_DATABASE_URL not set")
    host = base.split("@", 1)[1].split("/", 1)[0].rsplit(":", 1)[0]
    if host not in {"127.0.0.1", "localhost", "[::1]"}:
        pytest.skip("tests set known role passwords cluster-wide; refusing non-local database")
    name = f"aegis_test_{uuid.uuid4().hex[:8]}"
    admin_url = base.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = base.rsplit("/", 1)[0] + f"/{name}"
    try:
        with psycopg.connect(url) as c, contextlib.ExitStack() as stack:
            mdir = stack.enter_context(contextlib.contextmanager(_migrations_dir)())
            apply_migrations(c, mdir)
            set_role_passwords(c, INGEST_PW, APP_PW, APPROVER_PW, REVIEWER_PW)
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def ingest_url(scratch_db_url: str) -> str:
    return _with_user(scratch_db_url, "aegis_ingest", INGEST_PW)


@pytest.fixture
def app_url(scratch_db_url: str) -> str:
    return _with_user(scratch_db_url, "aegis_app", APP_PW)


@pytest.fixture
def approver_url(scratch_db_url: str) -> str:
    return _with_user(scratch_db_url, "aegis_approver", APPROVER_PW)


@pytest.fixture
def reviewer_url(scratch_db_url: str) -> str:
    return _with_user(scratch_db_url, "aegis_reviewer", REVIEWER_PW)
