"""Provenance walk (T-INV-04): resolves to leaves with hashes intact; every kind of tampering is
detected at the node where it happens."""

import pytest
from p3_builders import HUMAN, World, claim_draft, ev_draft

from aegis.common.errors import IntegrityError
from aegis.domain.evidence import EvidenceInput, EvidenceType, InputRefType, SupportRole
from aegis.evidence.provenance import Problem, walk, walk_case
from aegis.evidence.store import InMemoryEvidenceStore

CASE = "case_1"


def build() -> tuple[World, InMemoryEvidenceStore, str, str, str]:
    w = World()
    s = InMemoryEvidenceStore(w.leaves)
    base, _ = s.create_evidence(ev_draft(CASE, (w.src("t1"), w.src("t2"))))
    link = EvidenceInput(
        ref_type=InputRefType.EVIDENCE, ref_id=base.evidence_id, ref_hash=base.content_hash
    )
    derived, _ = s.create_evidence(
        ev_draft(CASE, (link, w.src("t3")), content={"derived": 1}, etype=EvidenceType.GRAPH_CYCLES)
    )
    claim = s.create_claim(claim_draft(CASE, [derived.evidence_id]))
    return w, s, base.evidence_id, derived.evidence_id, claim.claim_id


def test_walk_reaches_source_leaves_with_hashes_intact() -> None:
    w, s, base, derived, claim = build()
    rep = walk(s, w.leaves, claim)
    assert rep.ok
    assert {n.node_id for n in rep.leaves} == {"t1", "t2", "t3"}
    assert all(n.kind == "SOURCE" for n in rep.leaves)
    assert (claim, derived) in rep.edges and (derived, base) in rep.edges
    rep.assert_intact()


def test_human_evidence_is_a_leaf() -> None:
    w = World()
    s = InMemoryEvidenceStore(w.leaves)
    note, _ = s.create_evidence(
        ev_draft(CASE, (), producer=HUMAN, etype=EvidenceType.HUMAN_NOTE, content={"t": "x"})
    )
    c = s.create_claim(claim_draft(CASE, [note.evidence_id], role=SupportRole.CONTEXT))
    rep = walk(s, w.leaves, c.claim_id)
    assert rep.ok and [n.kind for n in rep.leaves] == ["HUMAN"]


def test_tampered_evidence_content_is_detected() -> None:
    w, s, base, _, claim = build()
    ev = s.get_evidence(base)
    forged_draft = ev.draft.model_copy(update={"content": {"n": 999}})
    s._evidence[base] = ev.model_copy(update={"draft": forged_draft})
    rep = walk(s, w.leaves, claim)
    assert (Problem.EVIDENCE_HASH_MISMATCH, base) in rep.problems
    with pytest.raises(IntegrityError):
        rep.assert_intact()


def test_changed_source_record_is_detected() -> None:
    w, s, _, _, claim = build()
    w.leaves.add(InputRefType.SOURCE, "t2", "changed-after-the-fact")
    rep = walk(s, w.leaves, claim)
    assert [p for p, _ in rep.problems] == [Problem.LEAF_HASH_MISMATCH]


def test_missing_source_and_dangling_evidence_are_detected() -> None:
    w, s, base, _, claim = build()
    del w.leaves.entries[(InputRefType.SOURCE, "t3")]
    del s._evidence[base]
    rep = walk(s, w.leaves, claim)
    kinds = {p for p, _ in rep.problems}
    assert kinds == {Problem.LEAF_UNKNOWN, Problem.DANGLING_REF}


def test_input_hash_recorded_wrongly_is_detected() -> None:
    w, s, base, derived, claim = build()
    ev = s.get_evidence(derived)
    wrong = tuple(
        i.model_copy(update={"ref_hash": "0" * 64}) if i.ref_type is InputRefType.EVIDENCE else i
        for i in ev.draft.inputs
    )
    s._evidence[derived] = ev.model_copy(
        update={"draft": ev.draft.model_copy(update={"inputs": wrong})}
    )
    problems = {p for p, _ in walk(s, w.leaves, claim).problems}
    assert Problem.INPUT_HASH_MISMATCH in problems


def test_walk_case_covers_every_claim() -> None:
    w, s, base, derived, claim = build()
    s.create_claim(claim_draft(CASE, [base], text="second claim", value=2))
    reports = walk_case(s, w.leaves, CASE)
    assert len(reports) == 2 and all(r.ok for r in reports)
