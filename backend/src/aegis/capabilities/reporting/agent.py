import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from aegis.capabilities.reporting.schemas import NarrativeItem, ReportNarrative
from aegis.common.errors import BudgetExceeded, PermissionDenied
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
from aegis.llm.types import LlmError

log = get_logger("aegis.reporting")
VERSION = "reporting-agent-1"
PROMPT_ID = "reporting.draft"
TEMPLATE_NOTE = (
    "PLACEHOLDER structure. The official FIU-IND STR format has not been read from the source "
    "and verified (decisions.md Q-04); this document is not a filing."
)


class ReportingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = "reporting"
    request_filing_approval: bool = False  # creates a PENDING approval request only (D-09)


def _ev_ids(c: Mapping[str, Any]) -> list[str]:
    return sorted(x["evidence_id"] for x in c["citations"])


def build_report_body(
    *,
    case: Mapping[str, Any],
    run_id: str,
    as_of: str,
    claims: Sequence[Mapping[str, Any]],
    contradictions: Sequence[Mapping[str, Any]],
    narrative: ReportNarrative | None,
    narrative_note: str,
    weak_evidence: Mapping[str, str],
) -> dict[str, Any]:
    """Assemble the report deterministically from claims that carry a verification `status`.

    Guarantees (property-tested): findings.items are exactly the VERIFIED claims, each with its
    citations; narrative items are kept only if every one cites verified claim ids; every claim
    not VERIFIED appears in partially_supported or not_findings, never in findings.
    """
    verified = [c for c in claims if c["status"] == "VERIFIED"]
    partial = [c for c in claims if c["status"] == "PARTIALLY_SUPPORTED"]
    other = [c for c in claims if c["status"] not in ("VERIFIED", "PARTIALLY_SUPPORTED")]
    vids = {c["claim_id"] for c in verified}
    by_id = {c["claim_id"]: c for c in verified}

    items = [
        {"claim_id": c["claim_id"], "text": c["text"], "claim_kind": c["claim_kind"],
         "evidence_ids": _ev_ids(c)}
        for c in sorted(verified, key=lambda c: (c["text"], c["claim_id"]))
    ]  # fmt: skip
    kept: list[dict[str, Any]] = []
    source = "NONE"
    if narrative is not None:
        ok = bool(narrative.items) and all(
            it.claim_ids and set(it.claim_ids) <= vids for it in narrative.items
        )
        if ok:
            source = "LLM_CITATION_CHECKED"
            kept = [
                {"text": it.text, "claim_ids": sorted(set(it.claim_ids)),
                 "evidence_ids": sorted({e for i in it.claim_ids for e in _ev_ids(by_id[i])})}
                for it in narrative.items
            ]  # fmt: skip
        else:
            source = "REJECTED_BY_CITATION_CHECK"
            narrative_note = (
                "narrative discarded: an item cited no verified claim or an unknown one"
            )

    cited = sum(1 for i in items if i["evidence_ids"]) + sum(1 for n in kept if n["evidence_ids"])
    total = len(items) + len(kept)
    limitations = [
        "Scores in this report are model scores, not probabilities (D-06).",
        TEMPLATE_NOTE,
        f"Analysis covers data ingested up to as_of={as_of} only.",
        f"{len(other)} claim(s) were not verified and are excluded from findings; "
        f"{len(partial)} are only partially supported.",
    ]
    if not verified:
        limitations.append("No claim was verified: this report contains no findings.")
    if narrative_note:
        limitations.append(narrative_note)
    for eid, why in sorted(weak_evidence.items()):
        limitations.append(f"Evidence {eid} is {why}.")
    unconfirmed = [
        {"claim_id": c["claim_id"], "link_ids": list(c["depends_on_er_links"])}
        for c in claims
        if c.get("depends_on_er_links")
    ]
    if unconfirmed:
        limitations.append(
            f"{len(unconfirmed)} claim(s) depend on entity links that are not confirmed."
        )

    def row(c: Mapping[str, Any]) -> dict[str, Any]:
        return {"claim_id": c["claim_id"], "text": c["text"], "status": c["status"],
                "evidence_ids": _ev_ids(c)}  # fmt: skip

    return {
        "report_format": {"template": "PLACEHOLDER", "note": TEMPLATE_NOTE},
        "case": {
            "case_id": case["case_id"],
            "subject_ref": case["subject_ref"],
            "run_id": run_id,
            "as_of": as_of,
        },  # fmt: skip
        "findings": {"items": items, "narrative": kept, "narrative_source": source},
        "partially_supported": [
            row(c) for c in sorted(partial, key=lambda c: (c["text"], c["claim_id"]))
        ],
        "not_findings": [row(c) for c in sorted(other, key=lambda c: (c["text"], c["claim_id"]))],
        "open_contradictions": [dict(c) for c in contradictions],
        "unconfirmed_entity_link_dependencies": unconfirmed,
        "limitations": limitations,
        "citation_coverage": {"cited": cited, "total": total},
        "approvals": {"filing_recommendation_request_id": None},
    }


