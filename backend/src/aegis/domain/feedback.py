"""Feedback contracts (FR-HITL-01/03/04, DATA_SCHEMA workflow.feedback)."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TargetType(StrEnum):
    CLAIM = "CLAIM"
    ER_LINK = "ER_LINK"
    CONTRADICTION = "CONTRADICTION"
    ALERT = "ALERT"
    REPORT = "REPORT"


class FeedbackAction(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    EDIT = "EDIT"
    COMMENT = "COMMENT"
    LABEL = "LABEL"


class FeedbackSource(StrEnum):
    HUMAN = "HUMAN"
    SIMULATED = "SIMULATED"  # disclosed in every export (Q-09)


class ValidationStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    QUARANTINED = "QUARANTINED"


VERDICTS = frozenset({FeedbackAction.ACCEPT, FeedbackAction.REJECT})
RULES_VERSION = "feedback-rules-v1"


class FeedbackIn(BaseModel):
    """What a caller may say. The user id, role and login are NOT in here: they come from the
    verified identity (API) and the database."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    target_type: TargetType
    target_id: str = Field(min_length=1, max_length=200)
    action: FeedbackAction
    reason: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    source: FeedbackSource = FeedbackSource.HUMAN


class RuleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: str
    passed: bool
    detail: str = ""
