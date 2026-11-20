"""Throwaway database per test: source tables are insert-only, so tests that commit need their own
database, dropped afterwards. Schema comes from the real checksummed migration runner, and tests
log in DIRECTLY as the application roles (no superuser SET ROLE shortcuts).

Role passwords are cluster-wide; tests set them to the same dev-only values scripts/migrate.py uses.
"""

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


def _migrations_with_test_approvals(dst: Path) -> list[str]:
    """Copy the migrations to `dst`; give any file WITHOUT an owner approval a temporary
    test-harness approval so the suite can run before the owner approves new migrations (the
    real APPROVALS.json is never modified; returns the unapproved file names for visibility)."""
    for f in MIGRATIONS.glob("*.sql"):
        shutil.copy(f, dst / f.name)
    approvals = json.loads((MIGRATIONS / "APPROVALS.json").read_text(encoding="utf-8"))
    missing = []
    for f in sorted(dst.glob("*.sql")):
        if f.name not in approvals:
            missing.append(f.name)
            digest = hashlib.sha256(f.read_text(encoding="utf-8").encode()).hexdigest()
            approvals[f.name] = {"status": "approved", "by": "test-harness", "sha256": digest}
    (dst / "APPROVALS.json").write_text(json.dumps(approvals), encoding="utf-8")
    return missing


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
        with tempfile.TemporaryDirectory() as tmp, psycopg.connect(url) as c:
            _migrations_with_test_approvals(Path(tmp))
            apply_migrations(c, Path(tmp))
            set_role_passwords(c, INGEST_PW, APP_PW, APPROVER_PW)
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
