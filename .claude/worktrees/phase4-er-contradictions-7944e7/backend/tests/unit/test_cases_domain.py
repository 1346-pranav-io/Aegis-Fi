from aegis.domain.cases import LEGAL_TRANSITIONS, ApprovalAction, CaseStatus


def test_closed_is_terminal_and_closing_only_from_pending_review() -> None:
    assert not any(a is CaseStatus.CLOSED for a, _ in LEGAL_TRANSITIONS)
    assert {a for a, b in LEGAL_TRANSITIONS if b is CaseStatus.CLOSED} == {
        CaseStatus.PENDING_REVIEW
    }


def test_no_self_transitions_and_rework_allowed() -> None:
    assert all(a is not b for a, b in LEGAL_TRANSITIONS)
    assert (CaseStatus.PENDING_REVIEW, CaseStatus.INVESTIGATING) in LEGAL_TRANSITIONS


def test_approval_actions_match_d09_list() -> None:
    assert {a.value for a in ApprovalAction} == {
        "REGULATORY_FILING_RECOMMENDATION",
        "CASE_CLOSURE",
        "RESTRICTION_RECOMMENDATION",
        "ENTITY_MERGE",
        "MODEL_ACTIVATION",
        "FEEDBACK_TO_TRAINING",
    }
