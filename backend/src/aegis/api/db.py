"""Per-request database connections, one login per kind of decision.

The API process holds three logins. Which one a request uses follows from what it does (never from
what the caller asks): ordinary work -> app, approval decisions -> approver, contradiction and
entity-link decisions and feedback second reviews -> reviewer. Each request gets a fresh
connection that commits on success and rolls back on any error.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from aegis.api.settings import ApiSettings


class Databases:
    def __init__(self, settings: ApiSettings) -> None:
        self._urls = {
            "app": settings.app_url,
            "approver": settings.approver_url,
            "reviewer": settings.reviewer_url,
        }

    @contextmanager
    def connect(
        self, kind: str = "app", *, autocommit: bool = False
    ) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self._urls[kind], autocommit=autocommit)
        try:
            yield conn
            if not autocommit:
                conn.commit()
        except BaseException:
            if not autocommit:
                conn.rollback()
            raise
        finally:
            conn.close()
