"""Postgres implementations of the runtime services (run as aegis_app).

`PostgresBackend` requires an AUTOCOMMIT connection: `transaction()` is then a real
BEGIN/COMMIT (nested calls become savepoints), and evidence/claim rules that the database checks
at COMMIT (migration 0009) fire at the end of the outermost block."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from ulid import ULID

from aegis.common.errors import AegisValidationError, NotFound
from aegis.common.hashing import canonical_json, content_hash
from aegis.common.ids import new_id
from aegis.domain.agents import AgentName, ResultStatus
from aegis.evidence.canon import hashable
from aegis.evidence.pg_store import PostgresEvidenceStore
from aegis.evidence.types import LeafHashes
from aegis.runtime.services import CaseSummary, SourceTxn, StepRecord
from aegis.runtime.state import RunStatus


def _j(obj: Any) -> Jsonb:
    return Jsonb(json.loads(canonical_json(hashable(obj))))


class PostgresSourceReader:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def transactions(
        self, *, as_of: datetime, start: datetime, end: datetime, account: str | None, limit: int
    ) -> tuple[list[SourceTxn], bool]:
        sql = (
            "SELECT t.txn_id, t.content_hash, t.event_time, t.ingested_at, o.source_account_ref, "
            "d.source_account_ref, t.amount, t.currency FROM source.transaction t "
            "JOIN source.account o ON o.account_id = t.origin_account_id "
            "JOIN source.account d ON d.account_id = t.destination_account_id "
            "WHERE t.ingested_at <= %s AND t.event_time >= %s AND t.event_time < %s "
        )
        params: list[Any] = [as_of, start, end]
        if account is not None:
            sql += "AND (o.source_account_ref = %s OR d.source_account_ref = %s) "
            params += [account, account]
        sql += "ORDER BY t.event_time, t.txn_id LIMIT %s"
        params.append(limit + 1)
        rows = self._c.execute(sql, params).fetchall()
        out = [
            SourceTxn(
                txn_id=r[0], content_hash=r[1], event_time=r[2], ingested_at=r[3],
                origin_ref=r[4], dest_ref=r[5], amount=r[6], currency=str(r[7]).strip(),
            )
            for r in rows[:limit]
        ]  # fmt: skip
        return out, len(rows) > limit


class PostgresCaseReader:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def summary(self, case_id: str) -> CaseSummary:
        row = self._c.execute(
            "SELECT case_id, subject_ref, status FROM workflow.investigation_case WHERE case_id=%s",
            (case_id,),
        ).fetchone()
        if row is None:
            raise NotFound(f"case {case_id}")
        sig = self._c.execute(
            "SELECT s.reason_codes, s.score, coalesce(s.window_end, s.as_of) "
            "FROM workflow.case_alert ca "
            "JOIN workflow.alert a USING (alert_id) JOIN derived.signal s USING (signal_id) "
            "WHERE ca.case_id=%s ORDER BY s.score DESC, s.signal_id",
            (case_id,),
        ).fetchall()
        codes: list[str] = []
        for rc, _, _ in sig:
            codes += [c for c in rc if c not in codes]
        return CaseSummary(
            case_id=row[0], subject_ref=row[1], status=row[2],
            reason_codes=tuple(codes), scores=tuple(float(s) for _, s, _ in sig),
            event_anchor=max((a for _, _, a in sig), default=None),
        )  # fmt: skip


class PostgresApprovals:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def request(self, action: str, subject_ref: str, requested_by: str, justification: str) -> str:
        rid = new_id("apr")
        self._c.execute(
            "INSERT INTO workflow.approval_request (request_id, action_type, subject_ref, "
            "requested_by, justification) VALUES (%s,%s,%s,%s,%s)",
            (rid, action, subject_ref, requested_by, justification),
        )
        return rid


class PostgresAudit:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def event(self, action: str, object_type: str, object_id: str, details: dict[str, Any]) -> None:
        self._c.execute(
            "SELECT audit.record_app(%s,%s,%s,%s)", (action, object_type, object_id, _j(details))
        )


class PostgresRunStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def create_run(
        self, *, run_id: str, case_id: str, as_of: datetime, policy_name: str,
        policy_version: str, budget: dict[str, Any], config: dict[str, Any],
    ) -> None:  # fmt: skip
        self._c.execute(
            "INSERT INTO runtime.investigation_run (run_id, case_id, as_of, policy_name, "
            "policy_version, budget, config) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (run_id, case_id, as_of, policy_name, policy_version, _j(budget), _j(config)),
        )
        self._c.execute(
            "INSERT INTO runtime.run_status_event (run_id, status) VALUES (%s,'QUEUED')", (run_id,)
        )

    def run_info(self, run_id: str) -> dict[str, Any]:
        row = self._c.execute(
            "SELECT run_id, case_id, as_of, policy_name, policy_version, budget, config "
            "FROM runtime.investigation_run WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        keys = ("run_id", "case_id", "as_of", "policy_name", "policy_version", "budget", "config")
        return dict(zip(keys, row, strict=True))

    def set_status(self, run_id: str, status: RunStatus, reason: str | None = None) -> None:
        self._c.execute(
            "INSERT INTO runtime.run_status_event (run_id, status, reason) VALUES (%s,%s,%s)",
            (run_id, status.value, reason),
        )

    def status(self, run_id: str) -> RunStatus:
        row = self._c.execute(
            "SELECT status FROM runtime.run_current WHERE run_id=%s", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunStatus(row[0])

    _COLS = (
        "step_id, run_id, task_id, seq, agent, task_type, capability_version, status, input_hash, "
        "output, tool_calls, llm_call_ids, tokens_in, tokens_out, cost_usd, duration_ms, error"
    )

    def _rec(self, r: tuple[Any, ...]) -> StepRecord:
        return StepRecord(
            step_id=r[0], run_id=r[1], task_id=r[2], seq=r[3], agent=AgentName(r[4]),
            task_type=r[5], capability_version=r[6], status=ResultStatus(r[7]), input_hash=r[8],
            output=r[9], tool_calls=r[10], llm_call_ids=r[11], tokens_in=r[12], tokens_out=r[13],
            cost_usd=r[14], duration_ms=r[15], error=r[16],
        )  # fmt: skip

    def get_step(self, run_id: str, task_id: str) -> StepRecord | None:
        row = self._c.execute(
            f"SELECT {self._COLS} FROM runtime.agent_step WHERE run_id=%s AND task_id=%s",  # noqa: S608
            (run_id, task_id),
        ).fetchone()
        return self._rec(row) if row else None

    def add_step(self, step: StepRecord) -> None:
        self._c.execute(
            f"INSERT INTO runtime.agent_step ({self._COLS}) "  # noqa: S608
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                step.step_id, step.run_id, step.task_id, step.seq, step.agent.value, step.task_type,
                step.capability_version, step.status.value, step.input_hash, _j(step.output),
                _j(step.tool_calls), _j(step.llm_call_ids), step.tokens_in, step.tokens_out,
                step.cost_usd, step.duration_ms, _j(step.error) if step.error else None,
            ),
        )  # fmt: skip

    def steps(self, run_id: str) -> list[StepRecord]:
        rows = self._c.execute(
            f"SELECT {self._COLS} FROM runtime.agent_step WHERE run_id=%s ORDER BY seq",  # noqa: S608
            (run_id,),
        ).fetchall()
        return [self._rec(r) for r in rows]


class PostgresReportStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def add_version(
        self, case_id: str, run_id: str | None, body: dict[str, Any], generator: dict[str, Any]
    ) -> tuple[str, int]:
        prev = self._c.execute(
            "SELECT coalesce(max(version),0) FROM runtime.report WHERE case_id=%s", (case_id,)
        ).fetchone()
        version = (prev[0] if prev else 0) + 1
        rid = f"rep_{ULID()}"
        self._c.execute(
            "INSERT INTO runtime.report (report_id, case_id, run_id, version, body, body_hash, "
            "generator) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (rid, case_id, run_id, version, _j(body), content_hash(hashable(body)), _j(generator)),
        )
        return rid, version

    def get(self, report_id: str) -> dict[str, Any]:
        row = self._c.execute(
            "SELECT report_id, case_id, run_id, version, body, body_hash, generator "
            "FROM runtime.report WHERE report_id=%s",
            (report_id,),
        ).fetchone()
        if row is None:
            raise NotFound(report_id)
        keys = ("report_id", "case_id", "run_id", "version", "body", "body_hash", "generator")
        return dict(zip(keys, row, strict=True))

    def versions(self, case_id: str) -> list[tuple[str, int]]:
        rows = self._c.execute(
            "SELECT report_id, version FROM runtime.report WHERE case_id=%s ORDER BY version",
            (case_id,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]


class PostgresBackend:
    def __init__(self, conn: psycopg.Connection, leaves: LeafHashes) -> None:
        if not conn.autocommit:
            raise AegisValidationError("PostgresBackend needs an autocommit connection")
        self._c = conn
        self.evidence = PostgresEvidenceStore(conn, leaves)
        self.runs = PostgresRunStore(conn)
        self.reports = PostgresReportStore(conn)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._c.transaction():
            yield


class JobQueue:
    """Work queue over runtime.job using FOR UPDATE SKIP LOCKED (D-03). A claimed job whose lease
    has expired (worker died) can be claimed again; the run itself resumes from its checkpoint."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def enqueue(self, run_id: str) -> str:
        job_id = new_id("job")
        self._c.execute(
            "INSERT INTO runtime.job (job_id, run_id, status) VALUES (%s,%s,'QUEUED')",
            (job_id, run_id),
        )
        return job_id

    def claim(self, worker: str, lease_s: int = 300) -> tuple[str, str] | None:
        """Returns (job_id, run_id) or None."""
        with self._c.transaction():
            row = self._c.execute(
                "SELECT job_id, run_id FROM runtime.job WHERE status = 'QUEUED' "
                "OR (status = 'CLAIMED' AND locked_until < now()) "
                "ORDER BY created_at, job_id LIMIT 1 FOR UPDATE SKIP LOCKED"
            ).fetchone()
            if row is None:
                return None
            self._c.execute(
                "UPDATE runtime.job SET status='CLAIMED', attempts=attempts+1, locked_by=%s, "
                "locked_until=now() + make_interval(secs => %s), updated_at=now() WHERE job_id=%s",
                (worker, lease_s, row[0]),
            )
        return row[0], row[1]

    def finish(self, job_id: str, *, ok: bool, error: str | None = None) -> None:
        self._c.execute(
            "UPDATE runtime.job SET status=%s, last_error=%s, locked_until=NULL, updated_at=now() "
            "WHERE job_id=%s",
            ("DONE" if ok else "FAILED", (error or "")[:2000] or None, job_id),
        )
