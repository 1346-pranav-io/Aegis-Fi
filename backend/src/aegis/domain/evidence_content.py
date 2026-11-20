"""Shared shape of the `facts` and `events` that evidence producers put in `EvidenceDraft.content`
and that the contradiction detectors (analytics.contradictions) read.

The frozen evidence contract leaves `content` free-form. Without a shared declaration a producer
and a detector can disagree on key names and the detectors then silently find nothing
(PHASE4_NOTES risk, reconciliation item R-4). This module is that declaration: producers build
`Fact`/`Event` values and call `with_facts` / `with_events`; consumers call `facts_of` /
`events_of`, which REJECT malformed entries loudly instead of skipping them.

* Fact  - one statement `subject_ref / predicate = value`. Numeric facts carry a `unit` and a
          decimal STRING (content hashes forbid floats); categorical facts have no unit.
* Event - something that happened to a subject at a time (`kind`, ISO-8601 `at` with offset).
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis.common.timeutil import require_utc

FACTS_KEY = "facts"
EVENTS_KEY = "events"


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_ref: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: str  # numeric facts: a decimal string such as "1234.50"
    unit: str | None = None
    time_scope: str | None = None

    def is_numeric(self) -> bool:
        if self.unit is None:
            return False
        try:
            return Decimal(self.value).is_finite()
        except (InvalidOperation, ValueError):
            return False


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_ref: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    at: datetime

    @field_validator("at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return require_utc(v)


def numeric_fact(
    subject_ref: str, predicate: str, value: Decimal | str, unit: str, time_scope: str | None = None
) -> Fact:
    text = format(Decimal(str(value)), "f")
    return Fact(
        subject_ref=subject_ref, predicate=predicate, value=text, unit=unit, time_scope=time_scope
    )


def with_facts(content: Mapping[str, Any], facts: Iterable[Fact]) -> dict[str, Any]:
    out = dict(content)
    out[FACTS_KEY] = [f.model_dump(mode="json", exclude_none=True) for f in facts]
    return out


def with_events(content: Mapping[str, Any], events: Iterable[Event]) -> dict[str, Any]:
    out = dict(content)
    out[EVENTS_KEY] = [
        {"subject_ref": e.subject_ref, "kind": e.kind, "at": e.at.isoformat()} for e in events
    ]
    return out


def facts_of(content: Mapping[str, Any]) -> list[Fact]:
    """The facts in `content`, [] if there are none; a malformed entry raises (never skipped)."""
    raw = content.get(FACTS_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("content['facts'] must be a list")
    return [Fact.model_validate(f) for f in raw]


def events_of(content: Mapping[str, Any]) -> list[Event]:
    raw = content.get(EVENTS_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("content['events'] must be a list")
    return [Event.model_validate(e) for e in raw]
