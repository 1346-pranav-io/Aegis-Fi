from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from aegis.ingestion.adapters import CsvMapping
from aegis.ingestion.writer import ingest_csv

pytestmark = pytest.mark.integration

CSV = """id,ts,src,dst,amt,cur,kind,bad
t1,2024-01-01 10:00,A,B,100.50,USD,TRANSFER,0
t2,2024-01-01 11:00,B,C,95.00,USD,TRANSFER,1
t3,2024-01-02 09:00,A,C,-5,USD,TRANSFER,0
t4,not-a-date,A,C,5,USD,TRANSFER,0
"""

MAPPING = CsvMapping(
    adapter_name="test_csv",
    adapter_version="1",
    ref_column="id",
    time_column="ts",
    time_format="%Y-%m-%d %H:%M",
    origin_columns=("src",),
    destination_columns=("dst",),
    amount_column="amt",
    currency_column="cur",
    type_column="kind",
    label_column="bad",
    provenance="test-fixture",
)


@pytest.fixture
def conn(ingest_url: str) -> Iterator[psycopg.Connection]:
    c = psycopg.connect(ingest_url)
    yield c
    c.close()


def _count(conn: psycopg.Connection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    assert row is not None
    return int(row[0])


def test_ingest_counts_quarantine_and_idempotency(conn: psycopg.Connection, tmp_path: Path) -> None:
    f = tmp_path / "t.csv"
    f.write_text(CSV, encoding="utf-8")

    r1 = ingest_csv(conn, f, MAPPING, "fixture", "v1")
    assert (r1.transactions, r1.labels, r1.quarantined) == (2, 2, 2)
    assert _count(conn, "source.transaction") == 2
    assert _count(conn, "source.account") == 3
    assert _count(conn, "source.quarantine") == 2  # negative amount, bad date: kept, not dropped
    laundering = conn.execute("SELECT count(*) FROM source.label WHERE label_value").fetchone()
    assert laundering is not None and laundering[0] == 1

    r2 = ingest_csv(conn, f, MAPPING, "fixture", "v1")  # identical file: no-op
    assert r2.skipped_existing_batch and r2.batch_id == r1.batch_id
    assert _count(conn, "source.transaction") == 2
    assert _count(conn, "source.quarantine") == 2


def test_overlapping_file_does_not_duplicate_records(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    f0 = tmp_path / "t.csv"
    f0.write_text(CSV, encoding="utf-8")
    ingest_csv(conn, f0, MAPPING, "fixture", "v1")
    f = tmp_path / "t2.csv"
    f.write_text(CSV + "t5,2024-01-03 10:00,C,A,7,USD,TRANSFER,0\n", encoding="utf-8")
    before = _count(conn, "source.transaction")
    r = ingest_csv(conn, f, MAPPING, "fixture", "v1")  # different file hash -> new batch
    assert not r.skipped_existing_batch
    assert r.transactions == 1  # only t5 is new; t1/t2 deduped by (ref, content_hash)
    assert _count(conn, "source.transaction") == before + 1
    assert _count(conn, "source.label") == 3  # labels deduped too


def test_missing_file_raises_before_any_batch_is_created(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    missing = tmp_path / "does_not_exist.csv"
    with pytest.raises(FileNotFoundError):
        ingest_csv(conn, missing, MAPPING, "fixture", "v1")
