"""Case workflow contracts (FR-CASE-*, D-09). The DB trigger enforces the same transition table."""

from enum import StrEnum


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    PENDING_REVIEW = "PENDING_REVIEW"
    CLOSED = "CLOSED"


class Disposition(StrEnum):
    CONFIRMED_SUSPICIOUS = "CONFIRMED_SUSPICIOUS"
    NOT_SUSPICIOUS = "NOT_SUSPICIOUS"
    INCONCLUSIVE = "INCONCLUSIVE"


class ApprovalAction(StrEnum):
    """Consequential actions that need a stored human approval (D-09)."""

    REGULATORY_FILING_RECOMMENDATION = "REGULATORY_FILING_RECOMMENDATION"
    CASE_CLOSURE = "CASE_CLOSURE"
    RESTRICTION_RECOMMENDATION = "RESTRICTION_RECOMMENDATION"
    ENTITY_MERGE = "ENTITY_MERGE"
    MODEL_ACTIVATION = "MODEL_ACTIVATION"
    FEEDBACK_TO_TRAINING = "FEEDBACK_TO_TRAINING"


LEGAL_TRANSITIONS: frozenset[tuple[CaseStatus, CaseStatus]] = frozenset(
    {
        (CaseStatus.OPEN, CaseStatus.INVESTIGATING),
        (CaseStatus.INVESTIGATING, CaseStatus.PENDING_REVIEW),
        (CaseStatus.PENDING_REVIEW, CaseStatus.INVESTIGATING),
        (CaseStatus.PENDING_REVIEW, CaseStatus.CLOSED),
    }
)

# A new alert for an account joins that account's newest non-closed case if it arrives within
# this many seconds of the case's last alert; otherwise it opens a new case (FR-CASE-01).
GROUPING_WINDOW_SECONDS = 24 * 3600
