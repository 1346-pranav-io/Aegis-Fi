"""Ingest the first 20k rows of the real IBM HI-Small file, if it has been downloaded."""

from pathlib import Path

import psycopg
import pytest

from aegis.ingestion.mappings import IBM_AML_TRANS
from aegis.ingestion.writer import ingest_csv

pytestmark = pytest.mark.integration
RAW = Path(__file__).parents[3] / "data" / "raw" / "ibm-aml" / "HI-Small_Trans.csv"


@pytest.mark.skipif(not RAW.exists(), reason="IBM HI-Small not downloaded")
def test_real_file_sample_ingests_cleanly(ingest_url: str, tmp_path: Path) -> None:
    sample = tmp_path / "sample.csv"
    with RAW.open(encoding="utf-8") as src, sample.open("w", encoding="utf-8") as dst:
        for i, line in enumerate(src):
            if i > 20000:
                break
            dst.write(line)
    with psycopg.connect(ingest_url) as conn:
        r = ingest_csv(conn, sample, IBM_AML_TRANS, "IBM-AML-HI-Small-sample", "v1")
        assert r.quarantined == 0
        assert r.transactions == 20000
        cur = conn.execute("SELECT count(DISTINCT currency) FROM source.transaction").fetchone()
        assert cur is not None and cur[0] > 1  # currency names stored as given
        row = conn.execute(
            "SELECT attributes->>'amount_received' FROM source.transaction LIMIT 1"
        ).fetchone()
        assert row is not None and row[0] is not None
