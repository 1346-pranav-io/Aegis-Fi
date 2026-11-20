"""Checksummed forward-only SQL migration runner (security findings S-5, H-1).

- Every applied file is recorded with its SHA-256; a changed applied file is an error.
- Fail-closed approval gate: a migration that mentions any protected construct (source./audit.
  objects, roles, grants, functions, triggers, search_path, DO/EXECUTE, ...) is refused unless
  `APPROVALS.json` beside the migrations holds an entry for that filename AND the file's current
  SHA-256. Editing an approved file therefore invalidates its approval. In-file header comments
  are NOT consulted. The approvals file is a record the owner adds to, not a cryptographic
  control: anyone with write access to the repo can edit it, so changes to it must be reviewed.
- Must run as the schema owner (admin) role, never as an application role.
"""

import hashlib
import json
import re
from pathlib import Path

import psycopg
from psycopg import sql

from aegis.common.errors import IntegrityError
from aegis.common.logging import get_logger

log = get_logger("aegis.migrate")

_TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
  filename text PRIMARY KEY, sha256 text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now(), applied_by text NOT NULL DEFAULT current_user)
"""
# Deliberately broad: false positives only cost an approval entry, false negatives cost safety.
_PROTECTED = re.compile(
    r"source|audit|schema_migrations|grant|revoke|role|owner|function|procedure|trigger|"
    r"security|search_path|session_replication_role|do\s*\$|execute|superuser|polic|extension|copy",
    re.I,
)
_OK_STATUSES = {"approved", "retroactive"}  # "retroactive" = disclosed after the fact (historic)


def _load_approvals(directory: Path) -> dict[str, dict[str, str]]:
    f = directory / "APPROVALS.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise IntegrityError("APPROVALS.json must be an object")
    return {str(k): dict(v) for k, v in data.items()}


def _check_approved(
    name: str, text: str, digest: str, approvals: dict[str, dict[str, str]]
) -> None:
    if not _PROTECTED.search(text):
        return
    entry = approvals.get(name)
    if entry is None or entry.get("status") not in _OK_STATUSES:
        raise IntegrityError(f"{name} touches protected objects and has no approval entry")
    if entry.get("sha256") != digest:
        raise IntegrityError(f"{name} changed since it was approved (hash mismatch)")
    log.info(
        "migrate.approval_ok", file=name, status=entry["status"], decision=entry.get("decision")
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def apply_migrations(conn: psycopg.Connection, directory: Path, adopt: bool = False) -> list[str]:
    """Apply pending migrations in filename order. With adopt=True, record unapplied files as
    already applied WITHOUT executing them (only for a database known to contain them)."""
    conn.execute(_TRACKING_DDL)
    conn.commit()
    applied: dict[str, str] = dict(
        conn.execute("SELECT filename, sha256 FROM public.schema_migrations").fetchall()
    )
    approvals = _load_approvals(directory)
    done: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        digest = _sha(text)
        if path.name in applied:
            if applied[path.name] != digest:
                raise IntegrityError(f"migration {path.name} changed after being applied")
            continue
        _check_approved(path.name, text, digest, approvals)  # also when adopting: fail closed
        if adopt:
            log.warning("migrate.adopted_without_running", file=path.name)
        else:
            conn.execute(text)  # multi-statement, simple-query protocol
        conn.execute(
            "INSERT INTO public.schema_migrations (filename, sha256) VALUES (%s, %s)",
            (path.name, digest),
        )
        conn.commit()
        done.append(path.name)
        log.info("migrate.applied", file=path.name)
    return done


def set_role_passwords(
    conn: psycopg.Connection,
    ingest_password: str,
    app_password: str,
    approver_password: str,
    reviewer_password: str,
) -> None:
    """Set login passwords for the application roles (admin only). Values come from the
    environment at the call site; they are never stored in migrations or the repo."""
    roles = [
        ("aegis_ingest", ingest_password),
        ("aegis_app", app_password),
        ("aegis_approver", approver_password),  # created by migration 0007; never left passwordless
        ("aegis_reviewer", reviewer_password),  # created by migration 0023; likewise
    ]
    for role, pw in roles:
        if not pw:
            raise IntegrityError(f"empty password for {role}")
        conn.execute(
            sql.SQL(
                "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
            ).format(sql.Identifier(role), sql.Literal(pw))
        )
    conn.commit()
