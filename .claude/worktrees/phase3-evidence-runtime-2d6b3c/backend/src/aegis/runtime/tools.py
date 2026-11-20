"""Tool gateway (AGENT_ARCHITECTURE 5, T-INV-02, T-INV-11).

Capabilities reach data and records ONLY through `RuntimeToolGateway.call`. For every call the
gateway, in this order:
  1. refuses tools it does not know,
  2. refuses tools outside the agent's allow-list,
  3. refuses tools whose permission class the capability does not hold,
  4. validates the arguments against the tool's Pydantic model (typed error on failure),
  5. enforces `as_of`: it fills it in from the task, and refuses a later one - agents cannot
     widen their own time horizon,
  6. enforces per-task context rules (evidence/claims must be in the task's visible set),
  7. enforces the per-task call budget,
  8. logs the call (hash of arguments, outcome) and, if denied, writes an audit event.
There is no tool that writes source data and no tool that executes a consequential action:
`request_approval` only creates a PENDING request (D-09).
"""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis.common.errors import (
    AegisError,
    AegisValidationError,
    BudgetExceeded,
    NotFound,
    PermissionDenied,
)
from aegis.common.hashing import canonical_json, content_hash
from aegis.common.logging import get_logger
from aegis.common.timeutil import require_utc
from aegis.domain.agents import AgentName, AgentTask, Capability, Permission
from aegis.domain.evidence import (
    AuthorKind,
    Citation,
    ClaimDraft,
    ClaimKind,
    ClaimStructure,
    SelfReportedConfidence,
    VerificationCheck,
    VerificationDraft,
    VerificationStatus,
)
from aegis.evidence.canon import hashable
from aegis.evidence.drafts import with_query_refs
from aegis.evidence.store import normalised
from aegis.llm.budget import BudgetTracker
from aegis.llm.types import StructuredRequest
from aegis.runtime.services import RuntimeServices

log = get_logger("aegis.tools")


class ToolDenied(PermissionDenied):
    pass


class ToolArgumentError(AegisValidationError):
    pass


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ argument models
class GetCaseArgs(_Args):
    pass


class GetTransactionsArgs(_Args):
    subject: str | None = None
    start: datetime
    end: datetime
    as_of: datetime | None = None
    limit: int = Field(default=5000, ge=1, le=100_000)


class GetEdgesArgs(_Args):
    start: datetime
    end: datetime
    as_of: datetime | None = None
    limit: int = Field(default=50_000, ge=1, le=1_000_000)


class IdsArgs(_Args):
    ids: list[str] = Field(min_length=1, max_length=500)


class GetClaimsArgs(_Args):
    statuses: list[VerificationStatus | Literal["DRAFT"]] | None = None


class CreateClaimArgs(_Args):
    structure: ClaimStructure
    text: str = Field(min_length=1, max_length=2000)
    claim_kind: ClaimKind
    citations: list[Citation] = Field(min_length=1)
    self_reported_confidence: SelfReportedConfidence = SelfReportedConfidence.NONE
    er_link_ids: list[str] = Field(default_factory=list)


class WriteVerificationArgs(_Args):
    claim_id: str
    status: VerificationStatus
    checks: list[VerificationCheck]
    rationale: str
    verifier_name: str
    verifier_version: str
    model_id: str | None = None
    prompt_version: str | None = None
    inputs_seen_hash: str


class VerifyHashArgs(_Args):
    evidence_id: str


class NoArgs(_Args):
    pass


class LlmArgs(_Args):
    prompt_id: str
    prompt_version: str | None = None
    role: str
    variables: dict[str, Any]
    sensitive: dict[str, list[str]] = Field(default_factory=dict)
    schema_name: str
    max_tokens: int | None = Field(default=None, ge=1)


class CreateReportArgs(_Args):
    body: dict[str, Any]
    generator: dict[str, Any] = Field(default_factory=dict)


class RequestApprovalArgs(_Args):
    action: Literal["REGULATORY_FILING_RECOMMENDATION"]
    justification: str = Field(min_length=1, max_length=2000)


# ------------------------------------------------------------------------ context
@dataclass
class ToolContext:
    task: AgentTask
    capability: Capability
    services: RuntimeServices
    step_id: str
    llm_budget: BudgetTracker | None = None
    visible_evidence: set[str] = field(default_factory=set)
    visible_claims: set[str] = field(default_factory=set)
    created_claims: list[str] = field(default_factory=list)
    created_verifications: list[str] = field(default_factory=list)
    created_reports: list[str] = field(default_factory=list)
    llm_call_ids: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        self.visible_evidence |= {r.id for r in self.task.input_refs if r.kind == "evidence"}
        self.visible_claims |= {r.id for r in self.task.input_refs if r.kind == "claim"}


