"""Run state and the single Plan schema shared by every orchestration policy
(AGENT_ARCHITECTURE 3-4). State holds IDs and counters, never evidence blobs."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis.common.timeutil import require_utc
from aegis.domain.agents import AgentName, Budget, ResultStatus


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"


TERMINAL = frozenset(
    {RunStatus.COMPLETE, RunStatus.PARTIAL, RunStatus.BUDGET_EXHAUSTED, RunStatus.FAILED}
)


class Signal(_M):
    """A typed alert-level signal the policy can route on (reason codes from Phase 2)."""

    code: str
    value: float | None = None  # a score or magnitude, never a probability (D-06)


class PlannedTask(_M):
    agent: AgentName
    task_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    rationale_code: str  # why the policy chose it (auditable, comparable across policies)


class StopDecision(_M):
    reason_code: str  # PLAN_EXHAUSTED | MARGINAL_GAIN | BUDGET | NO_APPLICABLE_AGENT
    detail: str = ""


class Plan(_M):
    """Identical for every policy, so policies are directly comparable (research contribution 1)."""

    policy_name: str
    policy_version: str
    tasks: list[PlannedTask]
    skipped: list[PlannedTask] = Field(default_factory=list)  # considered but not scheduled
    stop: StopDecision | None = None


class StepSummary(_M):
    seq: int
    task_id: str
    agent: AgentName
    task_type: str
    status: ResultStatus
    evidence_ids: list[str] = Field(default_factory=list)
    new_evidence: int = 0  # evidence items not seen before in this run (marginal gain)
    claim_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    report_ids: list[str] = Field(default_factory=list)
    reused: bool = False  # True when a resumed run reused the recorded step
    tokens: int = 0
    cost_usd: float = 0.0


class InvestigationState(_M):
    run_id: str
    case_id: str
    as_of: datetime
    subject_ref: str
    policy_name: str
    policy_version: str
    budget: Budget
    signals: list[Signal] = Field(default_factory=list)
    status: RunStatus = RunStatus.QUEUED
    planned: bool = False
    pending: list[PlannedTask] = Field(default_factory=list)
    skipped: list[PlannedTask] = Field(default_factory=list)
    steps: list[StepSummary] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    report_ids: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    seconds_used: float = 0.0
    stop_reason: str | None = None
    final_reason: str | None = None

    @field_validator("as_of")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return require_utc(v)

    def task_id(self, seq: int) -> str:
        return f"{self.run_id}:t{seq:03d}"

    def has(self, agent: AgentName) -> bool:
        return any(s.agent is agent for s in self.steps)

    def last_status(self, agent: AgentName) -> ResultStatus | None:
        for s in reversed(self.steps):
            if s.agent is agent:
                return s.status
        return None
