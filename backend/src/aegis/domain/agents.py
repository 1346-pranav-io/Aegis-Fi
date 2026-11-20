"""Runtime agent contract (AGENT_SPECIFICATIONS section 0). Shared by the runtime (Phase 3) and the
Contradiction agent / entity-resolution proposals (Phase 4)."""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis.common.timeutil import require_utc
from aegis.domain.evidence import ContradictionDraft, EvidenceDraft


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentName(StrEnum):
    ORCHESTRATOR = "ORCHESTRATOR"
    TRANSACTION = "TRANSACTION"
    GRAPH = "GRAPH"
    BEHAVIOUR = "BEHAVIOUR"
    INTELLIGENCE = "INTELLIGENCE"
    INVESTIGATION = "INVESTIGATION"
    VERIFICATION = "VERIFICATION"
    CONTRADICTION = "CONTRADICTION"
    REPORTING = "REPORTING"


class Permission(StrEnum):
    """Permission classes (AGENT_ARCHITECTURE 5). There is deliberately no WRITE_SOURCE."""

    READ_SOURCE = "READ_SOURCE"
    READ_DERIVED = "READ_DERIVED"
    READ_EVIDENCE = "READ_EVIDENCE"
    WRITE_EVIDENCE = "WRITE_EVIDENCE"
    WRITE_CLAIM = "WRITE_CLAIM"
    WRITE_PROPOSAL = "WRITE_PROPOSAL"
    CALL_LLM = "CALL_LLM"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"


class Budget(_Frozen):
    max_steps: int = Field(gt=0)
    max_seconds: float = Field(gt=0)
    max_tokens: int = Field(ge=0)
    max_cost_usd: float = Field(ge=0)


class Ref(_Frozen):
    kind: str  # "evidence" | "claim" | "entity" | "er_link"
    id: str


class AgentTask(_Frozen):
    task_id: str
    run_id: str
    case_id: str
    agent: AgentName
    as_of: datetime
    task_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    input_refs: tuple[Ref, ...] = ()
    budget: Budget

    @field_validator("as_of")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return require_utc(v)


def window_anchor(case: Mapping[str, Any], as_of: datetime) -> datetime:
    """End of the analysis window for a case (D-33). `as_of` is knowledge time: what the system
    may know. The anchor is event time: when the alerts are about. Lookbacks end at the anchor,
    never later than `as_of`. A case without an anchor (no alert window) uses `as_of`."""
    raw = case.get("event_anchor")
    if not raw:
        return as_of
    anchor = require_utc(datetime.fromisoformat(str(raw)))
    return min(anchor, as_of)


class ResultStatus(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DENIED = "DENIED"


class AgentError(_Frozen):
    code: str
    message: str
    retryable: bool = False


class Usage(_Frozen):
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0


class ProposalKind(StrEnum):
    ER_LINK = "ER_LINK"
    CONTRADICTION = "CONTRADICTION"
    FOLLOW_UP_TASK = "FOLLOW_UP_TASK"


class Proposal(_Frozen):
    """A suggestion the runtime may act on; it never changes state by itself."""

    kind: ProposalKind
    payload: dict[str, Any]


class AgentResult(_Frozen):
    task_id: str
    status: ResultStatus
    evidence: tuple[EvidenceDraft, ...] = ()
    contradictions: tuple[ContradictionDraft, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    error: AgentError | None = None
    usage: Usage = Usage()
    version: str

    def model_post_init(self, __context: object) -> None:
        if self.status in (ResultStatus.FAILED, ResultStatus.DENIED) and self.error is None:
            raise ValueError("FAILED and DENIED results need an error")


class ToolGateway(Protocol):
    """What a capability may use to read data or create records. The runtime implements it with
    per-agent allow-lists and `as_of` enforcement; tests use a fake. Arguments and results are
    plain JSON-like mappings validated by the gateway against per-tool schemas."""

    def call(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]: ...


class Capability(Protocol):
    """A runtime agent implementation. Capabilities never call each other: only the runtime
    composes them (SYSTEM_ARCHITECTURE section 2)."""

    name: AgentName
    version: str
    permissions: frozenset[Permission]

    def run(self, task: AgentTask, tools: ToolGateway) -> AgentResult: ...


class ExplanationProposer(Protocol):
    """Optional LLM-backed helper (Phase 3 gateway) that Phase 4 may call to *propose* an
    explanation for a contradiction or ER ambiguity. A proposal never changes any status."""

    def propose(self, subject: Mapping[str, Any]) -> Mapping[str, Any] | None: ...
