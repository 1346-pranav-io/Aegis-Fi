from datetime import datetime, timedelta
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis.analytics import behaviour as b
from aegis.analytics.graph import EdgeIndex
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

VERSION = "behaviour-agent-1"


class BehaviourParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_hours: int = Field(default=48, ge=1, le=24 * 30)
    baseline_hours: int = Field(default=240, ge=2, le=24 * 365)


class BehaviourCapability:
    name = AgentName.BEHAVIOUR
    version = VERSION
    permissions = frozenset({Permission.READ_SOURCE, Permission.READ_DERIVED})

    def __init__(self, cfg: b.BehaviourConfig = b.DEFAULT_CONFIG) -> None:
        self._cfg = cfg

    def run(self, task: AgentTask, tools: ToolGateway) -> AgentResult:
        try:
            p = BehaviourParams.model_validate(task.params)
        except ValidationError as e:
            return AgentResult(
                task_id=task.task_id, status=ResultStatus.FAILED, version=VERSION,
                error=AgentError(code="BAD_PARAMS", message=str(e)[:300]),
            )  # fmt: skip
        subject = str(tools.call("get_case", {})["subject_ref"])
        test_start = task.as_of - timedelta(hours=p.test_hours)
        base_start = test_start - timedelta(hours=p.baseline_hours)
        res = tools.call("get_edges", {"start": base_start, "end": task.as_of})
        raw: list[dict[str, Any]] = list(res["transactions"])
        truncated = bool(res["truncated"])
        accounts = sorted({r["origin_ref"] for r in raw} | {r["dest_ref"] for r in raw} | {subject})
        code = {a: i for i, a in enumerate(accounts)}
        edges = EdgeIndex.build(
            np.array([code[r["origin_ref"]] for r in raw], dtype=np.int64),
            np.array([code[r["dest_ref"]] for r in raw], dtype=np.int64),
            np.array([int(datetime.fromisoformat(r["event_time"]).timestamp()) for r in raw],
                     dtype=np.int64),
            np.array([float(r["amount"]) for r in raw], dtype=np.float64),
        )  # fmt: skip
        base = (int(base_start.timestamp()), int(test_start.timestamp()))
        test = (int(test_start.timestamp()), int(task.as_of.timestamp()))
        peers = b.PeerTable.build(edges, len(accounts), base, test, self._cfg)
        prof = b.behaviour_profile(edges, code[subject], base, test, peers, self._cfg)
        insufficient = prof.get("status") == "INSUFFICIENT_HISTORY"
        inputs = source_inputs(raw) or (
            query_input(
                {"tool": "get_edges", "start": base_start, "end": task.as_of, "as_of": task.as_of}
            ),  # fmt: skip
        )
        comp = (
            Completeness.INSUFFICIENT
            if insufficient
            else Completeness.TRUNCATED
            if truncated
            else Completeness.FULL
        )
        head = {"subject": subject, "algorithm": b.ALGORITHM_VERSION, "status": prof["status"],
                "baseline_txns": prof["baseline_txns"], "test_txns": prof["test_txns"],
                "baseline_hours": p.baseline_hours, "test_hours": p.test_hours}  # fmt: skip
        self_keys = ("z_out_count", "z_in_count", "expected_out_in_test", "expected_in_in_test",
                     "new_counterparty_share", "z_log_amount_mean")  # fmt: skip
        peer_keys = ("peer_percentile_of_test_count", "peer_bucket")
        kw: dict[str, Any] = dict(
            case_id=task.case_id, producer_name="behaviour_agent", producer_version=VERSION,
            as_of=task.as_of, window=(base_start, task.as_of), inputs=inputs, completeness=comp,
            notes="insufficient history: no deviation is claimed" if insufficient else None,
        )  # fmt: skip
        drafts = [
            tool_draft(
                evidence_type=EvidenceType.BEHAV_SELF_DEVIATION,
                content={**head, **{k: prof[k] for k in self_keys if k in prof}},
                summary=(
                    f"Self-history deviation for {subject}: "
                    + ("INSUFFICIENT HISTORY" if insufficient else "computed")
                ),
                **kw,
            )
        ]
        if "peer_percentile_of_test_count" in prof:
            drafts.append(
                tool_draft(
                    evidence_type=EvidenceType.BEHAV_PEER_DEVIATION,
                    content={**head, **{k: prof[k] for k in peer_keys if k in prof}},
                    summary=f"Peer-group rank of recent activity for {subject} (a rank)",
                    **kw,
                )
            )
        return AgentResult(
            task_id=task.task_id,
            status=ResultStatus.PARTIAL if truncated else ResultStatus.OK,
            evidence=tuple(drafts),
            version=VERSION,
        )
