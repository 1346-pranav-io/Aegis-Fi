"""Shared builders for Phase 3 tests (kept out of tests/integration to avoid cross-stream edits)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from aegis.domain.evidence import (
    AuthorKind,
    Citation,
    ClaimDraft,
    ClaimKind,
    ClaimStructure,
    EvidenceDraft,
    EvidenceInput,
    EvidenceType,
    InputRefType,
    Producer,
    ProducerKind,
    SupportRole,
)
from aegis.evidence.store import DictLeafHashes

T0 = datetime(2022, 9, 10, tzinfo=UTC)
TOOL = Producer(kind=ProducerKind.TOOL, name="txn_agent", version="1")
HUMAN = Producer(kind=ProducerKind.HUMAN, name="analyst", version="1")


class World:
    """Leaf registry + helpers; `src(n)` registers a SOURCE leaf and returns the input for it."""

    def __init__(self) -> None:
        self.leaves = DictLeafHashes()

    def src(self, ref_id: str, h: str | None = None) -> EvidenceInput:
        h = h or f"hash-{ref_id}"
        self.leaves.add(InputRefType.SOURCE, ref_id, h)
        return EvidenceInput(ref_type=InputRefType.SOURCE, ref_id=ref_id, ref_hash=h)


def ev_draft(
    case_id: str,
    inputs: tuple[EvidenceInput, ...],
    *,
    content: dict[str, Any] | None = None,
    etype: EvidenceType = EvidenceType.TXN_AGGREGATE,
    producer: Producer = TOOL,
    as_of: datetime = T0,
    summary: str = "summary",
) -> EvidenceDraft:
    return EvidenceDraft(
        case_id=case_id,
        evidence_type=etype,
        content=content if content is not None else {"n": 3},
        summary_text=summary,
        producer=producer,
        as_of=as_of,
        window_start=as_of - timedelta(days=1),
        window_end=as_of,
        inputs=inputs,
    )


def claim_draft(
    case_id: str,
    evidence_ids: list[str],
    *,
    role: SupportRole = SupportRole.SUPPORTS,
    text: str = "Account sent 3 transactions",
    value: Any = 3,
) -> ClaimDraft:
    return ClaimDraft(
        case_id=case_id,
        author_kind=AuthorKind.AGENT,
        author_name="investigation",
        author_version="1",
        structure=ClaimStructure(
            subject_ref="acct_A", predicate="sent_count", object_value=value, time_scope="window"
        ),
        text=text,
        claim_kind=ClaimKind.AGGREGATE,
        citations=tuple(Citation(evidence_id=e, support_role=role) for e in evidence_ids),
    )
