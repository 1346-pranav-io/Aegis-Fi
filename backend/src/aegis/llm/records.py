"""Call records: one row per provider attempt (including failures and replay hits).

Only pseudonymised text is stored. Table `llm.call_record` (migration 0010) is insert-only."""

from datetime import datetime
from typing import Protocol

import psycopg
from pydantic import BaseModel, ConfigDict

from aegis.llm.types import CallStatus


class CallRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    run_id: str | None
    step_id: str | None
    agent: str
    role: str
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    model_id: str
    provider: str
    mode: str
    request_key: str
    input_hash: str
    repair_round: int
    attempt: int
    status: CallStatus
    from_replay: bool
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int
    response_text: str | None  # pseudonymised form
    error: str | None
    created_at: datetime


class CallRecorder(Protocol):
    def record(self, rec: CallRecord) -> None: ...


class InMemoryCallRecorder:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def record(self, rec: CallRecord) -> None:
        self.records.append(rec)


class PostgresCallRecorder:
    """Writes llm.call_record using the caller's connection/transaction (no commit here)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._c = conn

    def record(self, rec: CallRecord) -> None:
        self._c.execute(
            "INSERT INTO llm.call_record (call_id, run_id, step_id, agent, role, prompt_id, "
            "prompt_version, prompt_hash, model_id, provider, mode, request_key, input_hash, "
            "repair_round, attempt, status, from_replay, tokens_in, tokens_out, cost_usd, "
            "duration_ms, response_text, error, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                rec.call_id, rec.run_id, rec.step_id, rec.agent, rec.role, rec.prompt_id,
                rec.prompt_version, rec.prompt_hash, rec.model_id, rec.provider, rec.mode,
                rec.request_key, rec.input_hash, rec.repair_round, rec.attempt, rec.status.value,
                rec.from_replay, rec.tokens_in, rec.tokens_out, rec.cost_usd, rec.duration_ms,
                rec.response_text, rec.error, rec.created_at,
            ),
        )  # fmt: skip
