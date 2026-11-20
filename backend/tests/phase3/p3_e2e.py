"""End-to-end kit: real capabilities + gateway + FixtureLLM stub, in memory."""

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from p3_llm import KEY, config
from p3_runtime import CASE, SUBJECT, default_txns

from aegis.eval.phase3.fixture_llm import FixtureLLM, StubConfig
from aegis.llm.config import LlmConfig, ModelSpec
from aegis.llm.gateway import LlmGateway
from aegis.llm.providers import Provider, ScriptedProvider
from aegis.llm.pseudonym import Pseudonymiser
from aegis.llm.records import InMemoryCallRecorder
from aegis.llm.replay import InMemoryRecordStore, RecordStore
from aegis.llm.types import GatewayMode, ProviderRequest
from aegis.runtime.policies import OrchestrationPolicy
from aegis.runtime.runner import InvestigationRunner
from aegis.runtime.services import (
    CaseSummary,
    FakeERLinkReader,
    FixtureCases,
    FixtureSource,
    InMemoryApprovals,
    InMemoryAudit,
    InMemoryBackend,
    RuntimeServices,
    SourceTxn,
)
from aegis.runtime.wiring import default_prompts, default_registry, default_schemas


def llm_config() -> LlmConfig:
    return config(
        models={
            "investigation": ModelSpec(model_id="stub-inv"),
            "verification": ModelSpec(model_id="stub-ver"),
            "reporting": ModelSpec(model_id="stub-rep"),
        }
    )


class E2E:
    def __init__(
        self,
        policy: OrchestrationPolicy,
        stub: StubConfig | None = None,
        *,
        script: Callable[[ProviderRequest], str | Exception] | None = None,
        use_llm: bool = True,
        txns: list[SourceTxn] | None = None,
        reason_codes: tuple[str, ...] = ("CYCLE",),
        mode: GatewayMode = GatewayMode.RECORD,
        store: RecordStore | None = None,
        provider: Provider | None = None,
        checkpointer: Any = "memory",
    ) -> None:
        self.fixture_llm = FixtureLLM(stub or StubConfig())
        self.provider: Provider | None = provider or (
            ScriptedProvider(script or self.fixture_llm) if mode is not GatewayMode.REPLAY else None
        )
        self.store = store if store is not None else InMemoryRecordStore()
        self.recorder = InMemoryCallRecorder()
        self.gateway = LlmGateway(
            config=llm_config(),
            prompts=default_prompts(),
            pseudonymiser=Pseudonymiser(KEY),
            mode=mode,
            provider=self.provider,
            store=self.store,
            recorder=self.recorder,
            sleep=lambda _s: None,
        )
        self.source = FixtureSource(txns if txns is not None else default_txns())
        self.backend = InMemoryBackend(self.source.leaf_hashes())
        self.approvals, self.audit = InMemoryApprovals(), InMemoryAudit()
        self.services = RuntimeServices(
            backend=self.backend,
            leaves=self.source.leaf_hashes(),
            source=self.source,
            cases=FixtureCases(
                {
                    CASE: CaseSummary(
                        case_id=CASE, subject_ref=SUBJECT, status="OPEN", reason_codes=reason_codes
                    )
                }  # fmt: skip
            ),
            er=FakeERLinkReader(),
            approvals=self.approvals,
            audit=self.audit,
            llm=self.gateway,
            schemas=default_schemas(),
            clock=lambda: 0.0,
        )
        self.registry = default_registry(use_llm=use_llm)
        self.saver = InMemorySaver() if checkpointer == "memory" else checkpointer
        self.runner = InvestigationRunner(self.services, self.registry, policy, self.saver)

    def run(self, budget: Any = None) -> Any:
        from datetime import UTC, datetime

        from p3_runtime import big_budget

        rid = self.runner.start(
            case_id=CASE, as_of=datetime(2022, 9, 10, tzinfo=UTC), budget=budget or big_budget()
        )
        return self.runner.run(rid)

    # convenience views --------------------------------------------------------------
    def claims(self) -> list[Any]:
        return self.backend.evidence.list_claims(CASE)

    def latest_report(self) -> dict[str, Any]:
        rid, _ = self.backend.reports.versions(CASE)[-1]
        return dict(self.backend.reports.get(rid)["body"])

    def provider_requests(self) -> list[ProviderRequest]:
        return list(getattr(self.provider, "requests", []))
