from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from aegis.capabilities.investigation.schemas import InvestigationOutput
from aegis.common.errors import AegisValidationError
from aegis.common.logging import get_logger
from aegis.domain.agents import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    Permission,
    ResultStatus,
    ToolGateway,
)
from aegis.evidence.canon import account_refs

log = get_logger("aegis.investigation")
VERSION = "investigation-agent-1"
PROMPT_ID = "investigation.synthesise"


class InvestigationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = "investigation"  # key into the LLM config (model id lives in config)


class InvestigationCapability:
    name = AgentName.INVESTIGATION
    version = VERSION
    # No READ_SOURCE: this agent reasons only over evidence, which forces provenance (AG-06).
    permissions = frozenset(
        {Permission.READ_DERIVED, Permission.READ_EVIDENCE, Permission.WRITE_CLAIM,
         Permission.WRITE_PROPOSAL, Permission.CALL_LLM}
    )  # fmt: skip

    def run(self, task: AgentTask, tools: ToolGateway) -> AgentResult:
        try:
            params = InvestigationParams.model_validate(task.params)
        except ValidationError as e:
            return _failed(task, "BAD_PARAMS", str(e))
        ids = [r.id for r in task.input_refs if r.kind == "evidence"]
        if not ids:
            return _failed(task, "NO_EVIDENCE", "no evidence supplied to synthesise")
        case = tools.call("get_case", {})
        evidence = list(tools.call("get_evidence", {"ids": ids})["evidence"])
        contradictions = list(tools.call("get_contradictions", {})["contradictions"])
        items = [
            {
                k: e[k]
                for k in ("evidence_id", "evidence_type", "summary_text", "content", "completeness")
            }  # fmt: skip
            for e in evidence
        ]
        sensitive = {"ACCT": sorted({*account_refs(items), str(case["subject_ref"])})}
        out = tools.call(
            "llm.generate_structured",
            {
                "prompt_id": PROMPT_ID,
                "role": params.role,
                "schema_name": "investigation_output",
                "variables": {
                    "case_summary": {
                        "subject_ref": case["subject_ref"],
                        "alert_reason_codes": case["reason_codes"],
                    },  # fmt: skip
                    "evidence_items": items,
                    "open_contradictions": contradictions,
                },
                "sensitive": sensitive,
            },
        )
        parsed = InvestigationOutput.model_validate(out["parsed"])
        context = set(ids)
        rejected: list[str] = []
        for c in parsed.claims:
            unknown = [x.evidence_id for x in c.cited if x.evidence_id not in context]
            if unknown:  # rejected pre-storage and logged (AG-06 failure handling)
                rejected.append(f"cites unknown evidence {unknown[:2]}")
                log.warning("claim.rejected", task=task.task_id, reason="unknown_evidence")
                continue
            args: dict[str, Any] = {
                "structure": {
                    "subject_ref": c.subject_ref, "predicate": c.predicate,
                    "object_value": c.object_value, "unit": c.unit, "time_scope": c.time_scope,
                },
                "text": c.text,
                "claim_kind": c.claim_kind.value,
                "citations": [x.model_dump(mode="json") for x in c.cited],
                "self_reported_confidence": c.confidence.value,
                "er_link_ids": c.er_link_ids,
            }  # fmt: skip
            try:
                tools.call("create_claim", args)
            except AegisValidationError as e:
                rejected.append(str(e)[:120])
        if rejected:
            return AgentResult(
                task_id=task.task_id, status=ResultStatus.PARTIAL, version=VERSION,
                error=AgentError(code="CLAIMS_REJECTED",
                                 message=f"{len(rejected)} claim(s) rejected: {rejected[:3]}"),
            )  # fmt: skip
        return AgentResult(task_id=task.task_id, status=ResultStatus.OK, version=VERSION)


def _failed(task: AgentTask, code: str, msg: str) -> AgentResult:
    return AgentResult(
        task_id=task.task_id, status=ResultStatus.FAILED, version=VERSION,
        error=AgentError(code=code, message=msg[:300]),
    )  # fmt: skip
