from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis.eval.splits import Part, SplitSpec, Window, load_split

CFG = Path(__file__).parents[3] / "experiments" / "configs" / "split_ibm_hi_small_v1.json"


def d(day: int, h: int = 0) -> datetime:
    return datetime(2022, 9, day, h, tzinfo=UTC)


def test_config_loads_and_assigns_parts_at_boundaries() -> None:
    s = load_split(CFG)
    assert s.part_of(d(1)) == Part.TRAIN
    assert s.part_of(d(6, 23)) == Part.TRAIN
    assert s.part_of(d(7)) == Part.VALIDATION  # half-open: boundary belongs to the later part
    assert s.part_of(d(9)) == Part.TEST
    assert s.part_of(d(10, 23)) == Part.TEST
    assert s.part_of(d(11)) == Part.STRESS
    assert s.part_of(d(18, 23)) == Part.STRESS
    assert s.part_of(d(19)) is None
    assert s.part_of(datetime(2022, 8, 31, 23, tzinfo=UTC)) is None


def test_every_instant_in_exactly_one_part() -> None:
    s = load_split(CFG)
    hits = [
        sum(w.start <= d(day, h) < w.end for w in s.windows)
        for day in range(1, 19)
        for h in (0, 12)
    ]
    assert set(hits) == {1}


def test_overlap_wrong_order_and_naive_times_rejected() -> None:
    w = [
        Window(part=p, start=d(a), end=d(b))
        for p, a, b in [
            (Part.TRAIN, 1, 8),
            (Part.VALIDATION, 7, 9),
            (Part.TEST, 9, 11),
            (Part.STRESS, 11, 19),
        ]
    ]
    with pytest.raises(ValidationError):
        SplitSpec(name="x", dataset="d", windows=tuple(w))  # train/validation overlap
    with pytest.raises(ValidationError):
        SplitSpec(name="x", dataset="d", windows=tuple(reversed(w)))
    with pytest.raises(ValidationError):
        Window(part=Part.TRAIN, start=datetime(2022, 9, 1), end=d(2))
    with pytest.raises(ValidationError):
        Window(part=Part.TRAIN, start=d(2), end=d(2))


def test_spec_hash_is_stable_and_sensitive() -> None:
    a, b = load_split(CFG), load_split(CFG)
    assert a.spec_hash() == b.spec_hash()
    moved = a.model_copy(update={"name": "other"})
    assert moved.spec_hash() != a.spec_hash()
