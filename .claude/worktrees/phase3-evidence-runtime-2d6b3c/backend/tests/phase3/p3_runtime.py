"""Runtime test kit: fixture source, in-memory services, light fake capabilities."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from aegis.domain.agents import (
    AgentName,
    AgentResult,
    AgentTask,
    Budget,
    Permission,
    ResultStatus,
    ToolGateway,
)
from aegis.domain.evidence import (
    EvidenceDraft,
    EvidenceInput,
    EvidenceType,
    InputRefType,
    Producer,
    ProducerKind,
)
from aegis.runtime.policies import OrchestrationPolicy
from aegis.runtime.registry import CapabilityRegistry
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

AS_OF = datetime(2022, 9, 10, tzinfo=UTC)
CASE = "case_1"
SUBJECT = "bank1|ACCT_A"


def txn(
    i: int, o: str, d: str, hours: float, amount: str = "100", *, late: bool = False
) -> SourceTxn:
    et = AS_OF - timedelta(hours=hours)
    return SourceTxn(
        txn_id=f"txn_{i}", content_hash=f"hash_txn_{i}", event_time=et,
        ingested_at=AS_OF + timedelta(days=1) if late else et + timedelta(minutes=1),
        origin_ref=o, dest_ref=d, amount=Decimal(amount), currency="USD",
    )  # fmt: skip


def default_txns() -> list[SourceTxn]:
    return [
        txn(1, SUBJECT, "bank1|B", 30), txn(2, "bank1|B", SUBJECT, 28, "250"),
        txn(3, SUBJECT, "bank1|C", 20), txn(4, "bank1|C", "bank1|D", 10, "75"),
    ]  # fmt: skip


def big_budget(**kw: Any) -> Budget:
    base: dict[str, Any] = dict(max_steps=20, max_seconds=600, max_tokens=100_000, max_cost_usd=100)
    base.update(kw)
    return Budget(**base)


class FnCap:
    """Fake capability: behaviour supplied as a function."""

    def __init__(
        self,
        name: AgentName,
        fn: Callable[[AgentTask, ToolGateway], AgentResult],
        perms: frozenset[Permission],
        version: str = "t1",
    ) -> None:
        self.name, self.version, self.permissions, self._fn = name, version, perms, fn
        self.runs = 0

    def run(self, task: AgentTask, tools: ToolGateway) -> AgentResult:
        self.runs += 1
        return self._fn(task, tools)


def ok(task: AgentTask, **kw: Any) -> AgentResult:
    return AgentResult(task_id=task.task_id, status=ResultStatus.OK, version="t1", **kw)


def tool_evidence(task: AgentTask, tag: str, txn_ids: list[str]) -> EvidenceDraft:
    ins = tuple(
        EvidenceInput(ref_type=InputRefType.SOURCE, ref_id=t, ref_hash=f"hash_{t}") for t in txn_ids
    )
    return EvidenceDraft(
        case_id=task.case_id, evidence_type=EvidenceType.TXN_AGGREGATE,
        content={"tag": tag, "n": len(txn_ids)}, summary_text=f"{tag} over {len(txn_ids)} txns",
        producer=Producer(kind=ProducerKind.TOOL, name=tag, version="1"), as_of=task.as_of,
        window_start=task.as_of - timedelta(days=3), window_end=task.as_of, inputs=ins,
    )  # fmt: skip


def specialist(name: AgentName, tag: str) -> FnCap:
    def fn(task: AgentTask, tools: ToolGateway) -> AgentResult:
        rows = tools.call(
            "get_transactions" if name is AgentName.TRANSACTION else "get_edges",
            {"start": task.as_of - timedelta(days=3), "end": task.as_of},
        )["transactions"]
        return ok(task, evidence=(tool_evidence(task, tag, [r["txn_id"] for r in rows[:2]]),))

    return FnCap(name, fn, frozenset({Permission.READ_SOURCE, Permission.READ_DERIVED}))


def investigator() -> FnCap:
    def fn(task: AgentTask, tools: ToolGateway) -> AgentResult:
        ids = [r.id for r in task.input_refs if r.kind == "evidence"]
        tools.call("get_evidence", {"ids": ids})
        tools.call(
            "create_claim",
            {
                "structure": {"subject_ref": SUBJECT, "predicate": "has_evidence",
                              "object_value": len(ids)},
                "text": f"{len(ids)} evidence items exist", "claim_kind": "AGGREGATE",
                "citations": [{"evidence_id": ids[0], "support_role": "SUPPORTS"}],
            },
        )  # fmt: skip
        return ok(task)

    return FnCap(
        AgentName.INVESTIGATION, fn,
        frozenset({Permission.READ_EVIDENCE, Permission.WRITE_CLAIM, Permission.CALL_LLM}),
    )  # fmt: skip


def verifier() -> FnCap:
    def fn(task: AgentTask, tools: ToolGateway) -> AgentResult:
        ids = [r.id for r in task.input_refs if r.kind == "claim"]
        for c in tools.call("get_claim", {"ids": ids})["claims"]:
            tools.call(
                "write_verification",
                {
                    "claim_id": c["claim_id"], "status": "UNVERIFIABLE",
                    "checks": [{"check_id": "X", "kind": "DETERMINISTIC", "outcome": "NA"}],
                    "rationale": "fake", "verifier_name": "fake", "verifier_version": "1",
                    "inputs_seen_hash": "h",
                },
            )  # fmt: skip
        return ok(task)

    return FnCap(
        AgentName.VERIFICATION, fn,
        frozenset({Permission.READ_EVIDENCE, Permission.WRITE_CLAIM, Permission.CALL_LLM}),
    )  # fmt: skip


def reporter() -> FnCap:
    def fn(task: AgentTask, tools: ToolGateway) -> AgentResult:
        tools.call("create_report_version", {"body": {"findings": [], "n": 1}})
        return ok(task)

    return FnCap(
        AgentName.REPORTING, fn,
        frozenset({Permission.READ_EVIDENCE, Permission.WRITE_PROPOSAL, Permission.CALL_LLM,
                   Permission.REQUEST_APPROVAL}),
    )  # fmt: skip


def all_caps() -> list[FnCap]:
    return [
        specialist(AgentName.TRANSACTION, "txn"),
        specialist(AgentName.GRAPH, "graph"),
        specialist(AgentName.BEHAVIOUR, "behav"),
        investigator(),
        verifier(),
        reporter(),
    ]


class Kit:
    def __init__(
        self,
        policy: OrchestrationPolicy,
        caps: list[FnCap] | None = None,
        *,
        reason_codes: tuple[str, ...] = (),
        txns: list[SourceTxn] | None = None,
        checkpointer: Any = "memory",
    ) -> None:
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
            clock=lambda: 0.0,
        )
        self.registry = CapabilityRegistry()
        self.caps = caps if caps is not None else all_caps()
        for c in self.caps:
            self.registry.register(c)
        self.saver = InMemorySaver() if checkpointer == "memory" else checkpointer
        self.policy = policy
        self.runner = InvestigationRunner(self.services, self.registry, policy, self.saver)

    def start(self, budget: Budget | None = None) -> str:
        return self.runner.start(case_id=CASE, as_of=AS_OF, budget=budget or big_budget())
