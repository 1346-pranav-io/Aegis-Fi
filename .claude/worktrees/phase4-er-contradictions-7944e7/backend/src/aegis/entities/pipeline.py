"""Pure ER pipeline: mention -> candidate entities -> link proposals (no database, no clock).

Policy (FR-ER-02, D-09d): the only link that may be produced already CONFIRMED is an exact match
on an allow-listed verified identifier that identifies exactly ONE entity. Every other link is
PROPOSED, however high its score. Up to `top_k` candidates are kept with their scores so the
alternatives stay visible (EVIDENCE_MODEL 7).
"""

from dataclasses import dataclass, field

from aegis.domain.entities import LinkMethod, LinkStatus
from aegis.entities.blocking import DEFAULT_MAX_BLOCK, BlockIndex
from aegis.entities.similarity import SCORER_VERSION, name_similarity

# Must mirror derived.er_exact_identifier_type (the database re-checks equality at insert).
EXACT_IDENTIFIER_TYPES: frozenset[str] = frozenset({"account_number", "entity_id"})
DEFAULT_PROPOSE_THRESHOLD = 0.80
DEFAULT_TOP_K = 3


@dataclass(frozen=True)
class MentionRecord:
    mention_id: str
    normalised_name: str
    identifiers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EntityRecord:
    entity_id: str
    normalised_name: str
    identifiers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkProposal:
    mention_id: str
    entity_id: str
    method: LinkMethod
    match_score: float  # similarity, never a probability
    status: LinkStatus  # PROPOSED, or CONFIRMED only for a unique exact identifier match
    features: dict[str, object]
    scorer_version: str = SCORER_VERSION


class EntityIndex:
    def __init__(self, entities: list[EntityRecord], max_block: int = DEFAULT_MAX_BLOCK) -> None:
        self._entities = {e.entity_id: e for e in entities}
        self._blocks = BlockIndex(max_block)
        self._blocks.extend((e.entity_id, e.normalised_name) for e in entities)
        self._by_identifier: dict[tuple[str, str], list[str]] = {}
        for e in sorted(entities, key=lambda x: x.entity_id):
            for t, v in e.identifiers.items():
                if t in EXACT_IDENTIFIER_TYPES and v:
                    self._by_identifier.setdefault((t, v), []).append(e.entity_id)

    def entity(self, entity_id: str) -> EntityRecord:
        return self._entities[entity_id]

    def by_identifier(self, id_type: str, value: str) -> list[str]:
        return list(self._by_identifier.get((id_type, value), ()))

    def candidates(self, normalised_name: str) -> list[str]:
        return list(self._blocks.candidates(normalised_name))


def propose_links(
    mention: MentionRecord,
    index: EntityIndex,
    *,
    propose_threshold: float = DEFAULT_PROPOSE_THRESHOLD,
    top_k: int = DEFAULT_TOP_K,
) -> list[LinkProposal]:
    """Deterministic: same inputs give the same proposals in the same order."""
    exact: list[LinkProposal] = []
    ambiguous: list[LinkProposal] = []
    for id_type in sorted(mention.identifiers):
        value = mention.identifiers[id_type]
        if id_type not in EXACT_IDENTIFIER_TYPES or not value:
            continue
        owners = index.by_identifier(id_type, value)
        if len(owners) == 1:
            exact.append(
                LinkProposal(
                    mention.mention_id,
                    owners[0],
                    LinkMethod.EXACT_ID,
                    1.0,
                    LinkStatus.CONFIRMED,
                    {"identifier_type": id_type},
                )  # fmt: skip
            )
        elif len(owners) > 1:  # one identifier on several entities: a data conflict, never auto
            for o in owners:
                ambiguous.append(
                    LinkProposal(
                        mention.mention_id,
                        o,
                        LinkMethod.ATTRIBUTE_MATCH,
                        1.0,
                        LinkStatus.PROPOSED,
                        {
                            "identifier_type": id_type,
                            "ambiguous_identifier": True,
                            "shared_by": len(owners),
                        },
                    )  # fmt: skip
                )
    if exact:
        # distinct entities only: two identifiers pointing at different entities stay proposals
        targets = {p.entity_id for p in exact}
        if len(targets) == 1:
            return exact[:1]
        return [
            LinkProposal(p.mention_id, p.entity_id, LinkMethod.ATTRIBUTE_MATCH, 1.0,
                         LinkStatus.PROPOSED, {**p.features, "conflicting_identifiers": True})
            for p in exact
        ]  # fmt: skip
    if ambiguous:
        return ambiguous
    scored: list[tuple[float, str]] = []
    for eid in index.candidates(mention.normalised_name):
        s = name_similarity(mention.normalised_name, index.entity(eid).normalised_name)
        if s >= propose_threshold:
            scored.append((s, eid))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [
        LinkProposal(
            mention.mention_id,
            eid,
            LinkMethod.FUZZY_NAME,
            round(s, 6),
            LinkStatus.PROPOSED,
            {"name_similarity": round(s, 6), "rank": i + 1},
        )
        for i, (s, eid) in enumerate(scored[:top_k])
    ]
