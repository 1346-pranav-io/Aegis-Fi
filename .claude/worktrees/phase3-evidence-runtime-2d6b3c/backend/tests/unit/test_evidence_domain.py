from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aegis.domain.agents import AgentError, AgentResult, Permission, ResultStatus
from aegis.domain.evidence import (
    CheckKind,
    CheckOutcome,
    Citation,
    ClaimDraft,
    ClaimKind,
    ClaimStructure,
    ContradictionDraft,
    ContradictionMember,
    ContradictionType,
    EvidenceDraft,
    EvidenceInput,
    EvidenceType,
    InputRefType,
    MemberType,
    Producer,
    ProducerKind,
    Severity,
    SupportRole,
    VerificationCheck,
    VerificationDraft,
    VerificationStatus,
)

T = datetime(2022, 9, 10, tzinfo=UTC)
TOOL = Producer(kind=ProducerKind.TOOL, name="txn", version="1")
INP = EvidenceInput(ref_type=InputRefType.SOURCE, ref_id="txn_1", ref_hash="h")


def evidence(**kw: object) -> EvidenceDraft:
    base: dict[str, object] = dict(
        case_id="case_1",
        evidence_type=EvidenceType.TXN_AGGREGATE,
        content={"n": 3},
        summary_text="3 transactions",
        producer=TOOL,
        as_of=T,
        inputs=(INP,),
    )
    base.update(kw)
    return EvidenceDraft(**base)  # type: ignore[arg-type]


def test_evidence_needs_inputs_unless_human() -> None:
    with pytest.raises(ValidationError):
        evidence(inputs=())
    human = Producer(kind=ProducerKind.HUMAN, name="analyst", version="1")
    assert evidence(inputs=(), producer=human).inputs == ()


def test_probability_needs_calibration_artefact() -> None:
    with pytest.raises(ValidationError):
        evidence(scores={"score": 0.9, "calibrated_probability": 0.8})
    ok = evidence(
        scores={"score": 0.9, "calibrated_probability": 0.8, "calibration_artefact_id": "c1"}
    )
    assert ok.scores is not None
    assert evidence(scores={"score": 0.9}).scores == {"score": 0.9}


def test_content_hash_is_stable_and_sensitive() -> None:
    a, b = evidence(), evidence()
    assert a.content_hash() == b.content_hash()
    assert evidence(content={"n": 4}).content_hash() != a.content_hash()
    assert (
        evidence(inputs=(INP, INP.model_copy(update={"ref_hash": "h2"}))).content_hash()
        != a.content_hash()
    )


def test_naive_times_rejected() -> None:
    with pytest.raises(ValidationError):
        evidence(as_of=datetime(2022, 9, 10))


def claim(**kw: object) -> ClaimDraft:
    base: dict[str, object] = dict(
        case_id="case_1",
        author_kind="AGENT",
        author_name="investigation",
        author_version="1",
        structure=ClaimStructure(subject_ref="acct_1", predicate="sent_total", object_value=5),
        text="Account sent 5 in total",
        claim_kind=ClaimKind.AGGREGATE,
        citations=(Citation(evidence_id="ev_1", support_role=SupportRole.SUPPORTS),),
    )
    base.update(kw)
    return ClaimDraft(**base)  # type: ignore[arg-type]


def test_claim_must_cite_distinct_evidence() -> None:
    assert claim().citations
    with pytest.raises(ValidationError):
        claim(citations=())
    c = Citation(evidence_id="ev_1", support_role=SupportRole.CONTEXT)
    with pytest.raises(ValidationError):
        claim(citations=(c, c))


def test_verified_is_impossible_with_a_failed_check() -> None:
    fail = VerificationCheck(
        check_id="AMOUNT_MATCHES", kind=CheckKind.DETERMINISTIC, outcome=CheckOutcome.FAIL
    )
    common = dict(
        claim_id="clm_1",
        rationale="r",
        verifier_name="v",
        verifier_version="1",
        inputs_seen_hash="h",
    )
    with pytest.raises(ValidationError):
        VerificationDraft(status=VerificationStatus.VERIFIED, checks=(fail,), **common)  # type: ignore[arg-type]
    assert VerificationDraft(
        status=VerificationStatus.UNSUPPORTED,
        checks=(fail,),
        **common,  # type: ignore[arg-type]
    )


def test_contradiction_needs_two_members() -> None:
    m1 = ContradictionMember(member_type=MemberType.EVIDENCE, member_id="ev_1")
    m2 = ContradictionMember(member_type=MemberType.CLAIM, member_id="clm_1")
    common = dict(
        case_id="c",
        type=ContradictionType.AMOUNT_MISMATCH,
        severity=Severity.HIGH,
        detector_name="d",
        detector_version="1",
    )
    with pytest.raises(ValidationError):
        ContradictionDraft(members=(m1,), **common)  # type: ignore[arg-type]
    assert ContradictionDraft(members=(m1, m2), **common)  # type: ignore[arg-type]


def test_agent_result_failed_needs_error_and_no_write_source_permission_exists() -> None:
    with pytest.raises(ValueError):
        AgentResult(task_id="t", status=ResultStatus.FAILED, version="1")
    assert AgentResult(
        task_id="t",
        status=ResultStatus.FAILED,
        version="1",
        error=AgentError(code="X", message="m"),
    )
    assert "WRITE_SOURCE" not in {p.value for p in Permission}