Handler = Callable[[ToolContext, Any], Mapping[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    permission: Permission
    args: type[BaseModel]
    handler: Handler


@dataclass(frozen=True)
class ToolCallRecord:
    tool: str
    args_hash: str
    status: Literal["OK", "DENIED", "ERROR"]
    reason: str
    duration_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool, "args_hash": self.args_hash, "status": self.status,
            "reason": self.reason, "duration_ms": self.duration_ms,
        }  # fmt: skip


# ------------------------------------------------------------------------ helpers
def _iso(d: datetime) -> str:
    return require_utc(d).isoformat()


def _evidence_json(ev: Any) -> dict[str, Any]:
    d = ev.draft
    return {
        "evidence_id": ev.evidence_id,
        "evidence_type": d.evidence_type.value,
        "summary_text": d.summary_text,
        "content": json.loads(canonical_json(d.content)),  # Decimals -> canonical strings
        "content_hash": ev.content_hash,
        "producer": d.producer.model_dump(mode="json"),
        "as_of": _iso(d.as_of),
        "window": [d.window_start and _iso(d.window_start), d.window_end and _iso(d.window_end)],
        "completeness": d.completeness.value,
        "quality_notes": d.quality_notes,
        "scores": d.scores,
        "inputs": [i.model_dump(mode="json") for i in d.inputs],
    }


def _claim_json(c: Any, *, with_status: bool) -> dict[str, Any]:
    d = c.draft
    out: dict[str, Any] = {
        "claim_id": c.claim_id,
        "structure": d.structure.model_dump(mode="json"),
        "text": d.text,
        "claim_kind": d.claim_kind.value,
        "citations": [x.model_dump(mode="json") for x in d.citations],
        "self_reported_confidence": d.self_reported_confidence.value,
        "depends_on_er_links": list(d.depends_on_er_links),
        "author": {"name": d.author_name, "version": d.author_version},
    }
    if with_status:
        out["status"] = c.status.value if c.status else "DRAFT"
    return out


# ------------------------------------------------------------------------ handlers
def _get_case(ctx: ToolContext, _: NoArgs) -> Mapping[str, Any]:
    s = ctx.services.cases.summary(ctx.task.case_id)
    return {"case_id": s.case_id, "subject_ref": s.subject_ref, "status": s.status,
            "reason_codes": list(s.reason_codes), "scores": list(s.scores)}  # fmt: skip


def _get_transactions(ctx: ToolContext, a: GetTransactionsArgs) -> Mapping[str, Any]:
    if a.as_of is None:  # the gateway always fills it in; fail closed if called around it
        raise ToolDenied("as_of must be set by the gateway")
    rows, trunc = ctx.services.source.transactions(
        as_of=a.as_of, start=a.start, end=a.end, account=a.subject, limit=a.limit
    )
    return {
        "as_of": _iso(a.as_of),
        "truncated": trunc,
        "transactions": [
            {
                "txn_id": t.txn_id,
                "content_hash": t.content_hash,
                "event_time": _iso(t.event_time),
                "origin_ref": t.origin_ref,
                "dest_ref": t.dest_ref,
                "amount": str(t.amount),
                "currency": t.currency,
            }
            for t in rows
        ],
    }


def _get_edges(ctx: ToolContext, a: GetEdgesArgs) -> Mapping[str, Any]:
    return _get_transactions(
        ctx, GetTransactionsArgs(start=a.start, end=a.end, as_of=a.as_of, limit=a.limit)
    )


def _require_visible(ctx: ToolContext, kind: str, ids: list[str]) -> None:
    pool = ctx.visible_evidence if kind == "evidence" else ctx.visible_claims
    unseen = [i for i in ids if i not in pool]
    if unseen:
        raise ToolDenied(f"{kind} ids not in this task's context: {unseen[:3]}")


def _get_evidence(ctx: ToolContext, a: IdsArgs) -> Mapping[str, Any]:
    _require_visible(ctx, "evidence", a.ids)
    items = []
    for i in a.ids:
        ev = ctx.services.backend.evidence.get_evidence(i)
        if ev.draft.case_id != ctx.task.case_id or ev.draft.as_of > ctx.task.as_of:
            raise ToolDenied(f"evidence {i} is outside this task's case/as_of")
        items.append(_evidence_json(ev))
    return {"evidence": items}


def _get_claim(ctx: ToolContext, a: IdsArgs) -> Mapping[str, Any]:
    """Claim text + citations only. Deliberately NO verification status and no author context
    beyond name/version: the verifier must not see other results (AG-07, D-08)."""
    _require_visible(ctx, "claim", a.ids)
    out = []
    for i in a.ids:
        c = ctx.services.backend.evidence.get_claim(i)
        if c.draft.case_id != ctx.task.case_id:
            raise ToolDenied(f"claim {i} belongs to another case")
        ctx.visible_evidence |= {x.evidence_id for x in c.draft.citations}
        out.append(_claim_json(c, with_status=False))
    return {"claims": out}


def _get_claims(ctx: ToolContext, a: GetClaimsArgs) -> Mapping[str, Any]:
    want = {s if isinstance(s, str) else s.value for s in a.statuses} if a.statuses else None
    out = []
    for c in ctx.services.backend.evidence.list_claims(ctx.task.case_id):
        st = c.status.value if c.status else "DRAFT"
        if want is None or st in want:
            ctx.visible_claims.add(c.claim_id)
            ctx.visible_evidence |= {x.evidence_id for x in c.draft.citations}
            out.append(_claim_json(c, with_status=True))
    return {"claims": out}


def _create_claim(ctx: ToolContext, a: CreateClaimArgs) -> Mapping[str, Any]:
    cited = [c.evidence_id for c in a.citations]
    unseen = [i for i in cited if i not in ctx.visible_evidence]
    if unseen:  # AG-06: claims may only cite evidence in the author's context
        raise ToolArgumentError(f"claim cites evidence outside its context: {unseen[:3]}")
    deps = ctx.services.er.unconfirmed_dependencies(a.er_link_ids) if a.er_link_ids else []
    try:
        draft = ClaimDraft(
            case_id=ctx.task.case_id,
            run_id=ctx.task.run_id,
            author_kind=AuthorKind.AGENT,
            author_name=ctx.task.agent.value.lower(),
            author_version=ctx.capability.version,
            structure=a.structure,
            text=a.text,
            claim_kind=a.claim_kind,
            citations=tuple(a.citations),
            self_reported_confidence=a.self_reported_confidence,
            depends_on_er_links=tuple(deps),
        )
        stored = ctx.services.backend.evidence.create_claim(draft)
    except (ValidationError, AegisValidationError, NotFound) as e:
        raise ToolArgumentError(f"claim rejected: {e}") from e
    ctx.created_claims.append(stored.claim_id)
    return {"claim_id": stored.claim_id, "depends_on_unconfirmed_links": deps}


def _write_verification(ctx: ToolContext, a: WriteVerificationArgs) -> Mapping[str, Any]:
    if a.claim_id not in ctx.visible_claims:
        raise ToolDenied("claim is not in this task's context")
    try:
        draft = VerificationDraft(**a.model_dump(exclude={"checks"}), checks=tuple(a.checks))
        stored = ctx.services.backend.evidence.record_verification(draft)
    except (ValidationError, AegisValidationError, NotFound) as e:
        raise ToolArgumentError(f"verification rejected: {e}") from e
    ctx.created_verifications.append(stored.verification_id)
    return {"verification_id": stored.verification_id}


def _verify_hash(ctx: ToolContext, a: VerifyHashArgs) -> Mapping[str, Any]:
    _require_visible(ctx, "evidence", [a.evidence_id])
    ev = ctx.services.backend.evidence.get_evidence(a.evidence_id)
    own_ok = normalised(ev.draft).content_hash() == ev.content_hash
    leaves = []
    for i in ev.draft.inputs:
        if i.ref_type.value == "EVIDENCE":
            continue
        held = with_query_refs(ctx.services.leaves).hash_of(i.ref_type, i.ref_id)
        leaves.append({"ref": f"{i.ref_type.value}:{i.ref_id}", "found": held is not None,
                       "match": held == i.ref_hash})  # fmt: skip
    if not own_ok or any(not (x["found"] and x["match"]) for x in leaves):
        ctx.services.audit.event(
            "app.evidence_hash_mismatch", "evidence", a.evidence_id,
            {"task": ctx.task.task_id, "hash_intact": own_ok, "leaves": leaves},
        )  # fmt: skip
    return {"evidence_id": a.evidence_id, "hash_intact": own_ok, "leaves": leaves}


def _get_contradictions(ctx: ToolContext, _: NoArgs) -> Mapping[str, Any]:
    return {
        "contradictions": [
            {"contradiction_id": c.contradiction_id, "type": c.type.value,
             "severity": c.severity.value, "status": c.status.value,
             "member_ids": list(c.member_ids)}
            for c in ctx.services.backend.evidence.unresolved_contradictions(ctx.task.case_id)
        ]
    }  # fmt: skip


def _get_er_links(ctx: ToolContext, a: IdsArgs) -> Mapping[str, Any]:
    return {
        "links": [v.model_dump(mode="json") for v in ctx.services.er.latest_links(a.ids)],
        "unconfirmed": ctx.services.er.unconfirmed_dependencies(a.ids),
    }


def _llm(ctx: ToolContext, a: LlmArgs) -> Mapping[str, Any]:
    gw = ctx.services.llm
    if gw is None:
        raise ToolDenied("no LLM gateway configured for this run")
    own = ctx.task.agent.value.lower() + "."
    if not a.prompt_id.startswith(own):  # an agent may only use its own prompts
        raise ToolDenied(f"prompt {a.prompt_id!r} is not an {ctx.task.agent.value} prompt")
    out_model = ctx.services.schemas.get(a.schema_name)
    if out_model is None:
        raise ToolArgumentError(f"unknown output schema {a.schema_name!r}")
    res = gw.generate_structured(
        StructuredRequest(
            prompt_id=a.prompt_id, prompt_version=a.prompt_version, role=a.role,
            variables=a.variables, sensitive=a.sensitive, agent=ctx.task.agent.value,
            run_id=ctx.task.run_id, step_id=ctx.step_id, max_tokens=a.max_tokens,
        ),
        out_model,
        budget=ctx.llm_budget,
    )  # fmt: skip
    ctx.llm_call_ids.extend(res.call_ids)
    ctx.tokens_in += res.tokens_in
    ctx.tokens_out += res.tokens_out
    ctx.cost_usd += res.cost_usd
    return {
        "parsed": res.parsed.model_dump(mode="json"), "raw_text": res.raw_text,
        "model_id": res.model_id, "prompt_id": res.prompt_id, "prompt_version": res.prompt_version,
        "prompt_hash": res.prompt_hash, "call_ids": list(res.call_ids),
        "repair_rounds": res.repair_rounds, "from_replay": res.from_replay,
    }  # fmt: skip


def _create_report(ctx: ToolContext, a: CreateReportArgs) -> Mapping[str, Any]:
    rid, version = ctx.services.backend.reports.add_version(
        ctx.task.case_id, ctx.task.run_id, a.body,
        {"agent": ctx.task.agent.value, "version": ctx.capability.version, **a.generator},
    )  # fmt: skip
    ctx.created_reports.append(rid)
    return {"report_id": rid, "version": version}


def _request_approval(ctx: ToolContext, a: RequestApprovalArgs) -> Mapping[str, Any]:
    summary = ctx.services.cases.summary(ctx.task.case_id)
    rid = ctx.services.approvals.request(
        a.action, summary.subject_ref, f"agent:{ctx.task.agent.value.lower()}", a.justification
    )
    return {"request_id": rid, "status": "PENDING"}  # a request only; a human decides (D-09)


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec("get_case", Permission.READ_DERIVED, NoArgs, _get_case),
        ToolSpec(
            "get_transactions", Permission.READ_SOURCE, GetTransactionsArgs, _get_transactions
        ),
        ToolSpec("get_edges", Permission.READ_SOURCE, GetEdgesArgs, _get_edges),
        ToolSpec("get_evidence", Permission.READ_EVIDENCE, IdsArgs, _get_evidence),
        ToolSpec("get_claim", Permission.READ_EVIDENCE, IdsArgs, _get_claim),
        ToolSpec("get_claims", Permission.READ_EVIDENCE, GetClaimsArgs, _get_claims),
        ToolSpec("verify_hash", Permission.READ_EVIDENCE, VerifyHashArgs, _verify_hash),
        ToolSpec("get_contradictions", Permission.READ_EVIDENCE, NoArgs, _get_contradictions),
        ToolSpec("get_er_links", Permission.READ_EVIDENCE, IdsArgs, _get_er_links),
        ToolSpec("create_claim", Permission.WRITE_CLAIM, CreateClaimArgs, _create_claim),
        ToolSpec(
            "write_verification", Permission.WRITE_CLAIM, WriteVerificationArgs, _write_verification
        ),
        ToolSpec("llm.generate_structured", Permission.CALL_LLM, LlmArgs, _llm),
        ToolSpec(
            "create_report_version", Permission.WRITE_PROPOSAL, CreateReportArgs, _create_report
        ),
        ToolSpec(
            "request_approval", Permission.REQUEST_APPROVAL, RequestApprovalArgs, _request_approval
        ),
    )
}

