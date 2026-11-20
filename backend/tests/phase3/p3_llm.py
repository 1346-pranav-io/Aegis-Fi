"""LLM gateway test kit: config, prompt registry, stub provider, gateway factory."""

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from aegis.llm.budget import BudgetTracker
from aegis.llm.config import LlmConfig, ModelSpec
from aegis.llm.gateway import LlmGateway
from aegis.llm.prompts import PromptRegistry, parse_prompt
from aegis.llm.providers import Provider, ScriptedProvider
from aegis.llm.pseudonym import Pseudonymiser
from aegis.llm.records import InMemoryCallRecorder
from aegis.llm.replay import RecordStore
from aegis.llm.types import GatewayMode, ProviderRequest

KEY = b"test-only-pseudonym-key-0123456789"

PROMPT_TEXT = """id: test.echo
version: 1
---system
You summarise records.
---user
Records: {{records}}
Question: {{question}}
"""


class Out(BaseModel):
    answer: str
    count: int


def config(**kw: Any) -> LlmConfig:
    base: dict[str, Any] = {
        "models": {
            "investigation": ModelSpec(
                model_id="stub-model-A", price_in_per_1k_usd=1.0, price_out_per_1k_usd=2.0
            ),
            "verification": ModelSpec(model_id="stub-model-B"),
        },
        "backoff_base_s": 0.0,
        "timeout_s": 5.0,
    }
    base.update(kw)
    return LlmConfig(**base)


def registry() -> PromptRegistry:
    r = PromptRegistry()
    r.add(parse_prompt(PROMPT_TEXT))
    return r


def ok_json(answer: str = "fine", count: int = 1) -> str:
    return json.dumps({"answer": answer, "count": count})


def gateway(
    script: Callable[[ProviderRequest], str | Exception] | None = None,
    *,
    mode: GatewayMode = GatewayMode.RECORD,
    store: RecordStore | None = None,
    provider: Provider | None = None,
    budget: BudgetTracker | None = None,
    cfg: LlmConfig | None = None,
    delay_s: float = 0.0,
) -> tuple[LlmGateway, ScriptedProvider | None, InMemoryCallRecorder]:
    prov = provider or (
        ScriptedProvider(script or (lambda _: ok_json()), delay_s=delay_s)
        if mode is not GatewayMode.REPLAY
        else None
    )
    rec = InMemoryCallRecorder()
    gw = LlmGateway(
        config=cfg or config(),
        prompts=registry(),
        pseudonymiser=Pseudonymiser(KEY),
        mode=mode,
        provider=prov,
        store=store,
        recorder=rec,
        budget=budget,
        sleep=lambda _s: None,
    )
    return gw, prov if isinstance(prov, ScriptedProvider) else None, rec
