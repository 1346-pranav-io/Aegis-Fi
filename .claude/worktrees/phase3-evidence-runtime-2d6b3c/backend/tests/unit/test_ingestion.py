from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from aegis.domain.source import QuarantineRecord, Transaction
from aegis.ingestion.validate import validate_rows

GOOD: dict[str, Any] = {
    "source_txn_ref": "t1",
    "event_time": datetime(2024, 1, 1, tzinfo=UTC),
    "origin_account_ref": "a",
    "destination_account_ref": "b",
    "amount": Decimal("10.00"),
    "currency": "USD",
}


def test_valid_and_invalid_rows_both_accounted_for() -> None:
    bad = {**GOOD, "amount": Decimal("-1")}
    out = list(validate_rows([GOOD, bad], Transaction))
    assert isinstance(out[0], Transaction)
    assert isinstance(out[1], QuarantineRecord)
    assert out[1].error_code == "SCHEMA_VALIDATION"


def test_transaction_is_frozen_and_hash_stable() -> None:
    t = Transaction.model_validate(GOOD)
    with pytest.raises(ValidationError):
        t.amount = Decimal("5")  # type: ignore[misc]
    assert t.hash() == Transaction.model_validate(GOOD).hash()


def test_naive_time_rejected() -> None:
    with pytest.raises(ValidationError):
        Transaction.model_validate({**GOOD, "event_time": datetime(2024, 1, 1)})
