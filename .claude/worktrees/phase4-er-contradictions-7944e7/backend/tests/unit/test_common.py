from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aegis.common.hashing import content_hash
from aegis.common.ids import new_id
from aegis.common.timeutil import require_utc


def test_hash_is_key_order_independent() -> None:
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_hash_decimal_normalised() -> None:
    assert content_hash({"x": Decimal("1.50")}) == content_hash({"x": Decimal("1.5")})


def test_hash_rejects_float_and_naive_datetime() -> None:
    with pytest.raises(ValueError):
        content_hash({"x": 1.5})
    with pytest.raises(ValueError):
        content_hash({"t": datetime(2024, 1, 1)})


def test_hash_timezone_equivalence() -> None:
    a = datetime(2024, 1, 1, 12, tzinfo=UTC)
    b = a.astimezone(timezone(timedelta(hours=5)))
    assert content_hash({"t": a}) == content_hash({"t": b})


@given(st.dictionaries(st.text(), st.integers()))
def test_hash_deterministic(d: dict[str, int]) -> None:
    assert content_hash(d) == content_hash(dict(reversed(list(d.items()))))


def test_ids() -> None:
    assert new_id("case").startswith("case_")
    with pytest.raises(ValueError):
        new_id("bogus")


def test_require_utc() -> None:
    with pytest.raises(ValueError):
        require_utc(datetime(2024, 1, 1))
