"""Composition of the Postgres-backed runtime services (the production counterpart of the
in-memory kits used in tests). The entity-link reader is the real one from `aegis.entities`;
`FakeERLinkReader` is for in-memory tests only."""

from collections.abc import Callable, Mapping

import psycopg

from aegis.entities.repo import PgERLinkReader
from aegis.evidence.pg_store import PostgresLeafHashes
from aegis.llm.gateway import LlmGateway
from aegis.runtime.pg_services import (
    PostgresApprovals,
    PostgresAudit,
    PostgresBackend,
    PostgresCaseReader,
    PostgresSourceReader,
)
from aegis.runtime.services import RuntimeServices
from aegis.runtime.wiring import default_schemas


def build_postgres_services(
    conn: psycopg.Connection, *, llm: LlmGateway | None = None
) -> RuntimeServices:
    """`conn` must be an autocommit connection opened as the application login."""
    leaves = PostgresLeafHashes(conn)
    return RuntimeServices(
        backend=PostgresBackend(conn, leaves),
        leaves=leaves,
        source=PostgresSourceReader(conn),
        cases=PostgresCaseReader(conn),
        er=PgERLinkReader(conn),
        approvals=PostgresApprovals(conn),
        audit=PostgresAudit(conn),
        llm=llm,
        schemas=default_schemas(),
    )


def _keep(xs: list[object]) -> list[str]:
    return sorted({x for x in xs if isinstance(x, str) and len(x.strip()) >= 3})


def postgres_name_source(conn: psycopg.Connection) -> Callable[[], Mapping[str, list[str]]]:
    """Central source of the names the gateway must mask (D-29): account references, entity and
    organisation names and their raw/normalised forms from entity resolution, and the identifier
    values mentions and entities carry. Read fresh on every call, so names added later are masked
    from the next call on. Values shorter than 3 characters are skipped (they would mask ordinary
    words); the regex layer and the leak guard still apply."""

    def source() -> Mapping[str, list[str]]:
        acct = [
            r[0] for r in conn.execute("SELECT DISTINCT source_account_ref FROM source.account")
        ]
        org = [
            r[0]
            for r in conn.execute(
                "SELECT raw_name FROM derived.entity_mention UNION "
                "SELECT canonical_name FROM derived.resolved_entity UNION "
                "SELECT normalised_name FROM derived.entity_mention UNION "
                "SELECT normalised_name FROM derived.resolved_entity"
            )
        ]
        idv = [
            v
            for (obj,) in conn.execute(
                "SELECT identifiers FROM derived.entity_mention UNION "
                "SELECT identifiers FROM derived.resolved_entity"
            )
            for v in dict(obj).values()
        ]
        return {"ACCT": _keep(acct), "ORG": _keep(org), "ID": _keep(idv)}

    return source
