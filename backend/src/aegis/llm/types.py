"""Types of the LLM gateway (D-16). Everything that crosses the gateway boundary is a typed,
validated model; provider output is untrusted text until it has passed schema validation."""

from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from aegis.common.errors import AegisError

T = TypeVar("T", bound=BaseModel)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LlmError(AegisError):
    pass


class ReplayMiss(LlmError):
    """REPLAY mode and no recorded response for the request key (fails visibly, never guesses)."""


class SchemaViolation(LlmError):
    """The model never produced valid structured output within the repair limit."""


class ProviderUnavailable(LlmError):
    """Provider failed after bounded retries, or timed out."""


class PseudonymLeak(LlmError):
    """A registered sensitive value survived pseudonymisation; the call is refused."""


class FreeTextBlocked(LlmError):
    """A LIVE call carried free text the system cannot prove is pseudonymised (D-29)."""


class ProviderError(LlmError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class GatewayMode(StrEnum):
    LIVE = "LIVE"  # call the provider; nothing stored for replay
    RECORD = "RECORD"  # call the provider on a miss; store every response; serve hits from store
    REPLAY = "REPLAY"  # never call the provider; a miss is an error


class CallStatus(StrEnum):
    OK = "OK"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    TIMEOUT = "TIMEOUT"
    REPLAY_MISS = "REPLAY_MISS"
    BUDGET_REFUSED = "BUDGET_REFUSED"
    LEAK_REFUSED = "LEAK_REFUSED"
    FREE_TEXT_REFUSED = "FREE_TEXT_REFUSED"


class ProviderRequest(_Frozen):
    model_id: str
    system: str
    user: str
    max_tokens: int
    timeout_s: float
    prompt_id: str
    prompt_version: str
    temperature: float = 0.0
    json_schema: dict[str, Any] | None = None


class ProviderResponse(_Frozen):
    text: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    model_id: str


class StructuredRequest(_Frozen):
    """One logical LLM task. `variables` may contain clear-text sensitive values; the gateway
    pseudonymises them before anything reaches a provider."""

    prompt_id: str
    prompt_version: str | None = None
    role: str  # key into LlmConfig.models; the model id itself lives in config only
    variables: dict[str, Any]
    sensitive: dict[str, list[str]] = Field(default_factory=dict)  # kind -> clear values
    agent: str = "unknown"
    run_id: str | None = None
    step_id: str | None = None
    max_tokens: int | None = None
    # Names of `variables` that hold text written by people or taken from documents (names,
    # notes, narratives). The pseudonymiser can only mask values it knows, so a LIVE call that
    # carries such text is refused unless the gateway was explicitly built to allow it (D-29).
    free_text: tuple[str, ...] = ()


class GatewayResult[M: BaseModel](_Frozen):
    parsed: M
    raw_text: str  # exactly what the provider returned (pseudonymised form)
    model_id: str
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    call_ids: tuple[str, ...]
    repair_rounds: int
    from_replay: bool
