"""Postgres-backed LangGraph checkpoints (FR-ORC-05).

The tables live in schema `runtime` and are created by migration 0024 (decision P-2, D-28), not by
the library's `setup()`. The saver uses unqualified table names, so its connection sets
`search_path=runtime`. Nothing here creates tables."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

SEARCH_PATH = "runtime,pg_catalog"


@contextmanager
def postgres_checkpointer(app_url: str) -> Iterator[PostgresSaver]:
    """A saver on its own autocommit connection (LangGraph requires dict rows + autocommit)."""
    with psycopg.connect(
        app_url, autocommit=True, row_factory=dict_row, options=f"-c search_path={SEARCH_PATH}"
    ) as c:
        yield PostgresSaver(c)
