"""Shared evidence / claim / verification / contradiction contracts (EVIDENCE_MODEL.md).

These models are the single contract between Phase 3 (evidence store, runtime, agents) and
Phase 4 (entity resolution, contradictions). Changing them requires a decisions.md entry.
All records are immutable once built (frozen) and match the insert-only tables in migration 0009.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis.common.hashing import content_hash
from aegis.common.timeutil import require_utc

EVIDENCE_SCHEMA_VERSION = 1


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceType(StrEnum):
    SIGNAL = "SIGNAL"
    TXN_AGGREGATE = "TXN_AGGREGATE"
    TXN_VELOCITY = "TXN_VELOCITY"
    TXN_STRUCTURING_CHECK = "TXN_STRUCTURING_CHECK"
    TXN_RAPID_MOVEMENT = "TXN_RAPID_MOVEMENT"
    TXN_COUNTERPARTY_SUMMARY = "TXN_COUNTERPARTY_SUMMARY"
    GRAPH_NEIGHBOURHOOD = "GRAPH_NEIGHBOURHOOD"
    GRAPH_CYCLES = "GRAPH_CYCLES"
    GRAPH_FANIN_FANOUT = "GRAPH_FANIN_FANOUT"
    GRAPH_FLOW_THROUGH = "GRAPH_FLOW_THROUGH"
    GRAPH_COMMUNITY = "GRAPH_COMMUNITY"
    GRAPH_CENTRALITY = "GRAPH_CENTRALITY"
    BEHAV_SELF_DEVIATION = "BEHAV_SELF_DEVIATION"
    BEHAV_PEER_DEVIATION = "BEHAV_PEER_DEVIATION"
    BEHAV_MODEL_SCORE = "BEHAV_MODEL_SCORE"
    REF_LIST_MATCH = "REF_LIST_MATCH"
    DOC_EXTRACTION = "DOC_EXTRACTION"
    ER_LINK_EVIDENCE = "ER_LINK_EVIDENCE"
    HUMAN_NOTE = "HUMAN_NOTE"


class ProducerKind(StrEnum):
    TOOL = "TOOL"
    MODEL = "MODEL"
    LLM = "LLM"
    HUMAN = "HUMAN"


class Producer(_Frozen):
    kind: ProducerKind
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    params_hash: str | None = None


class InputRefType(StrEnum):
    SOURCE = "SOURCE"
    EVIDENCE = "EVIDENCE"
    DOCUMENT = "DOCUMENT"
    REFERENCE = "REFERENCE"


class EvidenceInput(_Frozen):
    ref_type: InputRefType
    ref_id: str = Field(min_length=1)
    ref_hash: str = Field(min_length=1)


class Completeness(StrEnum):
    FULL = "FULL"
    TRUNCATED = "TRUNCATED"
    INSUFFICIENT = "INSUFFICIENT"


class EvidenceDraft(_Frozen):
    """What a producer hands to the evidence store. The store assigns the id and timestamps."""

    case_id: str
    run_id: str | None = None
    step_id: str | None = None
    evidence_type: EvidenceType
    content: dict[str, Any]
    summary_text: str = Field(min_length=1)
    producer: Producer
    as_of: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    inputs: tuple[EvidenceInput, ...] = ()
    completeness: Completeness = Completeness.FULL
    quality_notes: str | None = None
    scores: dict[str, Any] | None = (
        None  # {score, calibrated_probability?, calibration_artefact_id?}
    )
    supersedes: str | None = None

    @field_validator("as_of", "window_start", "window_end")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else require_utc(v)

    @model_validator(mode="after")
    def _checks(self) -> "EvidenceDraft":
        if self.producer.kind is not ProducerKind.HUMAN and not self.inputs:
            raise ValueError("non-human evidence must list its inputs (provenance)")
        if self.scores and self.scores.get("calibrated_probability") is not None:
            if not self.scores.get("calibration_artefact_id"):
                raise ValueError("calibrated_probability requires calibration_artefact_id (D-06)")
        return self

    def content_hash(self) -> str:
        """SHA-256 over (type, content, producer, as_of, window, inputs) - EVIDENCE_MODEL 2."""
        return content_hash(
            {
                "type": self.evidence_type.value,
                "content": self.content,
                "producer": self.producer.model_dump(mode="json"),
                "as_of": self.as_of,
                "window": [self.window_start, self.window_end],
                "inputs": [i.model_dump(mode="json") for i in self.inputs],
                "schema_version": EVIDENCE_SCHEMA_VERSION,
            }
        )


class SupportRole(StrEnum):
    SUPPORTS = "SUPPORTS"
    PARTIALLY_SUPPORTS = "PARTIALLY_SUPPORTS"
    CONTEXT = "CONTEXT"
    CONTRADICTS = "CONTRADICTS"


class ClaimKind(StrEnum):
    FACT_OBSERVATION = "FACT_OBSERVATION"
    AGGREGATE = "AGGREGATE"
    INFERENCE = "INFERENCE"
    TYPOLOGY_HYPOTHESIS = "TYPOLOGY_HYPOTHESIS"
    RECOMMENDATION = "RECOMMENDATION"


class SelfReportedConfidence(StrEnum):
    """Ordinal label reported by an LLM about itself. NOT a probability (D-06)."""

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Citation(_Frozen):
    evidence_id: str
    support_role: SupportRole


class ClaimStructure(_Frozen):
    subject_ref: str
    predicate: str
    object_value: str | float | int | bool | None = None
    unit: str | None = None
    time_scope: str | None = None


class AuthorKind(StrEnum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"


class ClaimDraft(_Frozen):
    case_id: str
    run_id: str | None = None
    author_kind: AuthorKind
    author_name: str
    author_version: str
    structure: ClaimStructure
    text: str = Field(min_length=1)
    claim_kind: ClaimKind
    citations: tuple[Citation, ...]
    self_reported_confidence: SelfReportedConfidence = SelfReportedConfidence.NONE
    depends_on_er_links: tuple[str, ...] = ()  # ER link ids not CONFIRMED when the claim was made
    supersedes: str | None = None

    @model_validator(mode="after")
    def _needs_citation(self) -> "ClaimDraft":
        if not self.citations:
            raise ValueError("a claim must cite at least one evidence id (D-07)")
        ids = [c.evidence_id for c in self.citations]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate citation of the same evidence id")
        return self


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIABLE = "UNVERIFIABLE"


class CheckKind(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    SEMANTIC = "SEMANTIC"


class CheckOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105 - an enum label, not a credential
    FAIL = "FAIL"
    NA = "NA"


class VerificationCheck(_Frozen):
    check_id: str
    kind: CheckKind
    outcome: CheckOutcome
    detail: str = ""


class VerificationDraft(_Frozen):
    claim_id: str
    status: VerificationStatus
    checks: tuple[VerificationCheck, ...]
    rationale: str
    verifier_name: str
    verifier_version: str
    model_id: str | None = None
    prompt_version: str | None = None
    inputs_seen_hash: str  # hash of exactly what the verifier was shown (isolation test, D-08)

    @model_validator(mode="after")
    def _never_verified_on_failed_check(self) -> "VerificationDraft":
        failed = any(c.outcome is CheckOutcome.FAIL for c in self.checks)
        if self.status is VerificationStatus.VERIFIED and failed:
            raise ValueError("a claim cannot be VERIFIED when any check failed")
        return self


class ContradictionType(StrEnum):
    ATTRIBUTE_CONFLICT = "ATTRIBUTE_CONFLICT"
    TIMELINE_IMPOSSIBLE = "TIMELINE_IMPOSSIBLE"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    CLAIM_VS_EVIDENCE = "CLAIM_VS_EVIDENCE"
    ER_CONFLICT = "ER_CONFLICT"
    SOURCE_DISAGREEMENT = "SOURCE_DISAGREEMENT"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ContradictionStatus(StrEnum):
    OPEN = "OPEN"
    EXPLAINED = "EXPLAINED"
    RESOLVED_BY_HUMAN = "RESOLVED_BY_HUMAN"
    DISMISSED = "DISMISSED"


class MemberType(StrEnum):
    EVIDENCE = "EVIDENCE"
    CLAIM = "CLAIM"


class ContradictionMember(_Frozen):
    member_type: MemberType
    member_id: str
    role: str = ""


class ContradictionDraft(_Frozen):
    case_id: str
    type: ContradictionType
    severity: Severity
    detector_name: str
    detector_version: str
    members: tuple[ContradictionMember, ...]
    explanation_proposal: dict[str, Any] | None = None  # a proposal; never resolves anything

    @model_validator(mode="after")
    def _two_members(self) -> "ContradictionDraft":
        if len(self.members) < 2:
            raise ValueError("a contradiction links at least two items")
        return self
