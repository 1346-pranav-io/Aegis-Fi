"""Evidence store interface plus the in-memory implementation used by unit tests and fixture
experiments. The Postgres implementation is in `pg_store.py`; both pass the same contract tests.

Rules enforced here (the database enforces them again, migration 0009):
- creation is idempotent per (case, content hash)             [FR-EVD-03]
- EVIDENCE inputs must exist in the same case and carry the stored hash
- SOURCE/DOCUMENT/REFERENCE input hashes must equal the hash the system holds for the leaf
- a claim cites only existing evidence of its own case; HUMAN_NOTE is context only (Q-10)
- nothing is updated or deleted; corrections supersede                     [D-04]
"""

import copy
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Protocol

from aegis.common.errors import AegisValidationError, NotFound
from aegis.common.ids import new_id
from aegis.domain.evidence import (
    ClaimDraft,
    ContradictionDraft,
    ContradictionStatus,
    EvidenceDraft,
    EvidenceType,
    InputRefType,
    MemberType,
    SupportRole,
    VerificationDraft,
)
from aegis.evidence.drafts import with_query_refs
from aegis.evidence.types import (
    ContradictionView,
    LeafHashes,
    StoredClaim,
    StoredEvidence,
    StoredVerification,
)

UNRESOLVED_STATUSES = (ContradictionStatus.OPEN, ContradictionStatus.EXPLAINED)


class EvidenceStore(Protocol):
    def create_evidence(self, draft: EvidenceDraft) -> tuple[StoredEvidence, bool]:
        """Returns (evidence, created). created=False when identical content already exists."""
        ...

    def create_claim(self, draft: ClaimDraft) -> StoredClaim: ...
    def record_verification(self, draft: VerificationDraft) -> StoredVerification: ...
    def create_contradiction(self, draft: ContradictionDraft) -> ContradictionView: ...
    def get_evidence(self, evidence_id: str) -> StoredEvidence: ...
    def get_claim(self, claim_id: str) -> StoredClaim: ...
    def list_evidence(self, case_id: str) -> list[StoredEvidence]: ...
    def list_claims(self, case_id: str) -> list[StoredClaim]: ...
    def verifications(self, claim_id: str) -> list[StoredVerification]: ...
    def unresolved_contradictions(self, case_id: str) -> list[ContradictionView]:
        """OPEN or EXPLAINED contradictions of the case (reads evidence.contradiction_current)."""
        ...


_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def evidence_id_for(case_id: str, content_hash: str) -> str:
    """Deterministic evidence id: a function of (case, content hash), shaped like a ULID.

    Content-addressing makes ids stable across runs and processes, which is what lets a recorded
    LLM run replay (prompts contain evidence ids) and makes idempotent re-creation trivial."""
    import hashlib

    n = int.from_bytes(hashlib.sha256(f"{case_id}|{content_hash}".encode()).digest()[:17], "big")
    chars = []
    for _ in range(26):
        chars.append(_B32[n & 31])
        n >>= 5
    return "ev_" + "".join(reversed(chars))


def normalised(draft: EvidenceDraft) -> EvidenceDraft:
    """Inputs sorted by (type, id) so the content hash does not depend on caller order."""
    ordered = tuple(sorted(draft.inputs, key=lambda i: (i.ref_type.value, i.ref_id)))
    return draft.model_copy(update={"inputs": ordered})


def validate_new_evidence(
    draft: EvidenceDraft,
    stored_hash_of: Callable[[str], tuple[str, str] | None],
    leaves: LeafHashes,
) -> None:
    """`stored_hash_of(evidence_id)` -> (case_id, content_hash) or None."""
    for inp in draft.inputs:
        if inp.ref_type is InputRefType.EVIDENCE:
            got = stored_hash_of(inp.ref_id)
            if got is None:
                raise AegisValidationError(f"input evidence {inp.ref_id} does not exist")
            if got[0] != draft.case_id:
                raise AegisValidationError(f"input evidence {inp.ref_id} belongs to another case")
            if got[1] != inp.ref_hash:
                raise AegisValidationError(f"input evidence {inp.ref_id}: hash mismatch")
        else:
            held = leaves.hash_of(inp.ref_type, inp.ref_id)
            if held is None:
                raise AegisValidationError(f"unknown {inp.ref_type.value} input {inp.ref_id}")
            if held != inp.ref_hash:
                raise AegisValidationError(
                    f"{inp.ref_type.value} input {inp.ref_id}: hash differs from the stored record"
                )


def validate_new_claim(
    draft: ClaimDraft, evidence_lookup: Callable[[str], StoredEvidence | None]
) -> None:
    for c in draft.citations:
        ev = evidence_lookup(c.evidence_id)
        if ev is None:
            raise AegisValidationError(f"claim cites unknown evidence {c.evidence_id}")
        if ev.draft.case_id != draft.case_id:
            raise AegisValidationError(f"claim cites evidence of another case: {c.evidence_id}")
        if ev.draft.evidence_type is EvidenceType.HUMAN_NOTE and c.support_role in (
            SupportRole.SUPPORTS,
            SupportRole.PARTIALLY_SUPPORTS,
        ):
            raise AegisValidationError("HUMAN_NOTE evidence is context only (Q-10)")


