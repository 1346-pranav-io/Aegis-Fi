"""In-memory pipeline harness for the Phase 3 fixture experiments (stub LLM only)."""

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from aegis.domain.agents import Budget
from aegis.eval.phase3.fixture_llm import FixtureLLM, StubConfig
from aegis.eval.phase3.fixtures import AS_OF, SUBJECT, FixtureCase
from aegis.llm.config import LlmConfig, ModelSpec
from aegis.llm.gateway import LlmGateway
from aegis.llm.providers import ScriptedProvider
from aegis.llm.pseudonym import Pseudonymiser
from aegis.llm.records import InMemoryCallRecorder
from aegis.llm.replay import InMemoryRecordStore
from aegis.llm.types import GatewayMode
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
)
from aegis.runtime.state import InvestigationState
from aegis.runtime.wiring import default_prompts, default_registry, default_schemas

CASE_ID = "case_fixture"
KEY = b"phase3-fixture-experiments-key-0001"  # local, non-secret: fixtures only


@dataclass
class Run:
    state: InvestigationState
    backend: InMemoryBackend
    llm: FixtureLLM
    provider: ScriptedProvider


def llm_config() -> LlmConfig:
    return LlmConfig(
        models={
            "investigation": ModelSpec(model_id="fixture-stub-investigation"),
            "verification": ModelSpec(model_id="fixture-stub-verification"),
            "reporting": ModelSpec(model_id="fixture-stub-reporting"),
        },
        backoff_base_s=0.0,
    )


def run_case(
    case: FixtureCase,
    policy: OrchestrationPolicy,
    stub: StubConfig,
    *,
    use_llm_verifier: bool = True,
    budget: Budget | None = None,
) -> Run:
    llm = FixtureLLM(stub)
    provider = ScriptedProvider(llm)
    gateway = LlmGateway(
        config=llm_config(), prompts=default_prompts(), pseudonymiser=Pseudonymiser(KEY),
        mode=GatewayMode.RECORD, provider=provider, store=InMemoryRecordStore(),
        recorder=InMemoryCallRecorder(), sleep=lambda _s: None,
    )  # fmt: skip
    source = FixtureSource(case.txns)
    backend = InMemoryBackend(source.leaf_hashes())
    services = RuntimeServices(
        backend=backend, leaves=source.leaf_hashes(), source=source,
        cases=FixtureCases({CASE_ID: CaseSummary(case_id=CASE_ID, subject_ref=SUBJECT,
                                                 status="OPEN", reason_codes=case.reason_codes)}),
        er=FakeERLinkReader(), approvals=InMemoryApprovals(), audit=InMemoryAudit(), llm=gateway,
        schemas=default_schemas(), clock=lambda: 0.0,
    )  # fmt: skip
    runner = InvestigationRunner(
        services, default_registry(use_llm=use_llm_verifier), policy, InMemorySaver()
    )
    rid = runner.start(
        case_id=CASE_ID, as_of=AS_OF,
        budget=budget or Budget(max_steps=20, max_seconds=600, max_tokens=10**7, max_cost_usd=100),
    )  # fmt: skip
    state = runner.run(rid)
    return Run(state, backend, llm, provider)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a proportion; (nan, nan) when n == 0."""
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (c - h) / d), min(1.0, (c + h) / d)


def fmt_rate(k: int, n: int) -> str:
    if n == 0:
        return "n/a (0)"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.3f} [{lo:.3f}, {hi:.3f}]"


def stored_claim_status(run: Run) -> dict[str, str]:
    return {
        c.draft.text: (c.status.value if c.status else "DRAFT")
        for c in run.backend.evidence.list_claims(CASE_ID)
    }


def report_findings(run: Run) -> set[str]:
    versions = run.backend.reports.versions(CASE_ID)
    if not versions:
        return set()
    body: dict[str, Any] = run.backend.reports.get(versions[-1][0])["body"]
    return {i["text"] for i in body["findings"]["items"]}