_LLM = "llm.generate_structured"
ALLOW_LISTS: dict[AgentName, frozenset[str]] = {
    AgentName.ORCHESTRATOR: frozenset(),
    AgentName.TRANSACTION: frozenset({"get_case", "get_transactions"}),
    AgentName.GRAPH: frozenset({"get_case", "get_edges"}),
    AgentName.BEHAVIOUR: frozenset({"get_case", "get_edges"}),
    AgentName.INTELLIGENCE: frozenset({"get_case", _LLM}),
    AgentName.INVESTIGATION: frozenset(
        {"get_case", "get_evidence", "get_contradictions", "get_er_links", "create_claim", _LLM}
    ),
    AgentName.VERIFICATION: frozenset(
        {"get_claim", "get_evidence", "verify_hash", "write_verification", _LLM}
    ),
    AgentName.CONTRADICTION: frozenset({"get_evidence", "get_claims", "get_er_links", _LLM}),
    AgentName.REPORTING: frozenset(
        {"get_case", "get_claims", "get_evidence", "get_contradictions", _LLM,
         "create_report_version", "request_approval"}
    ),
}  # fmt: skip


class RuntimeToolGateway:
    """Per-task implementation of `ToolGateway`."""

    def __init__(self, ctx: ToolContext, allow: Mapping[AgentName, frozenset[str]] = ALLOW_LISTS):
        self.ctx = ctx
        self._allow = allow
        self.calls: list[ToolCallRecord] = []

    def call(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        t0 = time.perf_counter()
        ahash = content_hash(hashable(dict(arguments)))

        def done(status: Literal["OK", "DENIED", "ERROR"], reason: str = "") -> None:
            ms = int((time.perf_counter() - t0) * 1000)
            self.calls.append(ToolCallRecord(tool, ahash, status, reason, ms))

        def deny(reason: str) -> ToolDenied:
            done("DENIED", reason)
            self.ctx.services.audit.event(
                "app.tool_denied", "agent_task", self.ctx.task.task_id,
                {"agent": self.ctx.task.agent.value, "tool": tool, "reason": reason},
            )  # fmt: skip
            log.warning("tool.denied", agent=self.ctx.task.agent.value, tool=tool, reason=reason)
            return ToolDenied(reason)

        spec = TOOLS.get(tool)
        if spec is None:
            raise deny("unknown tool")
        if tool not in self._allow.get(self.ctx.task.agent, frozenset()):
            raise deny(f"tool not in the allow-list of {self.ctx.task.agent.value}")
        if spec.permission not in self.ctx.capability.permissions:
            raise deny(f"agent lacks permission {spec.permission.value}")
        if len(self.calls) >= self.ctx.task.budget.max_steps * 50:  # bound on tool calls per task
            done("DENIED", "tool-call budget")
            raise BudgetExceeded("tool-call budget for this task exhausted")
        try:
            args = spec.args.model_validate(dict(arguments))
        except ValidationError as e:
            done("ERROR", "invalid arguments")
            raise ToolArgumentError(f"{tool}: invalid arguments: {e.error_count()} error(s)") from e
        if "as_of" in spec.args.model_fields:
            asked = getattr(args, "as_of", None)
            if asked is not None and require_utc(asked) > self.ctx.task.as_of:
                raise deny("requested as_of is later than the task's as_of")
            args = args.model_copy(
                update={
                    "as_of": min(require_utc(asked), self.ctx.task.as_of)
                    if asked
                    else self.ctx.task.as_of
                }
            )
        try:
            result = spec.handler(self.ctx, args)
        except ToolDenied as e:
            raise deny(str(e)) from e
        except (ToolArgumentError, BudgetExceeded):
            done("ERROR", "rejected by tool")
            raise
        except AegisError as e:  # NotFound, LLM errors, ...: logged, then surfaced to the caller
            done("ERROR", type(e).__name__)
            raise
        done("OK")
        return result
