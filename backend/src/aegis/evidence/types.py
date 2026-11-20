"""Read-side records returned by the evidence store (the write side uses the frozen drafts in
`aegis.domain.evidence`)."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from aegis.domain.evidence import (
    ClaimDraft,
    ContradictionStatus,
    ContradictionType,
    EvidenceDraft,
    InputRefType,
    Severity,
    VerificationDraft,
    VerificationStatus,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StoredEvidence(_Frozen):
    evidence_id: str
    content_hash: str
    created_at: datetime
    draft: EvidenceDraft


class StoredClaim(_Frozen):
    claim_id: str
    created_at: datetime
    draft: ClaimDraft
    status: VerificationStatus | None = None  # None = DRAFT (never verified)


class StoredVerification(_Frozen):
    verification_id: str
    created_at: datetime
    draft: VerificationDraft


class ContradictionView(_Frozen):
    contradiction_id: str
    case_id: str
    type: ContradictionType
    severity: Severity
    status: ContradictionStatus
    member_ids: tuple[str, ...] = ()


class LeafHashes(Protocol):
    """Resolves the stored content hash of a non-evidence provenance leaf (SOURCE record,
    DOCUMENT, REFERENCE list). None = unknown to the system (a provenance violation)."""

    def hash_of(self, ref_type: InputRefType, ref_id: str) -> str | None: ...
