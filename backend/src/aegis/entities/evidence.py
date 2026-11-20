"""ER link evidence: the producer of `ER_LINK_EVIDENCE` items (EVIDENCE_MODEL 7).

An investigation that relies on a resolved entity records the exact link it used, so the claim's
dependency on that link is visible to the provenance walk, to the contradiction detectors
(ER_CONFLICT) and to re-verification when the link later changes. The content follows the shared
declaration documented in `analytics.contradictions` and read by `services.reverify`.
"""

from datetime import datetime

from aegis.domain.entities import ERLinkView
from aegis.domain.evidence import EvidenceDraft, EvidenceType, Producer, ProducerKind
from aegis.evidence.canon import hashable
from aegis.evidence.drafts import query_input

PRODUCER_NAME = "er_pipeline"
PRODUCER_VERSION = "1"


def er_link_evidence(
    case_id: str,
    link: ERLinkView,
    *,
    as_of: datetime,
    merge_ids: tuple[str, ...] = (),
    run_id: str | None = None,
    step_id: str | None = None,
) -> EvidenceDraft:
    """Evidence stating "the investigation used this link in this state, as of this time".

    The input is a self-certifying query reference (what was asked of `derived.er_link_current`
    and when); it proves which question was asked, not that the answer was right (open decision
    P-3). `match_score` is stored as a string: it is a similarity score, never a probability
    (D-06), and content hashes forbid floats."""
    content = {
        "link_id": link.link_id,
        "mention_id": link.mention_id,
        "entity_id": link.entity_id,
        "method": link.method.value,
        "status": link.status.value,
        "match_score": f"{link.match_score:.6f}",
        "scorer_version": link.scorer_version,
        "merge_ids": list(merge_ids),
    }
    return EvidenceDraft(
        case_id=case_id,
        run_id=run_id,
        step_id=step_id,
        evidence_type=EvidenceType.ER_LINK_EVIDENCE,
        content=hashable(content),
        summary_text=(
            f"ER link {link.link_id}: mention {link.mention_id} -> entity {link.entity_id} "
            f"({link.method.value}, {link.status.value})"
        ),
        producer=Producer(kind=ProducerKind.TOOL, name=PRODUCER_NAME, version=PRODUCER_VERSION),
        as_of=as_of,
        inputs=(
            query_input(
                {"table": "derived.er_link_current", "link_id": link.link_id, "as_of": as_of}
            ),
        ),
    )
