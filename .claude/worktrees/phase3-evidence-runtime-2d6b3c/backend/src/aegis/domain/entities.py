"""Entity-resolution contracts (DATA_SCHEMA derived.*, FR-ER-*, EVIDENCE_MODEL 7).

Defined here so Phase 3 (claims record `depends_on_er_links`; the Investigation agent reads link
status) and Phase 4 (the ER pipeline writes links) share one vocabulary. Tables are created by
Phase 4's own migration; this module holds the enums and the read-side protocol only.
"""

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class LinkMethod(StrEnum):
    EXACT_ID = "EXACT_ID"  # verified identifier equality - the only method that may auto-confirm
    FUZZY_NAME = "FUZZY_NAME"
    ATTRIBUTE_MATCH = "ATTRIBUTE_MATCH"
    MANUAL = "MANUAL"


class LinkStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ERLinkView(BaseModel):
    """Read-only view of the latest state of one mention -> entity link."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    link_id: str
    mention_id: str
    entity_id: str
    method: LinkMethod
    match_score: float  # a similarity score, never a probability (D-06)
    status: LinkStatus
    scorer_version: str = Field(min_length=1)


class ERLinkReader(Protocol):
    """What Phase 3 needs from Phase 4. Implemented by the entities package."""

    def latest_links(self, link_ids: list[str]) -> list[ERLinkView]: ...

    def unconfirmed_dependencies(self, link_ids: list[str]) -> list[str]:
        """Subset of `link_ids` whose latest status is not CONFIRMED."""
        ...
