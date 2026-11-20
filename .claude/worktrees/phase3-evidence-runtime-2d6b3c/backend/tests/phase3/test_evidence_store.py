"""Evidence store contract (memory implementation; Postgres is covered by the integration test).
T-INV-05 (claim without valid evidence rejected), idempotent creation, HUMAN_NOTE context-only."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from p3_builders import HUMAN, World, claim_draft, ev_draft

from aegis.common.errors import AegisValidationError, NotFound
from aegis.domain.evidence import (
    CheckKind,
    CheckOutcome,
    ContradictionDraft,
    ContradictionMember,
    ContradictionType,
    EvidenceInput,
    EvidenceType,
    InputRefType,
    MemberType,
    Severity,
    SupportRole,
    VerificationCheck,
    VerificationDraft,
    VerificationStatus,
)
from aegis.evidence.canon import hashable
from aegis.evidence.store import InMemoryEvidenceStore

CASE = "case_1"


def kit() -> tuple[World, InMemoryEvidenceStore]:
    w = World()
    return w, InMemoryEvidenceStore(w.leaves)


def test_evidence_creation_is_idempotent_and_input_order_does_not_matter() -> None:
    w, s = kit()
    a, b = w.src("t1"), w.src("t2")
    e1, new1 = s.create_evidence(ev_draft(CASE, (a, b)))
    e2, new2 = s.create_evidence(ev_draft(CASE, (b, a)))
    assert new1 and not new2 and e1.evidence_id == e2.evidence_id
    assert len(s.list_evidence(CASE)) == 1
    other, new3 = s.create_evidence(ev_draft("case_2", (a, b)))  # same content, other case
    assert new3 and other.evidence_id != e1.evidence_id


def test_leaf_hash_must_match_the_stored_record() -> None:
    w, s = kit()
    w.src("t1", "real")
    forged = EvidenceInput(ref_type=InputRefType.SOURCE, ref_id="t1", ref_hash="forged")
    with pytest.raises(AegisValidationError, match="differs"):
        s.create_evidence(ev_draft(CASE, (forged,)))
    ghost = EvidenceInput(ref_type=InputRefType.SOURCE, ref_id="nope", ref_hash="x")
    with pytest.raises(AegisValidationError, match="unknown"):
        s.create_evidence(ev_draft(CASE, (ghost,)))


def test_evidence_inputs_must_exist_in_same_case_with_stored_hash() -> None:
    w, s = kit()
    base, _ = s.create_evidence(ev_draft(CASE, (w.src("t1"),)))
    good = EvidenceInput(
        ref_type=InputRefType.EVIDENCE, ref_id=base.evidence_id, ref_hash=base.content_hash
    )
    s.create_evidence(ev_draft(CASE, (good,), content={"derived": True}))
    bad = good.model_copy(update={"ref_hash": "0" * 64})
    with pytest.raises(AegisValidationError, match="hash mismatch"):
        s.create_evidence(ev_draft(CASE, (bad,), content={"x": 1}))
    with pytest.raises(AegisValidationError, match="another case"):
        s.create_evidence(ev_draft("case_2", (good,), content={"x": 2}))
    missing = good.model_copy(update={"ref_id": "ev_missing"})
    with pytest.raises(AegisValidationError, match="does not exist"):
        s.create_evidence(ev_draft(CASE, (missing,), content={"x": 3}))


def test_claim_must_cite_existing_evidence_of_its_case() -> None:
    w, s = kit()
    e, _ = s.create_evidence(ev_draft(CASE, (w.src("t1"),)))
    c = s.create_claim(claim_draft(CASE, [e.evidence_id]))
    assert s.get_claim(c.claim_id).status is None  # DRAFT until verified
    with pytest.raises(AegisValidationError, match="unknown evidence"):
        s.create_claim(claim_draft(CASE, ["ev_nope"]))
    with pytest.raises(AegisValidationError, match="another case"):
        s.create_claim(claim_draft("case_2", [e.evidence_id]))
    with pytest.raises(ValueError, match="at least one"):  # schema level (domain contract)
        claim_draft(CASE, [])


@given(st.lists(st.text(min_size=1, max_size=12), min_size=1, max_size=5, unique=True))
def test_property_claims_citing_unknown_ids_never_stored(ids: list[str]) -> None:
    """T-INV-05: whatever ids a producer invents, nothing is stored unless the evidence exists."""
    w, s = kit()
    real, _ = s.create_evidence(ev_draft(CASE, (w.src("t1"),)))
    fake = [i for i in ids if i != real.evidence_id]
    with pytest.raises((AegisValidationError, ValueError)):
        s.create_claim(claim_draft(CASE, [real.evidence_id, *fake, "ev_never_existed"]))
    assert s.list_claims(CASE) == []


def test_human_note_is_context_only() -> None:
    """Q-10: HUMAN_NOTE cannot support a claim, only give context."""
    _, s = kit()
    note, _ = s.create_evidence(
        ev_draft(CASE, (), etype=EvidenceType.HUMAN_NOTE, producer=HUMAN, content={"t": "hi"})
    )
    with pytest.raises(AegisValidationError, match="context only"):
        s.create_claim(claim_draft(CASE, [note.evidence_id]))
    s.create_claim(claim_draft(CASE, [note.evidence_id], role=SupportRole.CONTEXT))


def test_verification_status_follows_latest_result() -> None:
    w, s = kit()
    e, _ = s.create_evidence(ev_draft(CASE, (w.src("t1"),)))
    c = s.create_claim(claim_draft(CASE, [e.evidence_id]))

    def ver(status: VerificationStatus) -> VerificationDraft:
        return VerificationDraft(
            claim_id=c.claim_id,
            status=status,
            checks=(
                VerificationCheck(
                    check_id="X", kind=CheckKind.DETERMINISTIC, outcome=CheckOutcome.PASS
                ),
            ),
            rationale="r",
            verifier_name="v",
            verifier_version="1",
            inputs_seen_hash="h",
        )

    s.record_verification(ver(VerificationStatus.UNVERIFIABLE))
    s.record_verification(ver(VerificationStatus.VERIFIED))
    assert s.get_claim(c.claim_id).status is VerificationStatus.VERIFIED
    assert len(s.verifications(c.claim_id)) == 2
    with pytest.raises(NotFound):
        s.record_verification(ver(VerificationStatus.VERIFIED).model_copy(update={"claim_id": "x"}))


def test_contradictions_listed_until_a_human_resolves() -> None:
    w, s = kit()
    e1, _ = s.create_evidence(ev_draft(CASE, (w.src("t1"),), content={"a": 1}))
    e2, _ = s.create_evidence(ev_draft(CASE, (w.src("t2"),), content={"a": 2}))
    d = ContradictionDraft(
        case_id=CASE,
        type=ContradictionType.AMOUNT_MISMATCH,
        severity=Severity.HIGH,
        detector_name="d",
        detector_version="1",
        members=(
            ContradictionMember(member_type=MemberType.EVIDENCE, member_id=e1.evidence_id),
            ContradictionMember(member_type=MemberType.EVIDENCE, member_id=e2.evidence_id),
        ),
    )
    v = s.create_contradiction(d)
    assert [c.contradiction_id for c in s.unresolved_contradictions(CASE)] == [v.contradiction_id]
    s.resolve_contradiction_for_test(v.contradiction_id)
    assert s.unresolved_contradictions(CASE) == []
    with pytest.raises(AegisValidationError):
        s.create_contradiction(d.model_copy(update={"case_id": "case_9"}))


def test_transaction_rolls_back_all_writes() -> None:
    w, s = kit()
    with pytest.raises(RuntimeError), s.transaction():
        s.create_evidence(ev_draft(CASE, (w.src("t1"),)))
        raise RuntimeError("boom")
    assert s.list_evidence(CASE) == []


def test_hashable_makes_analytics_output_deterministic() -> None:
    out = hashable({"z": np.float64(1.23456789012), "n": np.int64(3), "nan": float("nan")})
    assert out["nan"] is None and out["n"] == 3
    d1 = ev_draft(CASE, (), content=out, producer=HUMAN, etype=EvidenceType.HUMAN_NOTE)
    d2 = ev_draft(CASE, (), content=hashable(dict(out)), producer=HUMAN,
                  etype=EvidenceType.HUMAN_NOTE)  # fmt: skip
    assert d1.content_hash() == d2.content_hash()
