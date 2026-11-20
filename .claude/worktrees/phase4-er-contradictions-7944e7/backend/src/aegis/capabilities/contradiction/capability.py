"""Contradiction capability (AG-08). Implements `aegis.domain.agents.Capability`.

Deterministic detectors first (analytics.contradictions); an optional `ExplanationProposer`
may attach a *proposal* to a finding. A proposal never changes a status (FR-CON-03): this class
has no tool that writes a status, and only validated, evidence-citing proposals are attached.

Tool contract (the runtime's ToolGateway implements these; tests use a fake). Every call passes
`case_id` and `as_of`; every result is a JSON-like mapping with an `items` list:

* get_evidence {case_id, as_of} -> items: [{"evidence_id", **EvidenceDraft.model_dump(json)}]
* get_claims   {case_id, as_of} -> items: [{"claim_id", **ClaimDraft.model_dump(json),
                                            "verification": VerificationDraft json | null}]
* get_er_links {case_id, as_of} -> items: [ERLinkView json]; optional "entity_roots": {id: root}
"""

import time
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from aegis.analytics.contradictions import (
    DetectionInput,
    DetectorConfig,
    StoredClaim,
    StoredEvidence,
    detect_all,
    evidence_ids_for,
    fingerprint,
)
from aegis.common.errors import AegisError, PermissionDenied
from aegis.domain.agents import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    ExplanationProposer,
    Permission,
    ResultStatus,
    ToolGateway,
    Usage,
)
from aegis.domain.entities import ERLinkView
from aegis.domain.evidence import (
    ClaimDraft,
    ContradictionDraft,
    EvidenceDraft,
    VerificationDraft,
)

CAPABILITY_VERSION = "1.0.0"
TASK_TYPE = "detect_contradictions"
PERMISSIONS: frozenset[Permission] = frozenset(
    {Permission.READ_EVIDENCE, Permission.WRITE_PROPOSAL, Permission.CALL_LLM}
)
MAX_EXPLANATION_CHARS = 2000


class ContradictionCapability:
    name = AgentName.CONTRADICTION
    version = CAPABILITY_VERSION
    permissions = PERMISSIONS

    def __init__(
        self, proposer: ExplanationProposer | None = None, config: DetectorConfig | None = None
    ) -> None:
        self._proposer = proposer
        self._config = config or DetectorConfig()

    def run(self, task: AgentTask, tools: ToolGateway) -> AgentResult:
        t0 = time.perf_counter()
        if task.agent is not AgentName.CONTRADICTION or task.task_type != TASK_TYPE:
            return self._fail(task, "UNSUPPORTED_TASK", f"cannot run {task.agent}/{task.task_type}")
        args = {"case_id": task.case_id, "as_of": task.as_of.isoformat()}
        calls = 0
        try:
            raw_ev = tools.call("get_evidence", args)
            raw_cl = tools.call("get_claims", args)
            raw_er = tools.call("get_er_links", args)
            calls = 3
            inp = _parse_input(raw_ev, raw_cl, raw_er, task)
        except PermissionDenied as exc:
            return self._fail(task, "DENIED", str(exc), ResultStatus.DENIED)
        except AegisError as exc:
            return self._fail(task, "TOOL_ERROR", str(exc), retryable=True)
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            return self._fail(task, "BAD_TOOL_DATA", f"{type(exc).__name__}: {exc}")

        try:
            drafts = detect_all(inp, self._config, case_id=task.case_id)
        except ValueError as exc:
            return self._fail(task, "DETECTOR_ERROR", str(exc))

        use_llm = _can_call_llm(task) and self._proposer is not None
        out: list[ContradictionDraft] = []
        for d in drafts:
            if use_llm and self._proposer is not None:
                d = self._attach_explanation(d, inp, self._proposer)
            out.append(d)
        usage = Usage(duration_ms=int((time.perf_counter() - t0) * 1000), tool_calls=calls)
        return AgentResult(
            task_id=task.task_id,
            status=ResultStatus.OK,
            contradictions=tuple(out),
            usage=usage,
            version=self.version,
        )

    # ------------------------------------------------------------------------- explanations
    def _attach_explanation(
        self, d: ContradictionDraft, inp: DetectionInput, proposer: ExplanationProposer
    ) -> ContradictionDraft:
        allowed = evidence_ids_for(d, inp)
        subject: dict[str, Any] = {
            "kind": "contradiction_explanation",
            "type": d.type.value,
            "severity": d.severity.value,
            "fingerprint": fingerprint(d),
            "members": [
                {"member_type": m.member_type.value, "id": m.member_id, "role": m.role}
                for m in d.members
            ],
            "allowed_evidence_ids": sorted(allowed),
        }
        try:
            proposal = proposer.propose(subject)
        except AegisError:
            return d  # a failing helper never fails detection; the finding stands without it
        checked = validate_explanation(proposal, allowed)
        if checked is None:
            return d  # unsupported / malformed proposals are discarded (AG-08 failure condition)
        return d.model_copy(update={"explanation_proposal": checked})

    @staticmethod
    def _fail(
        task: AgentTask,
        code: str,
        message: str,
        status: ResultStatus = ResultStatus.FAILED,
        retryable: bool = False,
    ) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            status=status,
            error=AgentError(code=code, message=message[:500], retryable=retryable),
            version=CAPABILITY_VERSION,
        )


