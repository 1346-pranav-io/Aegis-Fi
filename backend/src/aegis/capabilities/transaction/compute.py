"""Pure transaction computations. Decimal arithmetic for money; ints for counts; no floats."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from aegis.domain.evidence_content import Event, Fact, numeric_fact


@dataclass(frozen=True)
class Row:
    txn_id: str
    t: datetime
    origin: str
    dest: str
    amount: Decimal
    currency: str


def parse_rows(raw: list[dict[str, Any]]) -> list[Row]:
    rows = [
        Row(
            r["txn_id"],
            datetime.fromisoformat(r["event_time"]),
            r["origin_ref"],
            r["dest_ref"],
            Decimal(r["amount"]),
            r["currency"],
        )
        for r in raw
    ]
    return sorted(rows, key=lambda r: (r.t, r.txn_id))


def _by_currency(rows: list[Row]) -> dict[str, str]:
    tot: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        tot[r.currency] += r.amount
    return {c: format(v, "f") for c, v in sorted(tot.items())}


def split(rows: list[Row], subject: str) -> tuple[list[Row], list[Row]]:
    """(outgoing, incoming) with respect to the subject; self-transfers count as outgoing."""
    out = [r for r in rows if r.origin == subject]
    inc = [r for r in rows if r.dest == subject and r.origin != subject]
    return out, inc


def aggregate(rows: list[Row], subject: str) -> dict[str, Any]:
    out, inc = split(rows, subject)
    cps = {r.dest for r in out} | {r.origin for r in inc}
    cps.discard(subject)
    return {
        "subject": subject,
        "n_txns": len(out) + len(inc),
        "n_out": len(out),
        "n_in": len(inc),
        "total_out": _by_currency(out),
        "total_in": _by_currency(inc),
        "distinct_counterparties": len(cps),
        "first_event": rows[0].t.isoformat() if rows else None,
        "last_event": rows[-1].t.isoformat() if rows else None,
    }


def velocity(rows: list[Row], subject: str, window_s: int = 3600) -> dict[str, Any]:
    """Most transactions touching the subject inside any rolling window of `window_s` seconds."""
    ts = [r.t.timestamp() for r in rows if subject in (r.origin, r.dest)]
    best, best_start, lo = 0, None, 0
    for hi, t in enumerate(ts):
        while t - ts[lo] > window_s:
            lo += 1
        if hi - lo + 1 > best:
            best, best_start = hi - lo + 1, ts[lo]
    return {
        "window_s": window_s,
        "max_txns_in_window": best,
        "window_start": (
            datetime.fromtimestamp(best_start, rows[0].t.tzinfo).isoformat()
            if best_start is not None
            else None
        ),
        "n_txns": len(ts),
    }


def structuring(rows: list[Row], subject: str, threshold: Decimal, band_pct: int) -> dict[str, Any]:
    """Outgoing transactions in [threshold*(1-band), threshold): just below a reporting limit."""
    lo = threshold * (Decimal(100 - band_pct) / Decimal(100))
    out, _ = split(rows, subject)
    hits = [r for r in out if lo <= r.amount < threshold]
    return {
        "threshold": format(threshold, "f"),
        "band_pct": band_pct,
        "lower_bound": format(lo, "f"),
        "n_just_below": len(hits),
        "n_out": len(out),
        "txn_ids": [r.txn_id for r in hits],
    }


def rapid_movement(rows: list[Row], subject: str, window_s: int) -> dict[str, Any]:
    """Outgoing transactions that follow an incoming one within `window_s` (pass-through)."""
    out, inc = split(rows, subject)
    in_ts = [r.t.timestamp() for r in inc]
    n_rapid = 0
    for r in out:
        prior = [t for t in in_ts if t <= r.t.timestamp()]
        if prior and r.t.timestamp() - max(prior) <= window_s:
            n_rapid += 1
    return {"window_s": window_s, "n_out": len(out), "n_in": len(inc), "n_rapid_out": n_rapid}


def counterparties(rows: list[Row], subject: str, top: int = 5) -> dict[str, Any]:
    agg: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.origin == subject and r.dest != subject:
            agg[r.dest].append(r)
        elif r.dest == subject and r.origin != subject:
            agg[r.origin].append(r)
    ranked = sorted(agg.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top]
    return {
        "n_counterparties": len(agg),
        "top": [
            {"counterparty": cp, "n_txns": len(rs), "total": _by_currency(rs)} for cp, rs in ranked
        ],
    }


def aggregate_facts_and_events(
    agg: dict[str, Any], subject: str, time_scope: str
) -> tuple[list[Fact], list[Event]]:
    """Machine-readable restatement of the aggregate for the contradiction detectors
    (domain.evidence_content): totals per currency as numeric facts, the transaction count, and
    the first/last transaction times as events."""
    facts = [numeric_fact(subject, "txn_count", str(agg["n_txns"]), "count", time_scope)]
    for pred, key in (("total_out", "total_out"), ("total_in", "total_in")):
        for currency, total in sorted(dict(agg[key]).items()):
            facts.append(numeric_fact(subject, pred, total, currency, time_scope))
    events = [
        Event(subject_ref=subject, kind="TRANSACTION", at=datetime.fromisoformat(agg[k]))
        for k in ("first_event", "last_event")
        if agg.get(k)
    ]
    return facts, events
