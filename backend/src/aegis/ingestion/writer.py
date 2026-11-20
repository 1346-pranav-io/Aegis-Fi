"""Idempotent, insert-only batch writer (FR-ING-01..05).

One DB transaction per batch. A batch with the same dataset name/version and file hashes that is
already COMPLETE is returned untouched (idempotent re-ingestion).
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from aegis.common.hashing import file_sha256
from aegis.common.ids import new_id
from aegis.common.logging import get_logger
from aegis.domain.source import Account
from aegis.ingestion.adapters import AdapterRow, CsvMapping, read_csv

log = get_logger("aegis.ingestion")
CHUNK = 50_000


@dataclass(frozen=True)
class IngestResult:
    batch_id: str
    status: str
    transactions: int
    labels: int
    quarantined: int
    skipped_existing_batch: bool


def ingest_csv(
    conn: psycopg.Connection,
    path: Path,
    mapping: CsvMapping,
    dataset_name: str,
    dataset_version: str,
) -> IngestResult:
    file_hashes = {path.name: file_sha256(str(path))}
    existing = conn.execute(
        "SELECT batch_id FROM source.ingestion_batch WHERE dataset_name=%s AND dataset_version=%s "
        "AND file_hashes=%s::jsonb AND status='COMPLETE'",
        (dataset_name, dataset_version, json.dumps(file_hashes, sort_keys=True)),
    ).fetchone()
    conn.commit()
    if existing:
        log.info("ingest.skipped_existing", batch_id=existing[0])
        return IngestResult(existing[0], "COMPLETE", 0, 0, 0, True)

    batch_id = new_id("batch")
    conn.execute(
        "INSERT INTO source.ingestion_batch (batch_id,dataset_name,dataset_version,adapter,"
        "adapter_version,file_hashes,status,started_at) VALUES (%s,%s,%s,%s,%s,%s,'RUNNING',%s)",
        (
            batch_id,
            dataset_name,
            dataset_version,
            mapping.adapter_name,
            mapping.adapter_version,
            json.dumps(file_hashes),
            datetime.now(UTC),
        ),
    )
    conn.commit()  # the RUNNING batch row survives a failed load, so failures stay visible
    try:
        counts = _load(conn, batch_id, read_csv(path, mapping), mapping.provenance)
        conn.execute(
            "UPDATE source.ingestion_batch SET status='COMPLETE', record_count=%s, "
            "quarantine_count=%s, completed_at=%s WHERE batch_id=%s",
            (counts[0], counts[2], datetime.now(UTC), batch_id),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - process boundary: record failure, then re-raise
        conn.rollback()
        conn.execute(
            "UPDATE source.ingestion_batch SET status='FAILED', completed_at=%s WHERE batch_id=%s",
            (datetime.now(UTC), batch_id),
        )
        conn.commit()
        log.error("ingest.failed", batch_id=batch_id, error_type=type(exc).__name__)
        raise
    log.info("ingest.complete", batch_id=batch_id, txns=counts[0], quarantined=counts[2])
    return IngestResult(batch_id, "COMPLETE", counts[0], counts[1], counts[2], False)


def _load(
    conn: psycopg.Connection, batch_id: str, rows: Iterable[AdapterRow], provenance: str
) -> tuple[int, int, int]:
    """Bulk path: COPY each chunk into temp staging tables, then INSERT ... ON CONFLICT DO
    NOTHING into the insert-only source tables. Everything runs in the caller's transaction."""
    n_txn = n_lbl = n_quar = 0
    acct_hash: dict[str, str] = {}
    known_accounts: set[str] = set()
    with conn.cursor() as cur:
        cur.execute(_STAGING_DDL)
        for chunk in _chunks(rows, CHUNK):
            cur.execute("TRUNCATE stg_txn, stg_acct, stg_lbl")
            with cur.copy("COPY stg_txn FROM STDIN") as cp:
                for r in chunk:
                    t = r.transaction
                    if t is None:
                        continue
                    for ref in (t.origin_account_ref, t.destination_account_ref):
                        if ref not in acct_hash:
                            acct_hash[ref] = Account(source_account_ref=ref).hash()
                    cp.write_row(
                        (
                            new_id("txn"),
                            t.source_txn_ref,
                            t.event_time,
                            t.origin_account_ref,
                            acct_hash[t.origin_account_ref],
                            t.destination_account_ref,
                            acct_hash[t.destination_account_ref],
                            t.amount,
                            t.currency,
                            t.txn_type,
                            json.dumps(t.attributes),
                            t.hash(),
                        )
                    )
            with cur.copy("COPY stg_acct FROM STDIN") as cp:
                for r in chunk:
                    t = r.transaction
                    if t is None:
                        continue
                    for ref in (t.origin_account_ref, t.destination_account_ref):
                        if ref not in known_accounts:
                            known_accounts.add(ref)
                            cp.write_row((new_id("acct"), ref, acct_hash[ref]))
            with cur.copy("COPY stg_lbl FROM STDIN") as cp:
                for r in chunk:
                    if r.label:
                        cp.write_row(
                            (
                                new_id("lbl"),
                                r.label.subject_type,
                                r.label.subject_ref,
                                r.label.label_name,
                                r.label.label_value,
                                r.label.pattern_type,
                                r.label.provenance,
                            )
                        )
            cur.execute(
                "INSERT INTO source.account (account_id,batch_id,source_account_ref,content_hash) "
                "SELECT account_id,%s,source_account_ref,content_hash FROM stg_acct "
                "ON CONFLICT (source_account_ref, content_hash) DO NOTHING",
                (batch_id,),
            )
            cur.execute(
                "INSERT INTO source.transaction (txn_id,batch_id,source_txn_ref,event_time,"
                "origin_account_id,destination_account_id,amount,currency,txn_type,attributes,"
                "content_hash) "
                "SELECT s.txn_id,%s,s.source_txn_ref,s.event_time,o.account_id,d.account_id,"
                "s.amount,s.currency,s.txn_type,s.attributes::jsonb,s.content_hash "
                "FROM stg_txn s "
                "JOIN source.account o ON o.source_account_ref=s.origin_ref "
                "AND o.content_hash=s.origin_hash "
                "JOIN source.account d ON d.source_account_ref=s.dest_ref "
                "AND d.content_hash=s.dest_hash "
                "ON CONFLICT (source_txn_ref, content_hash) DO NOTHING",
                (batch_id,),
            )
            n_txn += cur.rowcount
            cur.execute(
                "INSERT INTO source.label (label_id,batch_id,subject_type,subject_ref,label_name,"
                "label_value,pattern_type,provenance) "
                "SELECT DISTINCT ON (s.subject_ref,s.label_name,s.provenance,s.label_value) "
                "s.label_id,%s,s.subject_type,s.subject_ref,s.label_name,s.label_value,"
                "s.pattern_type,s.provenance FROM stg_lbl s WHERE NOT EXISTS (SELECT 1 FROM "
                "source.label l WHERE l.subject_ref=s.subject_ref AND l.label_name=s.label_name "
                "AND l.provenance=s.provenance AND l.label_value=s.label_value)",
                (batch_id,),
            )
            n_lbl += cur.rowcount
            quarantined = [r.quarantine for r in chunk if r.quarantine]
            if quarantined:
                cur.executemany(
                    "INSERT INTO source.quarantine (batch_id,raw_record,error_code,error_detail) "
                    "VALUES (%s,%s,%s,%s)",
                    [(batch_id, q.raw_record, q.error_code, q.error_detail) for q in quarantined],
                )
                n_quar += len(quarantined)
    return n_txn, n_lbl, n_quar


_STAGING_DDL = """
CREATE TEMP TABLE IF NOT EXISTS stg_txn (
  txn_id text, source_txn_ref text, event_time timestamptz, origin_ref text, origin_hash text,
  dest_ref text, dest_hash text, amount numeric, currency text, txn_type text,
  attributes text, content_hash text) ON COMMIT DROP;
CREATE TEMP TABLE IF NOT EXISTS stg_acct (
  account_id text, source_account_ref text, content_hash text) ON COMMIT DROP;
CREATE TEMP TABLE IF NOT EXISTS stg_lbl (
  label_id text, subject_type text, subject_ref text, label_name text, label_value boolean,
  pattern_type text, provenance text) ON COMMIT DROP;
"""


def _chunks(rows: Iterable[AdapterRow], size: int) -> Iterable[list[AdapterRow]]:
    buf: list[AdapterRow] = []
    for r in rows:
        buf.append(r)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
