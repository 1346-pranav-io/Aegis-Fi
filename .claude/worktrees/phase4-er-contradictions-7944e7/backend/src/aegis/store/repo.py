"""Read repositories. `as_of` is applied centrally here (D-17): ingested_at <= as_of."""

from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

import psycopg

from aegis.common.timeutil import require_utc


class TxnRow(NamedTuple):
    txn_id: str
    event_time: datetime
    origin_account_id: str
    destination_account_id: str
    amount: Decimal
    currency: str


def fetch_transactions(
    conn: psycopg.Connection,
    as_of: datetime,
    start: datetime,
    end: datetime,
    limit: int = 1_000_000,
) -> list[TxnRow]:
    """Transactions with event_time in [start, end) that were known at `as_of`. No label columns."""
    rows = conn.execute(
        "SELECT txn_id,event_time,origin_account_id,destination_account_id,amount,currency "
        "FROM source.transaction WHERE ingested_at <= %s AND event_time >= %s AND event_time < %s "
        "ORDER BY event_time, txn_id LIMIT %s",
        (require_utc(as_of), require_utc(start), require_utc(end), limit),
    ).fetchall()
    return [TxnRow(*r) for r in rows]
