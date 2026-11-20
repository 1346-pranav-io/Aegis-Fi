"""Postgres-backed LangGraph checkpoints (FR-ORC-05).

The saver's tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
`checkpoint_migrations`, in `public`) are created by the LIBRARY's own `setup()`, not by our
migration files. `install_checkpoint_tables` must therefore be run once by an ADMIN (schema
owner) connection - the application role cannot create tables - and it grants the application
role the DML it needs. This is a schema addition outside `migrations/`; it is listed for the
owner in docs/PHASE3_NOTES.md."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def install_checkpoint_tables(admin_url: str, app_role: str = "aegis_app") -> None:
    with psycopg.connect(admin_url, autocommit=True, row_factory=dict_row) as c:
        PostgresSaver(c).setup()
        for t in TABLES:
            c.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{t} TO {app_role}")
        c.execute(f"GRANT SELECT ON public.checkpoint_migrations TO {app_role}")


@contextmanager
def postgres_checkpointer(app_url: str) -> Iterator[PostgresSaver]:
    """A saver on its own autocommit connection (LangGraph requires dict rows + autocommit)."""
    with psycopg.connect(app_url, autocommit=True, row_factory=dict_row) as c:
        yield PostgresSaver(c)
