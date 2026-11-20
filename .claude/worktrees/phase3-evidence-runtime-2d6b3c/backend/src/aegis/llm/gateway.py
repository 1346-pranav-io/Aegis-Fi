"""The single LLM gateway (D-16, NFR-SEC-05). Nothing else may call a provider.

Per call it: pseudonymises -> renders the versioned prompt (data fenced as untrusted) -> refuses
if a registered identifier survived -> checks the budget -> replays or calls the provider with a
timeout and bounded retries -> parses JSON -> validates against the Pydantic output model with a
bounded repair loop -> restores pseudonyms locally -> records every attempt.
"""

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from pydantic import ValidationError
from ulid import ULID

from aegis.common.errors import BudgetExceeded
from aegis.common.hashing import content_hash
from aegis.common.logging import get_logger
from aegis.common.timeutil import utcnow
from aegis.llm.budget import BudgetTracker
from aegis.llm.config import LlmConfig, ModelSpec
from aegis.llm.prompts import PromptRegistry
from aegis.llm.providers import Provider
from aegis.llm.pseudonym import Pseudonymiser
from aegis.llm.records import CallRecord, CallRecorder, InMemoryCallRecorder
from aegis.llm.replay import InMemoryRecordStore, RecordedResponse, RecordStore, request_key
from aegis.llm.types import (
    CallStatus,
    GatewayMode,
    GatewayResult,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderUnavailable,
    PseudonymLeak,
    ReplayMiss,
    SchemaViolation,
    StructuredRequest,
    T,
)

