"""Submit investigation runs and process them with a worker (FR-API-03, FR-ORC-05).

`submit_run` creates a QUEUED run plus a job in one step and returns at once; a worker claims jobs
(FOR UPDATE SKIP LOCKED, D-03) and runs them to a terminal status. Policy and budget are fixed at
submission and stored with the run. A job whose worker died is re-claimed after its lease expires
and the run resumes from its checkpoint, reusing recorded steps.

Live Claude calls are DISABLED (D-29): the worker runs with no model (`llm=None`: the LLM steps
end DENIED and the run is PARTIAL) or with the labelled fixture stub for demonstrations.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from aegis.common.errors import AegisValidationError, ConflictError, NotFound
from aegis.common.logging import get_logger
from aegis.domain.agents import Budget
from aegis.runtime.checkpoint import postgres_checkpointer
from aegis.runtime.pg_services import JobQueue
from aegis.runtime.pg_wiring import build_postgres_services
from aegis.runtime.policies import POLICIES
from aegis.runtime.runner import InvestigationRunner
from aegis.runtime.state import RunStatus
from aegis.runtime.wiring import default_registry

log = get_logger("aegis.worker")
DEFAULT_BUDGET = Budget(max_steps=12, max_seconds=600, max_tokens=200_000, max_cost_usd=5.0)
USER_POLICIES = ("fixed_pipeline", "rule_adaptive")  # `reverify` is started by the system only


@dataclass(frozen=True)
class SubmittedRun:
    run_id: str
    job_id: str
    as_of: datetime


def _runner(
    conn: psycopg.Connection, policy_name: str, saver: Any, llm: Any = None
) -> InvestigationRunner:
    policy = POLICIES[policy_name]()
    return InvestigationRunner(
        build_postgres_services(conn, llm=llm),
        default_registry(use_llm=llm is not None),
        policy,
        saver,
    )


def submit_run(
    autocommit_conn: psycopg.Connection,
    case_id: str,
    policy: str,
    budget: Budget | None = None,
    *,
    allow_system_policy: bool = False,
    reverify_claim_ids: list[str] | None = None,
) -> SubmittedRun:
    """`autocommit_conn` must be an autocommit connection opened as the application login."""
    if policy not in (*USER_POLICIES, *(["reverify"] if allow_system_policy else [])):
        raise AegisValidationError(f"unknown or not allowed policy {policy!r}")
    if policy in USER_POLICIES:  # one in-flight run per case: no queue flooding
        busy = autocommit_conn.execute(
            "SELECT 1 FROM runtime.run_current WHERE case_id=%s AND policy_name = ANY(%s) "
            "AND status IN ('QUEUED','RUNNING') LIMIT 1",
            (case_id, list(USER_POLICIES)),
        ).fetchone()
        if busy:
            raise ConflictError(f"case {case_id} already has a run in progress")
    row = autocommit_conn.execute("SELECT clock_timestamp()").fetchone()
    if row is None:
        raise RuntimeError("no database clock")
    as_of: datetime = row[0]
    runner = _runner(autocommit_conn, policy, None)
    try:
        run_id = runner.start(
            case_id=case_id,
            as_of=as_of,
            budget=budget or DEFAULT_BUDGET,
            reverify_claim_ids=reverify_claim_ids,
        )
    except KeyError as exc:
        raise NotFound(f"case {case_id}") from exc
    job_id = JobQueue(autocommit_conn).enqueue(run_id)
    return SubmittedRun(run_id, job_id, as_of)


def run_status(conn: psycopg.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT run_id, case_id, policy_name, status, reason, at FROM runtime.run_current "
        "WHERE run_id=%s",
        (run_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"run {run_id}")
    steps = conn.execute(
        "SELECT agent, status FROM runtime.agent_step WHERE run_id=%s ORDER BY seq",
        (run_id,),
    ).fetchall()
    return {
        "run_id": row[0], "case_id": row[1], "policy": row[2], "status": row[3],
        "reason": row[4], "updated_at": row[5].isoformat(),
        "steps": [{"agent": s[0], "status": s[1]} for s in steps],
    }  # fmt: skip


def work_once(
    app_url: str,
    worker_id: str,
    *,
    llm_factory: Callable[[psycopg.Connection], Any] | None = None,
    lease_s: int = 300,
) -> str | None:
    """Claim and run ONE job. Returns the run id, or None when the queue is empty.
    `llm_factory(conn)` builds the gateway for the job (None = no model)."""
    with psycopg.connect(app_url, autocommit=True) as conn:
        claimed = JobQueue(conn).claim(worker_id, lease_s)
        if claimed is None:
            return None
        job_id, run_id = claimed
        try:
            info = conn.execute(
                "SELECT policy_name FROM runtime.investigation_run WHERE run_id=%s", (run_id,)
            ).fetchone()
            if info is None:
                raise NotFound(f"run {run_id}")
            with postgres_checkpointer(app_url) as saver:
                llm = llm_factory(conn) if llm_factory else None
                state = _runner(conn, str(info[0]), saver, llm).run(run_id)
            JobQueue(conn).finish(job_id, ok=state.status is not RunStatus.FAILED)
            log.info("worker.finished", run_id=run_id, status=state.status.value)
        except Exception as exc:  # noqa: BLE001 - process boundary: record and keep the worker alive
            JobQueue(conn).finish(job_id, ok=False, error=f"{type(exc).__name__}: {exc}")
            log.error("worker.failed", run_id=run_id, error_type=type(exc).__name__)
        return run_id
