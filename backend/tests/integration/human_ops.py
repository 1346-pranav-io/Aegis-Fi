"""Run human-review operations on their own login (migration 0023).

Contradiction status changes and human entity-link decisions can only be written by the
`aegis_reviewer` login. These helpers open a reviewer connection to the SAME database as the
given application connection, so a test can keep working with its app connection.
"""

from collections.abc import Callable
from typing import Any

import psycopg

REVIEWER_PW = "dev-only-reviewer"  # local test value, matches scripts/migrate.py default


def reviewer_conn(like: psycopg.Connection) -> psycopg.Connection:
    i = like.info
    return psycopg.connect(
        host=i.host, port=i.port, dbname=i.dbname, user="aegis_reviewer", password=REVIEWER_PW
    )


def as_reviewer(like: psycopg.Connection, fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
    """Commit `like`, call fn(reviewer_connection, *args, **kw), commit the reviewer connection."""
    like.commit()
    with reviewer_conn(like) as rc:
        out = fn(rc, *args, **kw)
        rc.commit()
        return out
