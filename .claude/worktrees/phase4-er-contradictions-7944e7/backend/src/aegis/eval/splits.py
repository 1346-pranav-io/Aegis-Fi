"""Config-defined, validated temporal splits (D-12, D-24). Windows: half-open [start, end), UTC."""

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from aegis.common.hashing import content_hash
from aegis.common.timeutil import require_utc


class Part(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    STRESS = "stress"


class Window(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    part: Part
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return require_utc(v)

    @model_validator(mode="after")
    def _ordered(self) -> "Window":
        if self.end <= self.start:
            raise ValueError(f"{self.part}: end must be after start")
        return self


class SplitSpec(BaseModel):
    """train < validation < test < stress, non-overlapping, each part exactly once.

    The stress slice is evaluated separately and must never be mixed into headline metrics.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    dataset: str
    windows: tuple[Window, ...]

    @model_validator(mode="after")
    def _valid(self) -> "SplitSpec":
        parts = [w.part for w in self.windows]
        if parts != [Part.TRAIN, Part.VALIDATION, Part.TEST, Part.STRESS]:
            raise ValueError("windows must be exactly train, validation, test, stress in order")
        for a, b in zip(self.windows, self.windows[1:], strict=False):
            if b.start < a.end:
                raise ValueError(f"{b.part} overlaps or precedes {a.part}")
        return self

    def part_of(self, t: datetime) -> Part | None:
        t = require_utc(t)
        for w in self.windows:
            if w.start <= t < w.end:
                return w.part
        return None

    def window(self, part: Part) -> Window:
        return next(w for w in self.windows if w.part == part)

    def spec_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))


def load_split(path: Path) -> SplitSpec:
    return SplitSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def utc_day(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)
