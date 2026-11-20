"""Apply migrations and set application role passwords (local dev bootstrap).

Usage: uv run python scripts/migrate.py [--adopt]
Env:   AEGIS_ADMIN_URL         admin/owner connection (default: local compose, dev-only)
       AEGIS_INGEST_PASSWORD   password for role aegis_ingest (default: dev-only value)
       AEGIS_APP_PASSWORD      password for role aegis_app    (default: dev-only value)
--adopt records existing files as applied WITHOUT running them (for a DB that already has them).
"""

import os
import sys
from pathlib import Path

import psycopg

from aegis.common.logging import configure_logging
from aegis.store.migrate import apply_migrations, set_role_passwords

DIR = Path(__file__).resolve().parents[1] / "migrations"
DEV_ADMIN = "postgresql://aegis_migrator:dev-only-change-me@127.0.0.1:5432/aegis"  # local only


def _password(name: str, dev_default: str, url: str) -> str:
    """Known dev passwords are only allowed against a local database (review finding M-1)."""
    if name in os.environ:
        return os.environ[name]
    host = url.split("@", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
    if host not in {"127.0.0.1", "localhost", "[::1]"}:
        sys.exit(f"{name} must be set explicitly for non-local database host {host!r}")
    return dev_default


def main(adopt: bool) -> None:
    configure_logging("INFO")
    url = os.environ.get("AEGIS_ADMIN_URL", DEV_ADMIN)
    with psycopg.connect(url) as conn:
        print("applied:", apply_migrations(conn, DIR, adopt=adopt))
        set_role_passwords(
            conn,
            _password("AEGIS_INGEST_PASSWORD", "dev-only-ingest", url),
            _password("AEGIS_APP_PASSWORD", "dev-only-app", url),
            _password("AEGIS_APPROVER_PASSWORD", "dev-only-approver", url),
            _password("AEGIS_REVIEWER_PASSWORD", "dev-only-reviewer", url),
        )
        print("role passwords set")


if __name__ == "__main__":
    main("--adopt" in sys.argv)
