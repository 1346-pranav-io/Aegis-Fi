"""Source-record contracts (DATA_SCHEMA §1). Immutable once built."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis.common.hashing import content_hash
from aegis.common.timeutil import require_utc


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PartyType(StrEnum):
    INDIVIDUAL = "INDIVIDUAL"
    ORGANISATION = "ORGANISATION"


class Account(_Frozen):
    source_account_ref: str = Field(min_length=1)
    institution_id: str | None = None
    currency: str | None = None
    event_time: datetime | None = None

    @field_validator("event_time")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else require_utc(v)

    def hash(self) -> str:
        return content_hash(self.model_dump(mode="python"))


class Transaction(_Frozen):
    source_txn_ref: str = Field(min_length=1)
    event_time: datetime
    origin_account_ref: str = Field(min_length=1)
    destination_account_ref: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=1)  # as given by the source (may be a name, not ISO)
    txn_type: str | None = None
    channel: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_time")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return require_utc(v)

    def hash(self) -> str:
        return content_hash(self.model_dump(mode="python"))


class Label(_Frozen):
    """Ground-truth label; kept apart from features (DATA_SCHEMA §9)."""

    subject_type: str
    subject_ref: str
    label_name: str
    label_value: bool
    pattern_type: str | None = None
    provenance: str


class QuarantineRecord(_Frozen):
    raw_record: str
    error_code: str
    error_detail: str
