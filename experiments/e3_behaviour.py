"""E3: Behaviour analytics on the IBM pattern blocks (D-23 gold), oracle-windowed.

For each of the 370 blocks: test window = [first block txn, last block txn + 1 day); baseline =
[start of data, first block txn). Block accounts = every account in the block. Controls = for each
block, the same number of OTHER accounts that have at least one transaction in the same test window
(so "was active in the window" does not trivially separate the groups), sampled uniformly.

Reported: share of accounts with INSUFFICIENT_HISTORY, and for accounts with enough history the
separation (ROC-AUC, block vs control) of each deviation measure. Account-level samples are
clustered by block, so AUC intervals would be too narrow; none are given. This is a descriptive
check of the module, not a detector evaluation.

Run from backend/:
  AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis uv run python ../experiments/e3_behaviour.py
"""

import csv
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from sklearn.metrics import roc_auc_score

from aegis.analytics.behaviour import PeerTable, behaviour_profile
from aegis.analytics.graph import EdgeIndex
from aegis.store.frames import load_transactions

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ibm-aml"
DAY = 86400
MEASURES = [
    "z_out_count",
    "z_in_count",
    "new_counterparty_share",
    "z_log_amount_mean",
    "peer_percentile_of_test_count",
]


def parse_blocks() -> list[tuple[str, list[tuple[str, ...]]]]:
    blocks: list[tuple[str, list[tuple[str, ...]]]] = []
    cur: list[tuple[str, ...]] | None = None
    kind = ""
    for line in (RAW / "HI-Small_Patterns.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("BEGIN"):
            kind = re.sub(r":.*", "", line.replace("BEGIN LAUNDERING ATTEMPT - ", ""))
            cur = []
        elif line.startswith("END"):
            if cur is None:
                raise ValueError("END without BEGIN")
            blocks.append((kind, cur))
            cur = None
        elif line.strip() and cur is not None:
            cur.append(tuple(next(csv.reader([line]))))
    return blocks


def main() -> None:
    t0 = time.monotonic()
    rng = np.random.default_rng(11)
    with psycopg.connect(os.environ["AEGIS_APP_URL"]) as conn:
        cur = conn.execute("SELECT clock_timestamp()").fetchone()
        if cur is None:
            raise RuntimeError("no clock")
        raw = load_transactions(conn, cur[0])
    code = {r: i for i, r in enumerate(raw.attrs["account_refs"])}
    n_acc = len(code)
    src, dst = raw["origin"].to_numpy(np.int64), raw["dest"].to_numpy(np.int64)
    t, amt = raw["t"].to_numpy(np.int64), raw["amount"].to_numpy(np.float64)
    index = EdgeIndex.build(src, dst, t, amt)
    data_start = int(t.min())
    print(f"loaded {time.monotonic() - t0:.0f}s", flush=True)

    rows: list[dict[str, Any]] = []
    seen_block_accounts: set[int] = set()
    for bi, (kind, lines) in enumerate(parse_blocks()):
        ts, accts = [], set()
        for rw in lines:
            ts.append(int(datetime.strptime(rw[0], "%Y/%m/%d %H:%M").replace(tzinfo=UTC).timestamp()))
            accts |= {code[f"{rw[1]}|{rw[2]}"], code[f"{rw[3]}|{rw[4]}"]}
        first, last = min(ts), max(ts)
        base, test = (data_start, first), (first, last + DAY)
        if base[1] <= base[0]:
            continue  # block starts at the very beginning of the data: no baseline exists
        peers = PeerTable.build(index, n_acc, base, test)
        members = sorted(accts - seen_block_accounts)
        seen_block_accounts |= set(members)
        active = np.flatnonzero(peers.n_test > 0)
        pool = np.setdiff1d(active, np.array(sorted(accts)), assume_unique=False)
        ctrl = rng.choice(pool, size=min(len(members), len(pool)), replace=False) if len(pool) else []
        for group, ids in (("block", members), ("control", [int(x) for x in ctrl])):
            for a in ids:
                p = behaviour_profile(index, int(a), base, test, peers)
                rows.append({"group": group, "kind": kind, "block": bi, **{k: p.get(k) for k in ["status", *MEASURES]}})
        if bi % 50 == 0:
            print(f"block {bi} {time.monotonic() - t0:.0f}s", flush=True)

    out: dict[str, Any] = {"n_rows": len(rows), "groups": {}}
    for g in ("block", "control"):
        gr = [r for r in rows if r["group"] == g]
        ok = [r for r in gr if r["status"] == "OK"]
        out["groups"][g] = {
            "accounts": len(gr),
            "insufficient_history_share": 1 - len(ok) / len(gr) if gr else None,
            "ok_accounts": len(ok),
            **{
                f"median_{m}": float(np.median([r[m] for r in ok if r[m] is not None])) for m in MEASURES if any(r[m] is not None for r in ok)  # noqa: E501
            },
        }
    ok_rows = [r for r in rows if r["status"] == "OK"]
    auc: dict[str, Any] = {}
    for m in MEASURES:
        sub = [r for r in ok_rows if r[m] is not None]
        y = np.array([r["group"] == "block" for r in sub], dtype=int)
        if 0 < y.sum() < len(y):
            auc[m] = {"auc_block_vs_control": float(roc_auc_score(y, [r[m] for r in sub])), "n": len(sub),
                      "n_block": int(y.sum())}  # fmt: skip
    out["auc_among_sufficient_history"] = auc
    # peer percentile also exists for INSUFFICIENT_HISTORY accounts
    sub = [r for r in rows if r["peer_percentile_of_test_count"] is not None]
    y = np.array([r["group"] == "block" for r in sub], dtype=int)
    out["auc_peer_percentile_all_accounts"] = {"auc": float(roc_auc_score(y, [r["peer_percentile_of_test_count"] for r in sub])), "n": len(sub)}  # fmt: skip
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    res = ROOT / "experiments" / "results" / f"e3_behaviour_{stamp}.json"
    res.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(json.dumps(out, indent=1), flush=True)
    print(f"done {time.monotonic() - t0:.0f}s -> {res}", flush=True)


if __name__ == "__main__":
    main()