class ReportingCapability:
    name = AgentName.REPORTING
    version = VERSION
    permissions = frozenset(
        {Permission.READ_DERIVED, Permission.READ_EVIDENCE, Permission.WRITE_PROPOSAL,
         Permission.CALL_LLM, Permission.REQUEST_APPROVAL}
    )  # fmt: skip

    def __init__(self, use_llm: bool = True) -> None:
        self._use_llm = use_llm

    def run(self, task: AgentTask, tools: ToolGateway) -> AgentResult:
        try:
            params = ReportingParams.model_validate(task.params)
        except ValidationError as e:
            return AgentResult(
                task_id=task.task_id, status=ResultStatus.FAILED, version=VERSION,
                error=AgentError(code="BAD_PARAMS", message=str(e)[:300]),
            )  # fmt: skip
        case = dict(tools.call("get_case", {}))
        in_scope = {r.id for r in task.input_refs if r.kind == "claim"}
        # a report covers the claims of THIS run (earlier runs have their own report versions)
        claims = [c for c in tools.call("get_claims", {})["claims"] if c["claim_id"] in in_scope]
        contradictions = list(tools.call("get_contradictions", {})["contradictions"])
        verified = [c for c in claims if c["status"] == "VERIFIED"]

        narrative: ReportNarrative | None = None
        note = ""
        status = ResultStatus.OK
        if verified and self._use_llm:
            # The model sees stable aliases (C1, C2, ...), never real claim ids: ids are random per
            # run, and replaying a recorded run needs byte-identical prompts. Order is by content.
            ordered = sorted(
                verified, key=lambda c: (c["text"], json.dumps(c["structure"], sort_keys=True))
            )
            alias = {f"C{i + 1}": c["claim_id"] for i, c in enumerate(ordered)}
            payload = [
                {"claim_id": f"C{i + 1}", "text": c["text"], "structure": c["structure"]}
                for i, c in enumerate(ordered)
            ]
            try:
                out = tools.call(
                    "llm.generate_structured",
                    {
                        "prompt_id": PROMPT_ID,
                        "role": params.role,
                        "schema_name": "report_narrative",
                        "variables": {
                            "case_summary": {"subject_ref": case["subject_ref"]},
                            "claims": payload,
                        },
                        "sensitive": {
                            "ACCT": sorted({*account_refs(payload), case["subject_ref"]})
                        },
                    },
                )
                raw = ReportNarrative.model_validate(out["parsed"])
                narrative = ReportNarrative(
                    items=[
                        NarrativeItem(
                            text=it.text, claim_ids=[alias.get(x, f"?{x}") for x in it.claim_ids]
                        )
                        for it in raw.items
                    ]
                )  # unknown aliases map to "?alias", which never matches a verified id
            except BudgetExceeded:
                note, status = "narrative skipped: budget exhausted", ResultStatus.PARTIAL
            except (LlmError, PermissionDenied) as e:
                note = f"narrative unavailable: {type(e).__name__}"
        elif verified:
            note = "narrative not generated (LLM disabled); findings are the verified claim list"

        weak: dict[str, str] = {}
        shown = [c for c in claims if c["status"] in ("VERIFIED", "PARTIALLY_SUPPORTED")]
        cited = sorted({eid for c in shown for eid in _ev_ids(c)})
        if cited:
            for item in tools.call("get_evidence", {"ids": cited})["evidence"]:
                if item["completeness"] != "FULL":
                    weak[item["evidence_id"]] = (
                        f"{item['completeness'].lower()} (cited by a finding)"
                    )

        body = build_report_body(
            case=case, run_id=task.run_id, as_of=task.as_of.isoformat(), claims=claims,
            contradictions=contradictions, narrative=narrative, narrative_note=note,
            weak_evidence=weak,
        )  # fmt: skip
        if params.request_filing_approval and verified:
            req = tools.call(
                "request_approval",
                {
                    "action": "REGULATORY_FILING_RECOMMENDATION",
                    "justification": f"{len(verified)} verified claim(s), case {case['case_id']}",
                },
            )
            body["approvals"]["filing_recommendation_request_id"] = req["request_id"]
        tools.call("create_report_version", {"body": body})
        return AgentResult(task_id=task.task_id, status=status, version=VERSION)
