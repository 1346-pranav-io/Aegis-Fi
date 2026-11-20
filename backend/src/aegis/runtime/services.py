"""Service protocols the runtime depends on, with in-memory implementations used by unit tests and
fixture experiments. Postgres implementations are in `pg_services.py`.

Everything an agent can reach goes through these via the tool gateway; capabilities never see
them directly."""

import copy
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict
from ulid import ULID

from aegis.common.hashing import content_hash
from aegis.common.ids import new_id
from aegis.domain.agents import AgentName, ResultStatus
from aegis.domain.entities import ERLinkReader, ERLinkView, LinkStatus
from aegis.domain.evidence import InputRefType
from aegis.evidence.store import DictLeafHashes, EvidenceStore, InMemoryEvidenceStore
from aegis.evidence.types import LeafHashes
from aegis.llm.gateway import LlmGateway
from aegis.runtime.state import RunStatus


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ------------------------------------------------------------------ source reader
class SourceTxn(_M):
    txn_id: str
    content_hash: str
    event_time: datetime
    ingested_at: datetime
    origin_ref: str  # source account reference
    dest_ref: str
    amount: Decimal
    currency: str


class SourceReader(Protocol):
    def transactions(
        self,
        *,
        as_of: datetime,
        start: datetime,
        end: datetime,
        account: str | None,
        limit: int,
    ) -> tuple[list[SourceTxn], bool]:
        """Transactions with event_time in [start, end) and ingested_at <= as_of, optionally
        touching `account`; second item = truncated by `limit`. Ordered by (event_time, id)."""
        ...


class FixtureSource:
    """In-memory source used by fixtures and unit tests. Mirrors the SQL reader's as_of rule."""

    def __init__(self, txns: list[SourceTxn]) -> None:
        self.txns = sorted(txns, key=lambda t: (t.event_time, t.txn_id))

    def transactions(
        self,
        *,
        as_of: datetime,
        start: datetime,
        end: datetime,
        account: str | None,
        limit: int,
    ) -> tuple[list[SourceTxn], bool]:
        rows = [
            t
            for t in self.txns
            if t.ingested_at <= as_of
            and start <= t.event_time < end
            and (account is None or account in (t.origin_ref, t.dest_ref))
        ]
        return rows[:limit], len(rows) > limit

    def leaf_hashes(self) -> DictLeafHashes:
        d = DictLeafHashes()
        for t in self.txns:
            d.add(InputRefType.SOURCE, t.txn_id, t.content_hash)
        return d


# ------------------------------------------------------------------- case reader
class CaseSummary(_M):
    case_id: str
    subject_ref: str
    status: str
    reason_codes: tuple[str, ...] = ()
    scores: tuple[float, ...] = ()  # alert scores - scores, not probabilities (D-06)
    # D-33: event time the case's alerts are about (latest alert window end, else alert as_of).
    # Lookback windows are anchored here, not at the knowledge-time `as_of`. None = use as_of.
    event_anchor: datetime | None = None


class CaseReader(Protocol):
    def summary(self, case_id: str) -> CaseSummary: ...


class FixtureCases:
    def __init__(self, cases: dict[str, CaseSummary]) -> None:
        self._c = cases

    def summary(self, case_id: str) -> CaseSummary:
        return self._c[case_id]


# ---------------------------------------------------------------- ER link reader
class FakeERLinkReader:
    """PHASE 3 STAND-IN for Phase 4's ERLinkReader (reconciliation step 1 replaces it)."""

    def __init__(self, links: list[ERLinkView] | None = None) -> None:
        self._l = {link.link_id: link for link in links or []}

    def latest_links(self, link_ids: list[str]) -> list[ERLinkView]:
        return [self._l[i] for i in link_ids if i in self._l]

    def unconfirmed_dependencies(self, link_ids: list[str]) -> list[str]:
        return [
            i for i in link_ids if i not in self._l or self._l[i].status is not LinkStatus.CONFIRMED
        ]


