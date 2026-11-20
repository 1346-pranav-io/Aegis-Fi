"""Label access, for training/evaluation jobs only (T-INV-14)."""

import pandas as pd
import psycopg


def fetch_laundering_refs(conn: psycopg.Connection, provenance: str) -> set[str]:
    rows = conn.execute(
        "SELECT subject_ref FROM source.label WHERE provenance=%s AND label_value", (provenance,)
    ).fetchall()
    return {r[0] for r in rows}


def fetch_label_series(conn: psycopg.Connection, provenance: str) -> pd.Series:
    """Series indexed by source_txn_ref with 1 for laundering, 0 otherwise."""
    rows = conn.execute(
        "SELECT subject_ref, label_value FROM source.label "
        "WHERE provenance=%s AND subject_type='TRANSACTION' AND label_name='LAUNDERING'",
        (provenance,),
    ).fetchall()
    s = pd.Series({r[0]: int(r[1]) for r in rows}, name="label", dtype="int8")
    if s.index.has_duplicates:  # pragma: no cover - dict construction already dedupes
        raise ValueError("duplicate labels")
    return s
