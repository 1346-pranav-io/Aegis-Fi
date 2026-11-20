from collections.abc import Mapping
from typing import Any

from aegis.analytics.contradictions import claim_vs_evidence_findings
from aegis.capabilities.verification.schemas import EntailmentOut, Verdict
from aegis.common.errors import BudgetExceeded, NotFound, PermissionDenied
from aegis.common.hashing import content_hash
from aegis.common.logging import get_logger
from aegis.domain.agents import (
    AgentName,
    AgentResult,
    AgentTask,
    Permission,
    ResultStatus,
    ToolGateway,
)
from aegis.domain.evidence import (
    CheckKind,
    CheckOutcome,
    ContradictionDraft,
    SupportRole,
    VerificationCheck,
    VerificationStatus,
)
from aegis.evidence import checks as ck
from aegis.evidence.canon import account_refs, hashable
from aegis.llm.types import LlmError

log = get_logger("aegis.verification")
VERSION = "verification-agent-1"
PROMPT_ID = "verification.entail"
NUMERIC_KINDS = ("FACT_OBSERVATION", "AGGREGATE")


def _check(
    cid: str, outcome: CheckOutcome, detail: str = "", kind: CheckKind = CheckKind.DETERMINISTIC
) -> VerificationCheck:
    return VerificationCheck(check_id=cid, kind=kind, outcome=outcome, detail=detail[:500])


def derive_status(
    det: list[ck.CheckResult],
    *,
    integrity_ok: bool,
    semantic: EntailmentOut | None,
    span_ok: bool,
    purely_numeric: bool,
) -> tuple[VerificationStatus, str]:
    """EVIDENCE_MODEL 5 status table. Pure; unit-tested exhaustively. Returns (status, reason).
    Never VERIFIED when any check failed, and never without a semantic entailment unless the
    claim is purely numeric and fully deterministic-checkable (AG-07)."""
    if not integrity_ok:
        return VerificationStatus.UNVERIFIABLE, "cited evidence missing or altered"
    failed = [r for r in det if r.check.outcome is CheckOutcome.FAIL]
    if failed:
        if any(r.opposite for r in failed):
            return VerificationStatus.CONTRADICTED, "evidence states the opposite"
        return VerificationStatus.UNSUPPORTED, "deterministic check failed: " + ", ".join(
            r.check.check_id for r in failed
        )
    if semantic is None:
        if purely_numeric:
            return VerificationStatus.VERIFIED, "numeric claim fully verified deterministically"
        return VerificationStatus.UNVERIFIABLE, "no semantic check was possible"
    if semantic.verdict is Verdict.CONTRADICTS:
        return VerificationStatus.CONTRADICTED, "verifier: evidence contradicts the claim"
    if semantic.verdict is Verdict.NOT_ENTAILED:
        return VerificationStatus.UNSUPPORTED, "verifier: evidence does not support the claim"
    if not span_ok:  # a verdict of ENTAILS/PARTIAL must quote the evidence verbatim
        return VerificationStatus.UNVERIFIABLE, "verifier's quoted span is not in the evidence"
    if semantic.verdict is Verdict.PARTIAL:
        return (
            VerificationStatus.PARTIALLY_SUPPORTED,
            "verifier: only part of the claim is supported",
        )
    return VerificationStatus.VERIFIED, "deterministic checks passed and evidence entails claim"


