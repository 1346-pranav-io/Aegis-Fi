"""Config-driven CSV adapter. Dataset-specific column names live in a mapping, not in code.

No real dataset mapping is shipped yet: the IBM AML column names are UNVERIFIED
(docs/research/DATASET_VERIFICATION.md) and must be read from the downloaded file first.
"""

import csv
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis.domain.source import Label, QuarantineRecord, Transaction


class CsvMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter_name: str
    adapter_version: str
    ref_column: str | None = None  # if None, ref = "<file>:<row number>"
    time_column: str
    time_format: str  # strptime format; times are interpreted as UTC
    origin_columns: tuple[str, ...]  # joined with "|" to form the account ref
    destination_columns: tuple[str, ...]
    header_override: tuple[str, ...] | None = None  # for files with duplicate/ambiguous headers
    amount_column: str
    currency_column: str | None = None
    fixed_currency: str | None = None
    type_column: str | None = None
    label_column: str | None = None
    label_true_values: frozenset[str] = Field(default_factory=lambda: frozenset({"1", "true"}))
    extra_columns: tuple[str, ...] = ()
    provenance: str = "unspecified"


class AdapterRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction: Transaction | None = None
    label: Label | None = None
    quarantine: QuarantineRecord | None = None


def _quarantine(raw: dict[str, str], code: str, detail: str) -> AdapterRow:
    return AdapterRow(
        quarantine=QuarantineRecord(raw_record=repr(raw), error_code=code, error_detail=detail)
    )


def read_csv(path: Path, mapping: CsvMapping) -> Iterator[AdapterRow]:
    """Yield one AdapterRow per data row; bad rows become quarantine records, never dropped."""
    with path.open(newline="", encoding="utf-8") as f:
        if mapping.header_override:
            next(csv.reader(f))  # discard the file's own header line
        reader = csv.DictReader(f, fieldnames=mapping.header_override)
        for n, raw in enumerate(reader, start=1):
            try:
                yield _convert(raw, n, path.name, mapping)
            except (KeyError, ValueError, InvalidOperation) as exc:
                yield _quarantine(raw, "ROW_CONVERSION", f"{type(exc).__name__}: {exc}")


def _convert(raw: dict[str, str], n: int, fname: str, m: CsvMapping) -> AdapterRow:
    ref = raw[m.ref_column] if m.ref_column else f"{fname}:{n}"
    currency = raw[m.currency_column] if m.currency_column else m.fixed_currency
    if currency is None:
        raise ValueError("no currency column or fixed_currency configured")
    extras: dict[str, Any] = {c: raw[c] for c in m.extra_columns}
    try:
        txn = Transaction.model_validate(
            {
                "source_txn_ref": ref,
                "event_time": datetime.strptime(raw[m.time_column], m.time_format).replace(
                    tzinfo=UTC
                ),
                "origin_account_ref": "|".join(raw[c] for c in m.origin_columns),
                "destination_account_ref": "|".join(raw[c] for c in m.destination_columns),
                "amount": Decimal(raw[m.amount_column]),
                "currency": currency,
                "txn_type": raw[m.type_column] if m.type_column else None,
                "attributes": extras,
            }
        )
    except ValueError as exc:  # pydantic ValidationError subclasses ValueError
        return _quarantine(raw, "SCHEMA_VALIDATION", str(exc)[:1000])
    label = None
    if m.label_column:
        label = Label(
            subject_type="TRANSACTION",
            subject_ref=ref,
            label_name="LAUNDERING",
            label_value=raw[m.label_column].strip().lower() in m.label_true_values,
            provenance=m.provenance,
        )
    return AdapterRow(transaction=txn, label=label)