# ------------------------------------------------------------ approvals and audit
class ApprovalRequester(Protocol):
    def request(self, action: str, subject_ref: str, requested_by: str, justification: str) -> str:
        """Creates a PENDING approval request only (D-09); never decides one."""
        ...


class InMemoryApprovals:
    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []

    def request(self, action: str, subject_ref: str, requested_by: str, justification: str) -> str:
        rid = new_id("apr")
        self.requests.append(
            {
                "request_id": rid,
                "action": action,
                "subject_ref": subject_ref,
                "requested_by": requested_by,
                "justification": justification,
            }
        )
        return rid


class AuditSink(Protocol):
    def event(
        self, action: str, object_type: str, object_id: str, details: dict[str, Any]
    ) -> None: ...


class InMemoryAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str, dict[str, Any]]] = []

    def event(self, action: str, object_type: str, object_id: str, details: dict[str, Any]) -> None:
        self.events.append((action, object_type, object_id, details))


# ------------------------------------------------------------------- run storage
class StepRecord(_M):
    step_id: str
    run_id: str
    task_id: str
    seq: int
    agent: AgentName
    task_type: str
    capability_version: str
    status: ResultStatus
    input_hash: str
    output: dict[str, Any]  # ids only: evidence_ids, claim_ids, contradiction_ids, proposals...
    tool_calls: list[dict[str, Any]]
    llm_call_ids: list[str]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int
    error: dict[str, Any] | None = None


class RunStore(Protocol):
    def create_run(
        self,
        *,
        run_id: str,
        case_id: str,
        as_of: datetime,
        policy_name: str,
        policy_version: str,
        budget: dict[str, Any],
        config: dict[str, Any],
    ) -> None: ...
    def run_info(self, run_id: str) -> dict[str, Any]: ...
    def set_status(self, run_id: str, status: RunStatus, reason: str | None = None) -> None: ...
    def status(self, run_id: str) -> RunStatus: ...
    def get_step(self, run_id: str, task_id: str) -> StepRecord | None: ...
    def add_step(self, step: StepRecord) -> None: ...
    def steps(self, run_id: str) -> list[StepRecord]: ...


