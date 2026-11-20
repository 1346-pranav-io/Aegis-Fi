"""Phase 3 test support. Scratch databases here apply ALL migrations, including Phase 3 ones that
are not yet in APPROVALS.json, by writing a temporary approvals overlay (test harness only; the
repository's APPROVALS.json is never touched)."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from aegis.store.migrate import apply_migrations, set_role_passwords  # noqa: E402

MIGRATIONS = Path(__file__).parents[2] / "migrations"
INGEST_PW, APP_PW, APPROVER_PW = "dev-only-ingest", "dev-only-app", "dev-only-approver"


def with_test_approvals(src: Path, dst: Path) -> list[str]:
    """Copy migrations to dst; add a 'test-harness' approval for any protected-looking file that
    has none. Returns the names that were NOT owner-approved (for visibility)."""
    for f in src.glob("*.sql"):
        shutil.copy(f, dst / f.name)
    approvals = json.loads((src / "APPROVALS.json").read_text(encoding="utf-8"))
    missing = []
    for f in sorted(dst.glob("*.sql")):
        if f.name not in approvals:
            missing.append(f.name)
            approvals[f.name] = {
                "status": "approved",
                "decision": "TEST HARNESS ONLY (temp copy; not an owner approval)",
                "by": "test-harness",
                "on": "n/a",
                "sha256": hashlib.sha256(f.read_text(encoding="utf-8").encode()).hexdigest(),
            }
    (dst / "APPROVALS.json").write_text(json.dumps(approvals), encoding="utf-8")
    return missing


def _with_user(url: str, user: str, password: str) -> str:
    return f"postgresql://{user}:{password}@{url.split('@', 1)[1]}"


@pytest.fixture
def scratch_db_url() -> Iterator[str]:
    base = os.environ.get("AEGIS_TEST_DATABASE_URL")
    if not base:
        pytest.skip("AEGIS_TEST_DATABASE_URL not set")
    host = base.split("@", 1)[1].split("/", 1)[0].rsplit(":", 1)[0]
    if host not in {"127.0.0.1", "localhost", "[::1]"}:
        pytest.skip("refusing non-local database")
    name = f"aegis_test_{uuid.uuid4().hex[:8]}"
    admin_url = base.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = base.rsplit("/", 1)[0] + f"/{name}"
    try:
        with tempfile.TemporaryDirectory() as tmp, psycopg.connect(url) as c:
            with_test_approvals(MIGRATIONS, Path(tmp))
            apply_migrations(c, Path(tmp))
            set_role_passwords(c, INGEST_PW, APP_PW, APPROVER_PW)
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def app_url(scratch_db_url: str) -> str:
    return _with_user(scratch_db_url, "aegis_app", APP_PW)


@pytest.fixture
def ingest_url(scratch_db_url: str) -> str:
    return _with_user(scratch_db_url, "aegis_ingest", INGEST_PW)
