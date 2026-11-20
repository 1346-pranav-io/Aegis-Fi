"""Ingest IBM HI-Small transactions into the local dev database as role aegis_ingest (direct login).

Usage: python -I scripts/ingest_ibm.py <path-to-HI-Small_Trans.csv>
Env:   AEGIS_INGEST_URL, e.g. postgresql://aegis_ingest:<password>@127.0.0.1:5432/aegis
       (logs in AS the ingest role; never use an admin/superuser connection here)
Source tables are insert-only: a load cannot be undone except by dropping the database.
"""

import os
import sys
import time
from pathlib import Path

import psycopg

from aegis.common.logging import configure_logging
from aegis.ingestion.mappings import IBM_AML_TRANS
from aegis.ingestion.writer import ingest_csv


def main(path: Path) -> None:
    configure_logging("INFO")
    with psycopg.connect(os.environ["AEGIS_INGEST_URL"]) as conn:
        t0 = time.monotonic()
        r = ingest_csv(conn, path, IBM_AML_TRANS, "IBM-AML-HI-Small", "kaggle-2026-11-11")
        dt = time.monotonic() - t0
    print(r)
    print(f"elapsed {dt:.1f}s  rate {r.transactions / dt:,.0f} txn/s")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
