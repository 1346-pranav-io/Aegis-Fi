from datetime import datetime, timedelta
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis.analytics import graph as g
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

VERSION = "graph-agent-1"


class EgoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_hours: int = Field(default=72, ge=1, le=24 * 90)
    hops: int = Field(default=2, ge=1, le=3)
    flow_window_s: int = Field(default=3600, ge=60, le=86_400)


class GraphCapability:
    name = AgentName.GRAPH
    version = VERSION
    permissions = frozenset({Permission.READ_SOURCE, Permission.READ_DERIVED})

    def __init__(self, limits: g.GraphLimits = g.DEFAULT_LIMITS) -> None:
        self._limits = limits

    def run(self, task: AgentTask, tools: ToolGateway) -> AgentResult:
        try:
            p = EgoParams.model_validate(task.params)
        except ValidationError as e:
            return AgentResult(
                task_id=task.task_id, status=ResultStatus.FAILED, version=VERSION,
                error=AgentError(code="BAD_PARAMS", message=str(e)[:300]),
            )  # fmt: skip
        subject = str(tools.call("get_case", {})["subject_ref"])
        start, end = task.as_of - timedelta(hours=p.lookback_hours), task.as_of
        res = tools.call("get_edges", {"start": start, "end": end})
        raw: list[dict[str, Any]] = list(res["transactions"])
        load_truncated = bool(res["truncated"])

        # deterministic node coding: sorted account refs -> 0..n-1
        accounts = sorted({r["origin_ref"] for r in raw} | {r["dest_ref"] for r in raw} | {subject})
        code = {a: i for i, a in enumerate(accounts)}
        index = g.EdgeIndex.build(
            np.array([code[r["origin_ref"]] for r in raw], dtype=np.int64),
            np.array([code[r["dest_ref"]] for r in raw], dtype=np.int64),
            np.array([int(datetime.fromisoformat(r["event_time"]).timestamp()) for r in raw],
                     dtype=np.int64),
            np.array([float(r["amount"]) for r in raw], dtype=np.float64),
        )  # fmt: skip
        ego = g.ego_subgraph(
            index, code[subject], p.hops, int(start.timestamp()), int(end.timestamp()), self._limits
        )
        used = [raw[int(e)] for e in ego.edge_ids]
        truncated = ego.truncated or load_truncated
        reasons = list(ego.truncation_reasons) + (
            ["edge load limit reached"] if load_truncated else []
        )
        if used:
            m = g.ego_metrics(index, ego, self._limits, p.flow_window_s)
            inputs = source_inputs(used)
        else:
            m = {"n_nodes": 1, "n_edges": 0, "center_in_txns": 0, "center_out_txns": 0,
                 "center_in_distinct": 0, "center_out_distinct": 0, "n_cycles_through_center": 0,
                 "shortest_cycle_len": None, "flow_through_fraction": None,
                 "algorithm": g.ALGORITHM_VERSION, "params": {"hops": p.hops}}  # fmt: skip
            inputs = (
                query_input(
                    {
                        "tool": "get_edges",
                        "subject": subject,
                        "start": start,
                        "end": end,
                        "as_of": task.as_of,
                        "hops": p.hops,
                    }
                ),  # fmt: skip
            )
        base = {
            "subject": subject,
            "algorithm": g.ALGORITHM_VERSION,
            "hops": p.hops,
            "truncated": truncated,
            "truncation_reasons": reasons,
        }

        def pick(*keys: str) -> dict[str, Any]:
            return {**base, **{k: m[k] for k in keys if k in m}}

        parts = [
            (EvidenceType.GRAPH_NEIGHBOURHOOD, pick("n_nodes", "n_edges"), "Neighbourhood size"),
            (EvidenceType.GRAPH_CYCLES, pick("n_cycles_through_center", "shortest_cycle_len",
                                             "cycle_search_capped", "scc_size_of_center"),
             "Cycles through the subject"),
            (EvidenceType.GRAPH_FANIN_FANOUT, pick("center_in_txns", "center_out_txns",
                                                   "center_in_distinct", "center_out_distinct"),
             "Fan-in / fan-out"),
            (EvidenceType.GRAPH_FLOW_THROUGH, pick("flow_through_fraction"), "Flow-through"),
            (EvidenceType.GRAPH_COMMUNITY, pick("n_communities", "center_community_size",
                                                "community_skipped"), "Community structure"),
            (EvidenceType.GRAPH_CENTRALITY, pick("pagerank_of_center", "pagerank_failed"),
             "Centrality (PageRank score, not a probability)"),
        ]  # fmt: skip
        comp = Completeness.TRUNCATED if truncated else Completeness.FULL
        drafts = tuple(
            tool_draft(
                case_id=task.case_id, evidence_type=et, content=content,
                summary=f"{label} for {subject}: {m['n_nodes']} node(s), {m['n_edges']} edge(s)",
                producer_name="graph_agent", producer_version=VERSION, as_of=task.as_of,
                window=(start, end), inputs=inputs, completeness=comp,
                notes="; ".join(reasons) if truncated else None,
            )
            for et, content, label in parts
        )  # fmt: skip
        return AgentResult(
            task_id=task.task_id,
            status=ResultStatus.PARTIAL if truncated else ResultStatus.OK,
            evidence=drafts,
            version=VERSION,
        )