def validate_explanation(
    proposal: Mapping[str, Any] | None, allowed_evidence_ids: set[str]
) -> dict[str, Any] | None:
    """Accept a proposal only if it has non-empty text and cites at least one evidence id, all of
    them among `allowed_evidence_ids`. The stored form says plainly it is a proposal with no
    effect on status."""
    if not isinstance(proposal, Mapping):
        return None
    text = proposal.get("text")
    cited = proposal.get("cited_evidence_ids")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_EXPLANATION_CHARS:
        return None
    if not isinstance(cited, list) or not cited or not all(isinstance(c, str) for c in cited):
        return None
    if not set(cited) <= allowed_evidence_ids:
        return None
    return {
        "text": text.strip(),
        "cited_evidence_ids": sorted(set(cited)),
        "is_proposal": True,
        "effect_on_status": "NONE",
        "self_reported_confidence": str(proposal.get("self_reported_confidence", "NONE")),
    }


def _can_call_llm(task: AgentTask) -> bool:
    return task.budget.max_tokens > 0 and task.budget.max_cost_usd > 0


def _items(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = raw["items"]
    if not isinstance(items, list):
        raise TypeError("tool result 'items' must be a list")
    return items


def _parse_input(
    raw_ev: Mapping[str, Any],
    raw_cl: Mapping[str, Any],
    raw_er: Mapping[str, Any],
    task: AgentTask,
) -> DetectionInput:
    evidence = []
    for it in _items(raw_ev):
        body = {k: v for k, v in it.items() if k != "evidence_id"}
        draft = EvidenceDraft.model_validate(body)
        if draft.case_id != task.case_id or draft.as_of > task.as_of:
            continue  # defence in depth: other cases / the future are never analysed (as_of)
        evidence.append(StoredEvidence(str(it["evidence_id"]), draft))
    claims = []
    for it in _items(raw_cl):
        body = {k: v for k, v in it.items() if k not in ("claim_id", "verification")}
        cdraft = ClaimDraft.model_validate(body)
        if cdraft.case_id != task.case_id:
            continue
        ver = it.get("verification")
        claims.append(
            StoredClaim(
                str(it["claim_id"]),
                cdraft,
                None if ver is None else VerificationDraft.model_validate(ver),
            )
        )
    links = tuple(ERLinkView.model_validate(it) for it in _items(raw_er))
    roots_raw = raw_er.get("entity_roots") or {}
    roots = {str(k): str(v) for k, v in dict(roots_raw).items()}
    return DetectionInput(tuple(evidence), tuple(claims), links, roots)