class InMemoryEvidenceStore:
    """Dict-backed store with snapshot transactions (rollback on exception)."""

    def __init__(self, leaves: LeafHashes) -> None:
        self._leaves = with_query_refs(leaves)
        self._evidence: dict[str, StoredEvidence] = {}
        self._by_hash: dict[tuple[str, str], str] = {}
        self._claims: dict[str, StoredClaim] = {}
        self._verifs: dict[str, list[StoredVerification]] = {}
        self._contradictions: dict[str, ContradictionView] = {}
        self._tick = 0

    def _state(self) -> tuple[object, ...]:
        return (
            self._evidence,
            self._by_hash,
            self._claims,
            self._verifs,
            self._contradictions,
            self._tick,
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        snap = copy.deepcopy(self._state())
        try:
            yield
        except BaseException:
            (
                self._evidence,
                self._by_hash,
                self._claims,
                self._verifs,
                self._contradictions,
                self._tick,
            ) = snap  # type: ignore[assignment]
            raise

    def _now(self) -> datetime:
        self._tick += 1
        return datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=self._tick)

    def create_evidence(self, draft: EvidenceDraft) -> tuple[StoredEvidence, bool]:
        draft = normalised(draft)
        h = draft.content_hash()
        existing = self._by_hash.get((draft.case_id, h))
        if existing:
            return self._evidence[existing], False
        validate_new_evidence(
            draft,
            lambda i: (
                (self._evidence[i].draft.case_id, self._evidence[i].content_hash)
                if i in self._evidence
                else None
            ),
            self._leaves,
        )
        ev = StoredEvidence(
            evidence_id=evidence_id_for(draft.case_id, h),
            content_hash=h,
            created_at=self._now(),
            draft=draft,
        )
        self._evidence[ev.evidence_id] = ev
        self._by_hash[(draft.case_id, h)] = ev.evidence_id
        return ev, True

    def create_claim(self, draft: ClaimDraft) -> StoredClaim:
        validate_new_claim(draft, self._evidence.get)
        c = StoredClaim(claim_id=new_id("clm"), created_at=self._now(), draft=draft)
        self._claims[c.claim_id] = c
        return c

    def record_verification(self, draft: VerificationDraft) -> StoredVerification:
        if draft.claim_id not in self._claims:
            raise NotFound(draft.claim_id)
        v = StoredVerification(verification_id=new_id("ver"), created_at=self._now(), draft=draft)
        self._verifs.setdefault(draft.claim_id, []).append(v)
        return v

    def create_contradiction(self, draft: ContradictionDraft) -> ContradictionView:
        for m in draft.members:
            case = (
                self._evidence[m.member_id].draft.case_id
                if m.member_type is MemberType.EVIDENCE and m.member_id in self._evidence
                else self._claims[m.member_id].draft.case_id
                if m.member_type is MemberType.CLAIM and m.member_id in self._claims
                else None
            )
            if case != draft.case_id:
                raise AegisValidationError(f"contradiction member {m.member_id} not in the case")
        v = ContradictionView(
            contradiction_id=new_id("ctr"),
            case_id=draft.case_id,
            type=draft.type,
            severity=draft.severity,
            status=ContradictionStatus.OPEN,
            member_ids=tuple(m.member_id for m in draft.members),
        )
        self._contradictions[v.contradiction_id] = v
        return v

    def get_evidence(self, evidence_id: str) -> StoredEvidence:
        try:
            return self._evidence[evidence_id]
        except KeyError:
            raise NotFound(evidence_id) from None

    def get_claim(self, claim_id: str) -> StoredClaim:
        try:
            c = self._claims[claim_id]
        except KeyError:
            raise NotFound(claim_id) from None
        vs = self._verifs.get(claim_id)
        return c.model_copy(update={"status": vs[-1].draft.status if vs else None})

    def list_evidence(self, case_id: str) -> list[StoredEvidence]:
        return [e for e in self._evidence.values() if e.draft.case_id == case_id]

    def list_claims(self, case_id: str) -> list[StoredClaim]:
        return [self.get_claim(i) for i, c in self._claims.items() if c.draft.case_id == case_id]

    def verifications(self, claim_id: str) -> list[StoredVerification]:
        return list(self._verifs.get(claim_id, []))

    def unresolved_contradictions(self, case_id: str) -> list[ContradictionView]:
        return [
            c
            for c in self._contradictions.values()
            if c.case_id == case_id and c.status in UNRESOLVED_STATUSES
        ]

    def resolve_contradiction_for_test(self, contradiction_id: str) -> None:
        """Test helper standing in for a human action (agents have no such path)."""
        c = self._contradictions[contradiction_id]
        self._contradictions[contradiction_id] = c.model_copy(
            update={"status": ContradictionStatus.RESOLVED_BY_HUMAN}
        )


class DictLeafHashes:
    """Leaf resolver backed by a dict; used in tests and fixture cases."""

    def __init__(self, entries: dict[tuple[InputRefType, str], str] | None = None) -> None:
        self.entries: dict[tuple[InputRefType, str], str] = dict(entries or {})

    def add(self, ref_type: InputRefType, ref_id: str, h: str) -> None:
        self.entries[(ref_type, ref_id)] = h

    def hash_of(self, ref_type: InputRefType, ref_id: str) -> str | None:
        return self.entries.get((ref_type, ref_id))
