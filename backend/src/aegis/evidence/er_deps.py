"""Entity-resolution dependencies of evidence, read from the evidence itself.

An item of type ER_LINK_EVIDENCE names the link (`link_id`) and any merges (`merge_ids`) the
investigation relied on. A claim that cites such evidence depends on those ids, whether or not the
agent that wrote it said so (an agent can forget; the server does not)."""

from collections.abc import Iterable

from aegis.domain.evidence import EvidenceType
from aegis.evidence.store import EvidenceStore


def er_dependencies_of_evidence(store: EvidenceStore, evidence_ids: Iterable[str]) -> set[str]:
    deps: set[str] = set()
    for eid in evidence_ids:
        ev = store.get_evidence(eid).draft
        if ev.evidence_type is not EvidenceType.ER_LINK_EVIDENCE:
            continue
        link_id = ev.content.get("link_id")
        if isinstance(link_id, str):
            deps.add(link_id)
        merge_ids = ev.content.get("merge_ids")
        if isinstance(merge_ids, list):
            deps |= {m for m in merge_ids if isinstance(m, str)}
    return deps