log = get_logger("aegis.llm")
_BLOCK = re.compile(r'<data name="\w+">\n(.*?)\n</data>', re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _extract_json(text: str) -> Any:
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a != -1 and b > a:
            return json.loads(t[a : b + 1])
        raise


class LlmGateway:
    def __init__(
        self,
        *,
        config: LlmConfig,
        prompts: PromptRegistry,
        pseudonymiser: Pseudonymiser,
        mode: GatewayMode = GatewayMode.REPLAY,
        provider: Provider | None = None,
        store: RecordStore | None = None,
        recorder: CallRecorder | None = None,
        budget: BudgetTracker | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if mode is not GatewayMode.REPLAY and provider is None:
            raise ValueError(f"mode {mode.value} needs a provider")
        if mode is not GatewayMode.LIVE and store is None:
            store = InMemoryRecordStore()
        self._cfg, self._prompts, self._ps = config, prompts, pseudonymiser
        self._mode, self._provider, self._store = mode, provider, store
        self.recorder: CallRecorder = recorder or InMemoryCallRecorder()
        self.budget = budget or BudgetTracker(max_tokens=10**9, max_cost_usd=10**9)
        self._sleep = sleep
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aegis-llm")

    @property
    def pseudonymiser(self) -> Pseudonymiser:
        return self._ps

    # ------------------------------------------------------------------ public
    def generate_structured(
        self, req: StructuredRequest, out_model: type[T], *, budget: BudgetTracker | None = None
    ) -> GatewayResult[T]:
        budget = budget or self.budget
        prompt = self._prompts.get(req.prompt_id, req.prompt_version)
        spec = self._cfg.model_for(req.role)
        self._ps.register_all(req.sensitive)
        system, user = prompt.render(self._ps.apply(req.variables))
        system += "\n\nOUTPUT SCHEMA (JSON Schema):\n" + json.dumps(
            out_model.model_json_schema(), sort_keys=True
        )
        max_tokens = req.max_tokens or self._cfg.max_output_tokens
        ctx = _Ctx(req, prompt.version, prompt.sha256, spec)

        # only the data blocks are scanned: template text is static, trusted prose
        leaked = self._ps.leaks("\n".join(_BLOCK.findall(user)))
        if leaked:
            msg = f"{len(leaked)} registered identifier(s) survived pseudonymisation"
            self._rec(ctx, "", 0, 0, CallStatus.LEAK_REFUSED, 0, None, None, 0, False, msg)
            raise PseudonymLeak(f"{len(leaked)} registered identifier(s) not pseudonymised")

        call_ids: list[str] = []
        tokens_in = tokens_out = 0
        cost = 0.0
        user_round = user
        last_err = ""
        for rnd in range(self._cfg.max_repair_rounds + 1):
            input_hash = content_hash({"system": system, "user": user_round})
            key = request_key(
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
                model_id=spec.model_id,
                input_hash=input_hash,
            )
            ctx.set_round(rnd, key, input_hash)
            est = (len(system) + len(user_round)) // 4 + max_tokens
            try:
                budget.check(est)
            except BudgetExceeded as e:
                cid = self._rec(ctx, "", 0, 0, CallStatus.BUDGET_REFUSED, 0, None, None, 0,
                                False, str(e))  # fmt: skip
                call_ids.append(cid)
                raise
            resp, replayed, attempts, ms = self._obtain(
                ctx, key, spec, system, user_round, max_tokens, call_ids
            )
            c = (
                resp.tokens_in * spec.price_in_per_1k_usd
                + resp.tokens_out * spec.price_out_per_1k_usd
            ) / 1000.0
            budget.charge(resp.tokens_in + resp.tokens_out, c)
            tokens_in += resp.tokens_in
            tokens_out += resp.tokens_out
            cost += c
            try:
                data = self._ps.restore(_extract_json(resp.text))
                parsed = out_model.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = self._ps.apply(str(e))[:1500]
                cid = self._rec(ctx, resp.text, resp.tokens_in, resp.tokens_out,
                                CallStatus.SCHEMA_INVALID, attempts, c, resp.text, ms, replayed,
                                last_err)  # fmt: skip
                call_ids.append(cid)
                if rnd == self._cfg.max_repair_rounds:
                    raise SchemaViolation(
                        f"{req.prompt_id}: no valid output after {rnd + 1} attempt(s)"
                    ) from e
                user_round = (
                    user
                    + '\n\nYour previous answer was not valid.\n<data name="previous_answer">\n'
                    + resp.text.replace("</data", "<\\/data")
                    + '\n</data>\n<data name="validation_error">\n'
                    + last_err.replace("</data", "<\\/data")
                    + "\n</data>\nReturn corrected JSON only."
                )
                continue
            cid = self._rec(ctx, resp.text, resp.tokens_in, resp.tokens_out, CallStatus.OK,
                            attempts, c, resp.text, ms, replayed, None)  # fmt: skip
            call_ids.append(cid)
            return GatewayResult[T](
                parsed=parsed,
                raw_text=resp.text,
                model_id=spec.model_id,
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                call_ids=tuple(call_ids),
                repair_rounds=rnd,
                from_replay=replayed,
            )
        raise SchemaViolation(last_err)  # unreachable; keeps type-checkers honest

    # ---------------------------------------------------------------- internals
    def _obtain(
        self,
        ctx: "_Ctx",
        key: str,
        spec: ModelSpec,
        system: str,
        user: str,
        max_tokens: int,
        call_ids: list[str],
    ) -> tuple[ProviderResponse, bool, int, int]:
        """(response, from_replay, provider attempts, duration ms)."""
        if self._store is not None and self._mode is not GatewayMode.LIVE:
            hit = self._store.get(key)
            if hit is not None:
                return (
                    ProviderResponse(
                        text=hit.text,
                        tokens_in=hit.tokens_in,
                        tokens_out=hit.tokens_out,
                        model_id=hit.model_id,
                    ),
                    True,
                    0,
                    0,
                )
        if self._mode is GatewayMode.REPLAY:
            cid = self._rec(ctx, "", 0, 0, CallStatus.REPLAY_MISS, 0, None, None, 0, True,
                            "no recorded response for request key")  # fmt: skip
            call_ids.append(cid)
            raise ReplayMiss(f"no recorded response for {ctx.req.prompt_id} (key {key[:12]}...)")
        if self._provider is None:
            raise ProviderUnavailable("no provider configured")
        preq = ProviderRequest(
            model_id=spec.model_id,
            system=system,
            user=user,
            max_tokens=max_tokens,
            timeout_s=self._cfg.timeout_s,
            prompt_id=ctx.req.prompt_id,
            prompt_version=ctx.prompt_version,
        )
        t0 = time.perf_counter()
        last: Exception | None = None
        for attempt in range(1, self._cfg.max_retries + 2):
            try:
                fut = self._pool.submit(self._provider.complete, preq)
                resp = fut.result(timeout=self._cfg.timeout_s)
                ms = int((time.perf_counter() - t0) * 1000)
                if self._store is not None and self._mode is GatewayMode.RECORD:
                    self._store.put(
                        RecordedResponse(
                            key=key,
                            text=resp.text,
                            tokens_in=resp.tokens_in,
                            tokens_out=resp.tokens_out,
                            model_id=resp.model_id,
                        )
                    )
                return resp, False, attempt, ms
            except FutureTimeout as e:
                last = e
                status, retryable = CallStatus.TIMEOUT, True
            except ProviderError as e:
                last = e
                status, retryable = CallStatus.PROVIDER_ERROR, e.retryable
            cid = self._rec(ctx, "", 0, 0, status, attempt, None, None,
                            int((time.perf_counter() - t0) * 1000), False,
                            f"{type(last).__name__}: {last}"[:500])  # fmt: skip
            call_ids.append(cid)
            if not retryable or attempt > self._cfg.max_retries:
                raise ProviderUnavailable(f"{ctx.req.prompt_id}: {last}") from last
            self._sleep(self._cfg.backoff_base_s * 2 ** (attempt - 1))
        raise ProviderUnavailable("unreachable")  # pragma: no cover

    def _rec(
        self,
        ctx: "_Ctx",
        _text: str,
        tin: int,
        tout: int,
        status: CallStatus,
        attempt: int,
        cost: float | None,
        response_text: str | None,
        ms: int,
        replayed: bool,
        error: str | None,
    ) -> str:
        cid = f"call_{ULID()}"  # prefix not in common.ids (frozen); local to the gateway
        self.recorder.record(
            CallRecord(
                call_id=cid,
                run_id=ctx.req.run_id,
                step_id=ctx.req.step_id,
                agent=ctx.req.agent,
                role=ctx.req.role,
                prompt_id=ctx.req.prompt_id,
                prompt_version=ctx.prompt_version,
                prompt_hash=ctx.prompt_hash,
                model_id=ctx.spec.model_id,
                provider=getattr(self._provider, "name", "none"),
                mode=self._mode.value,
                request_key=ctx.key,
                input_hash=ctx.input_hash,
                repair_round=ctx.round,
                attempt=attempt,
                status=status,
                from_replay=replayed,
                tokens_in=tin,
                tokens_out=tout,
                cost_usd=cost or 0.0,
                duration_ms=ms,
                response_text=response_text,
                error=error,
                created_at=utcnow(),
            )
        )
        return cid


class _Ctx:
    def __init__(self, req: StructuredRequest, version: str, phash: str, spec: ModelSpec) -> None:
        self.req, self.prompt_version, self.prompt_hash, self.spec = req, version, phash, spec
        self.round, self.key, self.input_hash = 0, "", ""

    def set_round(self, rnd: int, key: str, input_hash: str) -> None:
        self.round, self.key, self.input_hash = rnd, key, input_hash
