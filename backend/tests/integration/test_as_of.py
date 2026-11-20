"""T-INV-03: records ingested after `as_of` must not affect repository reads."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from aegis.ingestion.adapters import CsvMapping
from aegis.ingestion.writer import ingest_csv
from aegis.store.repo import fetch_transactions

pytestmark = pytest.mark.integration

MAPPING = CsvMapping(
    adapter_name="t",
    adapter_version="1",
    ref_column="id",
    time_column="ts",
    time_format="%Y-%m-%d %H:%M",
    origin_columns=("o",),
    destination_columns=("d",),
    amount_column="a",
    fixed_currency="USD",
)


def test_future_ingestion_does_not_change_past_view(ingest_url: str, tmp_path: Path) -> None:
    f1 = tmp_path / "a.csv"
    f1.write_text("id,ts,o,d,a\nt1,2024-01-01 10:00,A,B,5\n", encoding="utf-8")
    f2 = tmp_path / "b.csv"
    f2.write_text("id,ts,o,d,a\nt2,2024-01-01 11:00,B,C,6\n", encoding="utf-8")
    start, end = datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
    with psycopg.connect(ingest_url) as conn:
        ingest_csv(conn, f1, MAPPING, "d1", "v1")
        # ingested_at is assigned by the DB clock, so as_of must come from the DB clock too
        row = conn.execute("SELECT clock_timestamp()").fetchone()
        assert row is not None
        as_of = row[0]
        before = fetch_transactions(conn, as_of, start, end)
        ingest_csv(conn, f2, MAPPING, "d2", "v1")  # ingested after as_of; event_time is in range
        after = fetch_transactions(conn, as_of, start, end)
        assert before == after and len(before) == 1
        later = fetch_transactions(conn, as_of + timedelta(minutes=5), start, end)
        assert len(later) == 2
