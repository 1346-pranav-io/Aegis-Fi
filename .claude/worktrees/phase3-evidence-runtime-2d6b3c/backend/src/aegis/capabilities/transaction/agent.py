from datetime import timedelta
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis.capabilities.transaction import compute
from aegis.domain.agents import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    Permission,
    ResultStatus,
    ToolGateway,
)
from aegis.domain.evidence import Completeness, EvidenceType
from aegis.evidence.drafts import query_input, source_inputs, tool_draft

VERSION = "txn-agent-1"


class TxnSummaryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_hours: int = Field(default=72, ge=1, le=24 * 90)
    structuring_threshold: str = "10000"
    band_pct: int = Field(default=10, ge=1, le=50)
    rapid_window_s: int = Field(default=3600, ge=60, le=86_400)
    velocity_window_s: int = Field(default=3600, ge=60, le=86_400)


class TransactionCapability:
    name = AgentName.TRANSACTION
    version = VERSION
    permissions = frozenset({Permission.READ_SOURCE, Permission.READ_DERIVED})

    def run(self, task: AgentTask, tools: ToolGateway) -> AgentResult:
        try:
            p = TxnSummaryParams.model_validate(task.params)
            threshold = Decimal(p.structuring_threshold)
        except (ValidationError, InvalidOperation) as e:
            return AgentResult(
                task_id=task.task_id, status=ResultStatus.FAILED, version=VERSION,
                error=AgentError(code="BAD_PARAMS", message=str(e)[:300]),
            )  # fmt: skip
        subject = str(tools.call("get_case", {})["subject_ref"])
        start, end = task.as_of - timedelta(hours=p.lookback_hours), task.as_of
        res = tools.call("get_transactions", {"subject": subject, "start": start, "end": end})
        raw = list(res["transactions"])
        truncated = bool(res["truncated"])
        rows = compute.parse_rows(raw)
        inputs = source_inputs(raw) or (
            query_input(
                {
                    "tool": "get_transactions",
                    "subject": subject,
                    "start": start,
                    "end": end,
                    "as_of": task.as_of,
                }
            ),  # fmt: skip
        )
        comp = Completeness.TRUNCATED if truncated else Completeness.FULL
        note = "row limit reached: figures cover only the earliest rows" if truncated else None
        parts = [
            (EvidenceType.TXN_AGGREGATE, compute.aggregate(rows, subject), "Activity totals"),
            (EvidenceType.TXN_VELOCITY, compute.velocity(rows, subject, p.velocity_window_s),
             "Peak transaction velocity"),
            (EvidenceType.TXN_STRUCTURING_CHECK,
             compute.structuring(rows, subject, threshold, p.band_pct), "Structuring check"),
            (EvidenceType.TXN_RAPID_MOVEMENT,
             compute.rapid_movement(rows, subject, p.rapid_window_s), "Rapid pass-through"),
            (EvidenceType.TXN_COUNTERPARTY_SUMMARY, compute.counterparties(rows, subject),
             "Counterparty summary"),
        ]  # fmt: skip
        drafts = tuple(
            tool_draft(
                case_id=task.case_id, evidence_type=et, content=content,
                summary=f"{label} for {subject} over {p.lookback_hours}h: {len(rows)} txn(s)",
                producer_name="transaction_agent", producer_version=VERSION, as_of=task.as_of,
                window=(start, end), inputs=inputs, completeness=comp, notes=note,
            )
            for et, content, label in parts
        )  # fmt: skip
        return AgentResult(
            task_id=task.task_id,
            status=ResultStatus.PARTIAL if truncated else ResultStatus.OK,
            evidence=drafts,
            version=VERSION,
        )
