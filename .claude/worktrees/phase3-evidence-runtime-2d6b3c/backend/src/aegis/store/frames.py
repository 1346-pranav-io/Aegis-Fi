"""Bulk read of transactions as a pandas frame for analytics. NEVER selects label data (T-INV-14).

Streams with COPY ... TO STDOUT into a temp file, because pulling 5M rows through python tuples
would need several GB. `as_of` is applied in SQL against the database-assigned ingested_at.
"""

import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from aegis.common.timeutil import require_utc

_SQL = """
COPY (
  SELECT t.source_txn_ref AS ref,
         extract(epoch FROM t.event_time)::bigint AS t,
         o.source_account_ref AS origin_ref,
         d.source_account_ref AS dest_ref,
         t.amount::float8 AS amount,
         t.currency AS currency_name,
         t.txn_type AS fmt_name,
         (t.attributes->>'amount_received')::float8 AS recv_amount,
         t.attributes->>'receiving_currency' AS recv_currency_name
  FROM source.transaction t
  JOIN source.account o ON o.account_id = t.origin_account_id
  JOIN source.account d ON d.account_id = t.destination_account_id
  WHERE t.ingested_at <= %(as_of)s
  ORDER BY t.event_time, t.txn_id
) TO STDOUT WITH (FORMAT csv, HEADER true)
"""


def load_transactions(conn: psycopg.Connection, as_of: datetime) -> pd.DataFrame:
    """Return the columns needed by analytics.features.build_features plus `ref`.

    Account refs are 'bank|account'; codes are dense integers assigned over origin AND dest.
    """
    as_of = require_utc(as_of)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "txn.csv"
        with conn.cursor() as cur, cur.copy(_SQL.replace("%(as_of)s", "%s"), (as_of,)) as cp:
            with path.open("wb") as f:
                for chunk in cp:
                    f.write(bytes(chunk))
        raw = pd.read_csv(path, dtype={"ref": str, "origin_ref": str, "dest_ref": str})
    both = pd.concat([raw["origin_ref"], raw["dest_ref"]], ignore_index=True)
    codes, uniques = pd.factorize(both)
    n = len(raw)
    out = pd.DataFrame(
        {
            "ref": raw["ref"],
            "t": raw["t"].to_numpy(np.int64),
            "origin": codes[:n].astype(np.int64),
            "dest": codes[n:].astype(np.int64),
            "origin_bank": pd.factorize(raw["origin_ref"].str.split("|").str[0])[0],
            "dest_bank": 0,
            "amount": raw["amount"].to_numpy(np.float64),
            "recv_amount": raw["recv_amount"].fillna(raw["amount"]).to_numpy(np.float64),
        }
    )
    # banks must share one coding to compare origin_bank == dest_bank
    banks = pd.concat(
        [raw["origin_ref"].str.split("|").str[0], raw["dest_ref"].str.split("|").str[0]],
        ignore_index=True,
    )
    bcodes, _ = pd.factorize(banks)
    out["origin_bank"] = bcodes[:n]
    out["dest_bank"] = bcodes[n:]
    cur_all = pd.concat([raw["currency_name"], raw["recv_currency_name"]], ignore_index=True)
    ccodes, cur_names = pd.factorize(cur_all)
    out["currency"] = ccodes[:n].astype(np.int64)
    out["recv_currency"] = ccodes[n:].astype(np.int64)
    out["fmt"] = pd.factorize(raw["fmt_name"])[0].astype(np.int64)
    out.attrs["n_accounts"] = len(uniques)
    out.attrs["account_refs"] = list(uniques)  # code -> "bank|account" text
    out.attrs["currencies"] = list(cur_names)
    return out