class InMemoryRunStore:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.status_events: dict[str, list[tuple[RunStatus, str | None]]] = {}
        self._steps: dict[str, list[StepRecord]] = {}

    def create_run(
        self,
        *,
        run_id: str,
        case_id: str,
        as_of: datetime,
        policy_name: str,
        policy_version: str,
        budget: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        self.runs[run_id] = dict(
            run_id=run_id, case_id=case_id, as_of=as_of, policy_name=policy_name,
            policy_version=policy_version, budget=budget, config=config,
        )  # fmt: skip
        self.status_events[run_id] = [(RunStatus.QUEUED, None)]
        self._steps[run_id] = []

    def run_info(self, run_id: str) -> dict[str, Any]:
        return self.runs[run_id]

    def set_status(self, run_id: str, status: RunStatus, reason: str | None = None) -> None:
        cur = self.status_events[run_id][-1][0]
        legal = {
            (RunStatus.QUEUED, RunStatus.RUNNING),
            (RunStatus.QUEUED, RunStatus.FAILED),
            (RunStatus.RUNNING, RunStatus.RUNNING),
            (RunStatus.RUNNING, RunStatus.COMPLETE),
            (RunStatus.RUNNING, RunStatus.PARTIAL),
            (RunStatus.RUNNING, RunStatus.BUDGET_EXHAUSTED),
            (RunStatus.RUNNING, RunStatus.FAILED),
        }
        if (cur, status) not in legal:
            raise ValueError(f"illegal run status change {cur} -> {status}")
        self.status_events[run_id].append((status, reason))

    def status(self, run_id: str) -> RunStatus:
        return self.status_events[run_id][-1][0]

    def get_step(self, run_id: str, task_id: str) -> StepRecord | None:
        return next((s for s in self._steps[run_id] if s.task_id == task_id), None)

    def add_step(self, step: StepRecord) -> None:
        if self.get_step(step.run_id, step.task_id) is not None:
            raise ValueError("a step for this task already exists")
        self._steps[step.run_id].append(step)

    def steps(self, run_id: str) -> list[StepRecord]:
        return sorted(self._steps[run_id], key=lambda s: s.seq)


class ReportStore(Protocol):
    def add_version(
        self, case_id: str, run_id: str | None, body: dict[str, Any], generator: dict[str, Any]
    ) -> tuple[str, int]:
        """Returns (report_id, version). Versions are numbered per case; stored reports are
        immutable, a regenerated report is a new version."""
        ...

    def get(self, report_id: str) -> dict[str, Any]: ...
    def versions(self, case_id: str) -> list[tuple[str, int]]: ...


class InMemoryReportStore:
    def __init__(self) -> None:
        self._r: dict[str, dict[str, Any]] = {}

    def add_version(
        self, case_id: str, run_id: str | None, body: dict[str, Any], generator: dict[str, Any]
    ) -> tuple[str, int]:
        version = 1 + max(
            (r["version"] for r in self._r.values() if r["case_id"] == case_id), default=0
        )
        rid = f"rep_{ULID()}"
        self._r[rid] = dict(
            report_id=rid, case_id=case_id, run_id=run_id, version=version,
            body=copy.deepcopy(body), body_hash=content_hash(body), generator=generator,
        )  # fmt: skip
        return rid, version

    def get(self, report_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._r[report_id])

    def versions(self, case_id: str) -> list[tuple[str, int]]:
        return sorted(
            ((r["report_id"], r["version"]) for r in self._r.values() if r["case_id"] == case_id),
            key=lambda x: x[1],
        )


# ----------------------------------------------------------------------- backend
class Backend(Protocol):
    # read-only properties: concrete backends expose narrower store types (a plain attribute in a
    # Protocol is invariant, which would reject PostgresBackend)
    @property
    def evidence(self) -> EvidenceStore: ...
    @property
    def runs(self) -> RunStore: ...
    @property
    def reports(self) -> ReportStore: ...

    def transaction(self) -> Any:
        """Context manager: commit on success, roll back everything on exception."""
        ...


class InMemoryBackend:
    def __init__(self, leaves: LeafHashes) -> None:
        self.evidence_mem = InMemoryEvidenceStore(leaves)
        self.evidence: EvidenceStore = self.evidence_mem
        self.run_store = InMemoryRunStore()
        self.runs: RunStore = self.run_store
        self.report_store = InMemoryReportStore()
        self.reports: ReportStore = self.report_store

    @contextmanager
    def transaction(self) -> Iterator[None]:
        snap = copy.deepcopy(
            (self.run_store.runs, self.run_store.status_events, self.run_store._steps,
             self.report_store._r)
        )  # fmt: skip
        try:
            with self.evidence_mem.transaction():
                yield
        except BaseException:
            (self.run_store.runs, self.run_store.status_events, self.run_store._steps,
             self.report_store._r) = snap  # fmt: skip
            raise


# ---------------------------------------------------------------- services bundle
@dataclass
class RuntimeServices:
    backend: Backend
    leaves: LeafHashes
    source: SourceReader
    cases: CaseReader
    er: ERLinkReader
    approvals: ApprovalRequester
    audit: AuditSink
    llm: LlmGateway | None = None
    schemas: dict[str, type[BaseModel]] = field(default_factory=dict)
    clock: Callable[[], float] = field(default=time.monotonic)  # seconds; injectable in tests
    fault: Callable[[str], None] | None = None  # test seam: raise to simulate a crash at a point

    def checkpoint(self, point: str) -> None:
        if self.fault is not None:
            self.fault(point)