class VerificationCapability:
    name = AgentName.VERIFICATION
    version = VERSION
    permissions = frozenset(
        {Permission.READ_EVIDENCE, Permission.READ_SOURCE, Permission.WRITE_CLAIM,
         Permission.CALL_LLM}
    )  # fmt: skip

    def __init__(self, role: str = "verification", use_llm: bool = True) -> None:
        self._role = role
        self._use_llm = use_llm

    def run(self, task: AgentTask, tools: ToolGateway) -> AgentResult:
        claim_ids = [r.id for r in task.input_refs if r.kind == "claim"]
        if not claim_ids:
            return AgentResult(task_id=task.task_id, status=ResultStatus.OK, version=VERSION)
        claims = list(tools.call("get_claim", {"ids": claim_ids})["claims"])
        contradictions: list[ContradictionDraft] = []
        exhausted = False
        for c in claims:
            try:
                contradictions += self._verify(task, tools, c, allow_llm=not exhausted)
            except BudgetExceeded:
                exhausted = True  # record this and the remaining claims as UNVERIFIABLE
                contradictions += self._verify(task, tools, c, allow_llm=False)
        return AgentResult(
            task_id=task.task_id,
            status=ResultStatus.BUDGET_EXHAUSTED if exhausted else ResultStatus.OK,
            contradictions=tuple(contradictions),
            version=VERSION,
            error=None,
        )

    # ------------------------------------------------------------------------------------
    def _verify(
        self, task: AgentTask, tools: ToolGateway, claim: Mapping[str, Any], *, allow_llm: bool
    ) -> list[ContradictionDraft]:
        cid = str(claim["claim_id"])
        cited = [str(x["evidence_id"]) for x in claim["citations"]]
        checks: list[VerificationCheck] = []
        ev: list[dict[str, Any]] = []
        try:
            ev = list(tools.call("get_evidence", {"ids": cited})["evidence"])
            checks.append(_check("EVIDENCE_EXISTS", CheckOutcome.PASS))
            exists = True
        except (NotFound, PermissionDenied) as err:
            checks.append(_check("EVIDENCE_EXISTS", CheckOutcome.FAIL, str(err)))
            exists = False
        # what the verifier is shown - exactly this, nothing from the investigator (D-08)
        shown = {
            "claim": {
                "text": claim["text"],
                "structure": claim["structure"],
                "claim_kind": claim["claim_kind"],
                "citations": claim["citations"],
            },
            "evidence": [
                {
                    k: e[k]
                    for k in (
                        "evidence_id",
                        "evidence_type",
                        "summary_text",
                        "content",
                        "completeness",
                    )
                }  # fmt: skip
                for e in ev
            ],
        }
        seen_hash = content_hash(hashable(shown))
        det: list[ck.CheckResult] = []
        integrity = exists
        if exists:
            for e in ev:
                h = tools.call("verify_hash", {"evidence_id": e["evidence_id"]})
                ok = bool(h["hash_intact"]) and all(x["found"] and x["match"] for x in h["leaves"])
                if not ok:
                    integrity = False
                    checks.append(_check("HASH_INTACT", CheckOutcome.FAIL, f"{e['evidence_id']}"))
            if integrity:
                checks.append(_check("HASH_INTACT", CheckOutcome.PASS))
            det = [
                ck.check_entity_present(str(claim["structure"]["subject_ref"]), ev),
                ck.check_values(
                    claim["structure"], str(claim["text"]), str(claim["claim_kind"]), ev
                ),
                ck.check_date_in_window(claim["structure"].get("time_scope"), ev),
                ck.check_as_of(task.as_of, ev),
                ck.check_roles(claim["citations"]),
            ]
            checks += [r.check for r in det]

        semantic: EntailmentOut | None = None
        span_ok = False
        model_id = prompt_version = None
        det_clean = integrity and not any(r.check.outcome is CheckOutcome.FAIL for r in det)
        numeric = (
            det_clean
            and str(claim["claim_kind"]) in NUMERIC_KINDS
            and ck.to_decimal(claim["structure"].get("object_value")) is not None
            and all(
                r.check.outcome is CheckOutcome.PASS
                for r in det
                if r.check.check_id in ("ENTITY_PRESENT_IN_EVIDENCE", "AMOUNT_MATCHES")
            )
        )
        if det_clean and self._use_llm and allow_llm:
            try:
                out = tools.call(
                    "llm.generate_structured",
                    {
                        "prompt_id": PROMPT_ID,
                        "role": self._role,
                        "schema_name": "entailment",
                        "variables": shown,
                        "sensitive": {"ACCT": account_refs(shown)},
                    },
                )
                semantic = EntailmentOut.model_validate(out["parsed"])
                model_id, prompt_version = str(out["model_id"]), str(out["prompt_version"])
                span_ok = semantic.verdict in (Verdict.NOT_ENTAILED, Verdict.CONTRADICTS) or (
                    ck.span_in_evidence(semantic.quoted_span, ev)
                )
                checks.append(
                    _check(
                        "ENTAILMENT",
                        CheckOutcome.PASS
                        if semantic.verdict is Verdict.ENTAILS and span_ok
                        else CheckOutcome.FAIL,
                        f"{semantic.verdict.value}: {semantic.rationale}"
                        + ("" if span_ok else " [quoted span not found in evidence]"),
                        CheckKind.SEMANTIC,
                    )
                )
            except (LlmError, PermissionDenied) as err:
                log.warning("verification.semantic_unavailable", claim=cid, error=str(err)[:200])
                checks.append(
                    _check("ENTAILMENT", CheckOutcome.NA, f"verifier unavailable: {err}"[:300],
                           CheckKind.SEMANTIC)
                )  # fmt: skip
        else:
            why = "numeric claim fully verified deterministically" if numeric else "not run"
            checks.append(_check("ENTAILMENT", CheckOutcome.NA, why, CheckKind.SEMANTIC))

        status, reason = derive_status(
            det, integrity_ok=integrity, semantic=semantic, span_ok=span_ok, purely_numeric=numeric
        )
        tools.call(
            "write_verification",
            {
                "claim_id": cid,
                "status": status.value,
                "checks": [c.model_dump(mode="json") for c in checks],
                "rationale": reason + (f" | {semantic.rationale}" if semantic else ""),
                "verifier_name": "verification_agent",
                "verifier_version": VERSION,
                "model_id": model_id,
                "prompt_version": prompt_version,
                "inputs_seen_hash": seen_hash,
            },
        )
        if status is VerificationStatus.CONTRADICTED and ev:
            seen_ev = {str(e["evidence_id"]) for e in ev}
            # the shared definition (analytics.contradictions) keeps this record identical to the
            # one the Contradiction agent derives, so storing both is idempotent
            return claim_vs_evidence_findings(
                task.case_id,
                cid,
                [
                    (str(c["evidence_id"]), SupportRole(c["support_role"]))
                    for c in claim["citations"]
                    if str(c["evidence_id"]) in seen_ev
                ],
                contradicted=True,
            )
        return []
