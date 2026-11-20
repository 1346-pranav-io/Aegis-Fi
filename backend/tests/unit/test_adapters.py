from pathlib import Path

from aegis.ingestion.adapters import CsvMapping, read_csv

MAPPING = CsvMapping(
    adapter_name="t",
    adapter_version="1",
    time_column="ts",
    time_format="%Y-%m-%d",
    origin_columns=("o",),
    destination_columns=("d",),
    amount_column="a",
    fixed_currency="USD",
    label_column="y",
)


def test_rows_become_transactions_labels_or_quarantine(tmp_path: Path) -> None:
    f = tmp_path / "x.csv"
    f.write_text(
        "ts,o,d,a,y\n2024-01-01,A,B,10,1\n2024-01-02,A,B,zzz,0\n2024-01-03,A,B,0,0\n",
        encoding="utf-8",
    )
    rows = list(read_csv(f, MAPPING))
    assert len(rows) == 3  # nothing silently dropped
    assert rows[0].transaction is not None and rows[0].label is not None
    assert rows[0].label.label_value is True
    assert rows[0].transaction.source_txn_ref == "x.csv:1"
    assert rows[1].quarantine is not None and rows[1].quarantine.error_code == "ROW_CONVERSION"
    assert rows[2].quarantine is not None and rows[2].quarantine.error_code == "SCHEMA_VALIDATION"


def test_event_time_is_utc_aware(tmp_path: Path) -> None:
    f = tmp_path / "y.csv"
    f.write_text("ts,o,d,a,y\n2024-01-01,A,B,1,0\n", encoding="utf-8")
    t = next(read_csv(f, MAPPING)).transaction
    assert t is not None and t.event_time.utcoffset() is not None
