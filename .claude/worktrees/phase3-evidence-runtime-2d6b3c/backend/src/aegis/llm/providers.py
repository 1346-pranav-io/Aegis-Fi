"""Provider interface and the providers that exist in Phase 3.

Only `ScriptedProvider` (deterministic stub) exists. NO real provider is implemented and NO
Anthropic SDK is installed: a live Claude call needs the owner to confirm the pseudonymisation
design first (decisions.md Q-02 / D-27 hard gate).

TODO(owner-gate Q-02): add `AnthropicProvider` HERE (and only here - `aegis.llm` is the sole
package allowed to import the SDK, enforced by import-linter). It must read the API key from the
environment, take model ids from `LlmConfig`, and honour `ProviderRequest.timeout_s`.
"""

from collections.abc import Callable
from typing import Protocol

from aegis.llm.types import ProviderError, ProviderRequest, ProviderResponse


class Provider(Protocol):
    name: str

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


Script = Callable[[ProviderRequest], str | Exception]


class ScriptedProvider:
    """Deterministic stub: `script(request)` returns the response text (or an exception to
    raise). Token counts are a crude estimate (characters / 4); cost is whatever the config
    prices say. Results describe the harness, never a real model's behaviour."""

    name = "scripted-stub"

    def __init__(self, script: Script, *, delay_s: float = 0.0) -> None:
        self._script = script
        self._delay = delay_s
        self.requests: list[ProviderRequest] = []  # everything the "provider" ever received

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        import time

        self.requests.append(request)
        if self._delay:
            time.sleep(self._delay)
        out = self._script(request)
        if isinstance(out, Exception):
            raise out
        return ProviderResponse(
            text=out,
            tokens_in=(len(request.system) + len(request.user)) // 4,
            tokens_out=len(out) // 4,
            model_id=request.model_id,
        )


def queue_script(responses: list[str | Exception]) -> Script:
    """Script that answers with the given items in order; running out is a visible error."""
    items = list(responses)

    def script(_: ProviderRequest) -> str | Exception:
        if not items:
            return ProviderError("scripted provider has no more responses", retryable=False)
        return items.pop(0)

    return script
