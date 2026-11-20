"""Request and response bodies (FR-API-01). Requests forbid extra fields; none of them carries a
user id or a role: identity comes from the verified token."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis.domain.cases import Disposition
from aegis.domain.evidence import ContradictionStatus
from aegis.domain.feedback import FeedbackAction, TargetType, ValidationStatus


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueueItem(BaseModel):
    case_id: str
    priority_score: float = Field(description="a score, not a probability")
    status: str


class TransitionIn(_In):
    to: Literal["INVESTIGATING", "PENDING_REVIEW"]
    reason: str = Field(min_length=1, max_length=500)


class BudgetIn(_In):
    max_steps: int = Field(gt=0, le=30)
    max_seconds: float = Field(gt=0, le=1800)
    max_tokens: int = Field(ge=0, le=200_000)
    max_cost_usd: float = Field(ge=0, le=10)


class RunIn(_In):
    policy: Literal["fixed_pipeline", "rule_adaptive"] = "rule_adaptive"
    budget: BudgetIn | None = None


class RunAccepted(BaseModel):
    run_id: str
    job_id: str
    status: str = "QUEUED"


class ClosureRequestIn(_In):
    justification: str = Field(min_length=1, max_length=2000)


class RequestCreated(BaseModel):
    request_id: str


class DecisionIn(_In):
    approve: bool
    reason: str = Field(min_length=1, max_length=500)


class DecisionOut(BaseModel):
    approval_id: str


class CloseIn(_In):
    approval_id: str
    disposition: Disposition
    reason: str = Field(min_length=1, max_length=500)


class FeedbackBody(_In):
    case_id: str
    target_type: TargetType
    target_id: str = Field(min_length=1, max_length=200)
    action: FeedbackAction
    reason: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    # no `source`: the API always records HUMAN. Simulated feedback is created only by trusted code.

    @field_validator("payload")
    @classmethod
    def _bounded(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 20 or len(json.dumps(v, default=str)) > 8_000:
            raise ValueError("payload is limited to 20 keys and 8 KB")
        return v


class FeedbackOut(BaseModel):
    feedback_id: str
    validation_status: ValidationStatus
    failed_rules: list[str]


class SecondReviewIn(_In):
    status: Literal["VALIDATED", "QUARANTINED"]
    note: str = Field(min_length=1, max_length=2000)


class ContradictionStatusIn(_In):
    status: ContradictionStatus
    note: str = Field(min_length=1, max_length=2000)


class LinkDecisionIn(_In):
    action: Literal["confirm", "reject", "reopen"]
    reason: str = Field(min_length=1, max_length=500)


class LinkDecisionOut(BaseModel):
    link_id: str
    action: str
    reverification_runs: dict[str, str]
    reverification_failed_cases: list[str] = Field(
        default_factory=list, description="cases where queuing failed; '*' = lookup failed"
    )


class ExportIn(_In):
    approval_id: str
    case_id: str | None = None


class ExportRequestIn(_In):
    justification: str = Field(min_length=1, max_length=2000)
    case_id: str | None = None


class ExportRequestOut(BaseModel):
    request_id: str
    manifest_hash: str = Field(description="the approval covers exactly this content")
    n_records: int
    n_simulated: int


class ExportOut(BaseModel):
    export_id: str
    manifest_hash: str
    rules_version: str
    n_records: int
    n_simulated: int = Field(description="items that are simulated, not human; always report it")
    records: list[dict[str, Any]]


class AuditEvent(BaseModel):
    event_id: int
    ts: str
    actor_id: str
    action: str
    object_type: str
    object_id: str
