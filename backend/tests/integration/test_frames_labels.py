"""frames.load_transactions / labels.fetch_label_series on a tiny IBM-format file."""

from pathlib import Path

import psycopg
import pytest

from aegis.ingestion.mappings import IBM_AML_TRANS
from aegis.ingestion.writer import ingest_csv
from aegis.store.frames import load_transactions
from aegis.store.labels import fetch_label_series

pytestmark = pytest.mark.integration

HEADER = (
    "Timestamp,From Bank,Account,To Bank,Account,Amount Received,Receiving Currency,"
    "Amount Paid,Payment Currency,Payment Format,Is Laundering\n"
)
ROWS = (
    "2022/09/01 10:00,010,AAA,020,BBB,100.0,Euro,90.0,US Dollar,ACH,0\n"
    "2022/09/01 11:00,020,BBB,010,AAA,50.0,US Dollar,50.0,US Dollar,Wire,1\n"
    "2022/09/02 09:30,010,AAA,010,AAA,5.0,US Dollar,5.0,US Dollar,Reinvestment,0\n"
)


def test_frame_has_no_labels_and_codes_are_consistent(
    ingest_url: str, app_url: str, tmp_path: Path
) -> None:
    f = tmp_path / "t.csv"
    f.write_text(HEADER + ROWS, encoding="utf-8")
    with psycopg.connect(ingest_url) as c:
        ingest_csv(c, f, IBM_AML_TRANS, "IBM-AML-HI-Small", "fixture")
    with psycopg.connect(app_url) as c:
        now = c.execute("SELECT clock_timestamp()").fetchone()
        assert now is not None
        df = load_transactions(c, now[0])
        labels = fetch_label_series(c, "IBM-AML-HI-Small")
    assert len(df) == 3 and list(df["t"]) == sorted(df["t"])
    assert not any("label" in col.lower() or "launder" in col.lower() for col in df.columns)
    # same account text -> same code on both sides; bank codes shared for the same-bank test
    assert df.loc[0, "origin"] == df.loc[1, "dest"] and df.loc[0, "dest"] == df.loc[1, "origin"]
    assert (
        df.loc[2, "origin"] == df.loc[2, "dest"]
        and df.loc[2, "origin_bank"] == df.loc[2, "dest_bank"]
    )
    assert df.loc[0, "currency"] != df.loc[0, "recv_currency"]  # US Dollar paid, Euro received
    assert df.loc[0, "amount"] == 90.0 and df.loc[0, "recv_amount"] == 100.0
    assert sorted(labels.tolist()) == [0, 0, 1] and len(labels) == 3
    assert set(df["ref"]) == set(labels.index)
