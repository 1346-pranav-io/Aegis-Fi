"""Helpers specialist capabilities share when building evidence (not capability logic)."""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from aegis.domain.evidence import (
    Completeness,
    EvidenceDraft,
    EvidenceInput,
    EvidenceType,
    InputRefType,
    Producer,
    ProducerKind,
)
from aegis.evidence.canon import hashable


def source_inputs(rows: Iterable[Mapping[str, Any]]) -> tuple[EvidenceInput, ...]:
    """SOURCE provenance edges for tool rows ({txn_id, content_hash, ...}); sorted, deduplicated."""
    seen: dict[str, str] = {}
    for r in rows:
        seen[str(r["txn_id"])] = str(r["content_hash"])
    return tuple(
        EvidenceInput(ref_type=InputRefType.SOURCE, ref_id=k, ref_hash=v)
        for k, v in sorted(seen.items())
    )


def tool_draft(
    *,
    case_id: str,
    evidence_type: EvidenceType,
    content: Mapping[str, Any],
    summary: str,
    producer_name: str,
    producer_version: str,
    as_of: datetime,
    window: tuple[datetime, datetime],
    inputs: tuple[EvidenceInput, ...],
    completeness: Completeness = Completeness.FULL,
    notes: str | None = None,
) -> EvidenceDraft:
    return EvidenceDraft(
        case_id=case_id,
        evidence_type=evidence_type,
        content=hashable(dict(content)),
        summary_text=summary,
        producer=Producer(kind=ProducerKind.TOOL, name=producer_name, version=producer_version),
        as_of=as_of,
        window_start=window[0],
        window_end=window[1],
        inputs=inputs,
        completeness=completeness,
        quality_notes=notes,
    )


# ---- "query" references: provenance for evidence about ABSENCE of data ----------------------
# A deterministic tool that finds no rows still has to record what it asked (AG-02: empty window
# is OK with an empty-finding evidence item, not a failure). Non-human evidence needs an input,
# so the exact query becomes a self-certifying REFERENCE: id and hash are both the digest of the
# query descriptor. It proves WHICH question was asked as of when, not that the answer was empty;
# the latter rests on the source reader being correct.
QUERY_PREFIX = "query:"


def query_input(descriptor: Mapping[str, Any]) -> EvidenceInput:
    from aegis.common.hashing import content_hash

    digest = content_hash(hashable(dict(descriptor)))
    return EvidenceInput(
        ref_type=InputRefType.REFERENCE, ref_id=f"{QUERY_PREFIX}{digest}", ref_hash=digest
    )


class WithQueryRefs:
    """LeafHashes wrapper that resolves self-certifying query references."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def hash_of(self, ref_type: InputRefType, ref_id: str) -> str | None:
        if ref_type is InputRefType.REFERENCE and ref_id.startswith(QUERY_PREFIX):
            return ref_id[len(QUERY_PREFIX) :]
        return self._inner.hash_of(ref_type, ref_id) if self._inner is not None else None


def with_query_refs(leaves: Any) -> Any:
    return leaves if isinstance(leaves, WithQueryRefs) else WithQueryRefs(leaves)
