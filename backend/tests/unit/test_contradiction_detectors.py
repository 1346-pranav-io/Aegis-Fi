"""Deterministic contradiction detectors: fixture contradictions found, decoys not (FR-CON-01)."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from aegis.analytics.contradictions import (
    DetectionInput,
    DetectorConfig,
    StoredClaim,
    StoredEvidence,
    detect_all,
    detect_amount_mismatches,
    detect_attribute_conflicts,
    detect_er_conflicts,
    detect_timeline_impossible,
    evidence_ids_for,
    fingerprint,
    pairs,
)
from aegis.domain.entities import ERLinkView, LinkMethod, LinkStatus
from aegis.domain.evidence import (
    ContradictionDraft,
    ContradictionType,
    EvidenceDraft,
    EvidenceInput,
    EvidenceType,
    InputRefType,
    MemberType,
    Producer,
    ProducerKind,
    Severity,
)
from aegis.eval.perturbation import generate_contradiction_corpus, to_detection_input

AS_OF = datetime(2022, 9, 10, tzinfo=UTC)
Detector = Callable[..., list[ContradictionDraft]]


def ev(
    eid: str, content: dict[str, Any], etype: EvidenceType = EvidenceType.TXN_AGGREGATE
) -> StoredEvidence:
    return StoredEvidence(
        eid,
        EvidenceDraft(
            case_id="c1",
            evidence_type=etype,
            content=content,
            summary_text="s",
            producer=Producer(kind=ProducerKind.TOOL, name="t", version="1"),
            as_of=AS_OF,
            inputs=(EvidenceInput(ref_type=InputRefType.SOURCE, ref_id="x", ref_hash="h"),),
        ),
    )


def fact(
    subject: str, pred: str, value: object, unit: str | None = None, ts: str | None = None
) -> dict[str, object]:
    return {
        "subject_ref": subject,
        "predicate": pred,
        "value": value,
        "unit": unit,
        "time_scope": ts,
    }


def run(det: Detector, *items: StoredEvidence, **kw: Any) -> list[ContradictionDraft]:
    return det(DetectionInput(evidence=tuple(items), **kw), "c1")


def test_attribute_conflict_found_and_normalisation_respected() -> None:
    a = ev("e1", {"facts": [fact("acct", "entity_type", "Corporation")]})
    b = ev("e2", {"facts": [fact("acct", "entity_type", "Partnership")]})
    out = run(detect_attribute_conflicts, a, b)
    assert len(out) == 1 and out[0].type is ContradictionType.ATTRIBUTE_CONFLICT
    assert out[0].severity is Severity.HIGH  # identity predicate
    assert {m.member_id for m in out[0].members} == {"e1", "e2"}
    same = ev("e3", {"facts": [fact("acct", "entity_type", "  CORPORATION ")]})
    assert run(detect_attribute_conflicts, a, same) == []
    s1 = ev("e4", {"facts": [fact("acct", "account_status", "ACTIVE")]})
    s2 = ev("e5", {"facts": [fact("acct", "account_status", "CLOSED")]})
    assert run(detect_attribute_conflicts, s1, s2)[0].severity is Severity.MEDIUM


def test_attribute_conflict_ignores_multivalued_other_subjects_and_other_periods() -> None:
    mv = [ev("e1", {"facts": [fact("a", "counterparty", "x")]}),
          ev("e2", {"facts": [fact("a", "counterparty", "y")]})]  # fmt: skip
    other = [ev("e3", {"facts": [fact("a", "entity_type", "X")]}),
             ev("e4", {"facts": [fact("b", "entity_type", "Y")]})]  # fmt: skip
    per = [ev("e5", {"facts": [fact("a", "account_status", "ACTIVE", ts="2022-08")]}),
           ev("e6", {"facts": [fact("a", "account_status", "CLOSED", ts="2022-09")]})]  # fmt: skip
    for items in (mv, other, per):
        assert run(detect_attribute_conflicts, *items) == []
    one = ev("e7", {"facts": [fact("a", "entity_type", "X"), fact("a", "entity_type", "Y")]})
    assert run(detect_attribute_conflicts, one) == []  # one item cannot contradict itself


def test_amount_mismatch_tolerance_units_and_severity() -> None:
    def pair(v1: str, v2: str, u2: str = "USD") -> list[ContradictionDraft]:
        return run(
            detect_amount_mismatches,
            ev("e1", {"facts": [fact("a", "total", v1, "USD", "2022-09")]}),
            ev("e2", {"facts": [fact("a", "total", v2, u2, "2022-09")]}),
        )

    assert pair("1000.00", "1000") == []  # equal under formatting
    assert pair("1000", "2000", "EUR") == []  # different units are not comparable
    assert pair("1000", "oops") == []  # non-numeric is ignored, not crashed on
    sev = [pair("10000", v)[0].severity for v in ("10050", "10300", "12500")]
    assert sev == [Severity.LOW, Severity.MEDIUM, Severity.HIGH]
    quiet = detect_amount_mismatches(
        DetectionInput(
            evidence=(
                ev("e1", {"facts": [fact("a", "t", "1000", "USD")]}),
                ev("e2", {"facts": [fact("a", "t", "1050", "USD")]}),
            )
        ),
        "c1",
        DetectorConfig(amount_tolerance=Decimal("100")),
    )
    assert quiet == []


def timeline(kind: str, at: str, subject: str = "a") -> dict[str, Any]:
    return {"events": [{"subject_ref": subject, "kind": kind, "at": at}]}


def test_timeline_impossible_needs_two_items_and_same_subject() -> None:
    opened = timeline("ACCOUNT_OPENED", "2022-01-10T00:00:00+00:00")
    early = timeline("TRANSACTION", "2022-01-05T00:00:00+00:00")
    late = timeline("TRANSACTION", "2022-01-15T00:00:00+00:00")
    naive = timeline("TRANSACTION", "2022-01-05T00:00:00")
    other = timeline("TRANSACTION", "2022-01-05T00:00:00+00:00", "z")
    out = run(detect_timeline_impossible, ev("o", opened), ev("e", early))
    assert len(out) == 1 and out[0].severity is Severity.HIGH
    assert run(detect_timeline_impossible, ev("o", opened), ev("l", late)) == []
    assert run(detect_timeline_impossible, ev("o", opened), ev("n", naive)) == []
    assert run(detect_timeline_impossible, ev("o", opened), ev("z", other)) == []
    both = ev("b", {"events": opened["events"] + early["events"]})
    assert run(detect_timeline_impossible, both) == []  # within one item: no pair to link


def link(i: str, mention: str, entity: str, status: LinkStatus) -> ERLinkView:
    return ERLinkView(
        link_id=i,
        mention_id=mention,
        entity_id=entity,
        method=LinkMethod.FUZZY_NAME,
        match_score=0.9,
        status=status,
        scorer_version="v",
    )


def er_ev(eid: str, link_id: str) -> StoredEvidence:
    return ev(eid, {"link_id": link_id}, EvidenceType.ER_LINK_EVIDENCE)


def test_er_conflict_severity_rejected_links_and_merged_entities() -> None:
    items = (er_ev("e1", "l1"), er_ev("e2", "l2"))
    conf, prop, rej = LinkStatus.CONFIRMED, LinkStatus.PROPOSED, LinkStatus.REJECTED
    both = (link("l1", "m", "A", conf), link("l2", "m", "B", conf))
    out = run(detect_er_conflicts, *items, links=both)
    assert len(out) == 1 and out[0].severity is Severity.HIGH
    mixed = (link("l1", "m", "A", conf), link("l2", "m", "B", prop))
    assert run(detect_er_conflicts, *items, links=mixed)[0].severity is Severity.MEDIUM
    rejected = (link("l1", "m", "A", conf), link("l2", "m", "B", rej))
    assert run(detect_er_conflicts, *items, links=rejected) == []
    assert run(detect_er_conflicts, *items, links=both, entity_roots={"B": "A"}) == []  # merged
    assert run(detect_er_conflicts, *items, links=()) == []  # unknown links are not judged


def test_all_detectors_find_every_seeded_contradiction_and_no_decoy() -> None:
    corpus = generate_contradiction_corpus(seed=11, n_per_type=15, n_decoys_per_type=15)
    drafts = detect_all(to_detection_input(corpus))
    found = {(d.type, frozenset(m.member_id for m in d.members)) for d in drafts}
    assert [s for s in corpus.seeded if (s.type, s.members) not in found] == []
    flagged_pairs = pairs(drafts)
    assert not [p for p in corpus.decoy_pairs if p in flagged_pairs]
    assert len(drafts) == len(corpus.seeded)  # nothing extra either
    assert all(len(d.members) >= 2 for d in drafts)


def test_detection_is_deterministic_and_order_independent() -> None:
    corpus = generate_contradiction_corpus(seed=3, n_per_type=5, n_decoys_per_type=5)
    inp = to_detection_input(corpus)
    a = detect_all(inp)
    rev = DetectionInput(
        evidence=inp.evidence[::-1], claims=inp.claims[::-1], links=inp.links[::-1]
    )
    b = detect_all(rev)
    assert [fingerprint(d) for d in a] == [fingerprint(d) for d in b]
    assert len({fingerprint(d) for d in a}) == len(a)  # no duplicates


def test_claim_vs_evidence_uses_the_verifier_and_cited_role() -> None:
    corpus = generate_contradiction_corpus(seed=2, n_per_type=3, n_decoys_per_type=3)
    inp = to_detection_input(corpus)
    cve = [d for d in detect_all(inp) if d.type is ContradictionType.CLAIM_VS_EVIDENCE]
    assert len(cve) == 3 and all(d.severity is Severity.HIGH for d in cve)
    kinds = {m.member_type for d in cve for m in d.members}
    assert kinds == {MemberType.CLAIM, MemberType.EVIDENCE}
    d = cve[0]
    ev_members = {m.member_id for m in d.members if m.member_type is MemberType.EVIDENCE}
    assert ev_members <= evidence_ids_for(d, inp)


def test_claim_cited_as_contradicted_without_verifier_is_medium() -> None:
    corpus = generate_contradiction_corpus(seed=2, n_per_type=2, n_decoys_per_type=0)
    inp = to_detection_input(corpus)
    no_verifs = DetectionInput(
        evidence=inp.evidence, claims=tuple(StoredClaim(c.claim_id, c.draft) for c in inp.claims)
    )
    cve = [d for d in detect_all(no_verifs) if d.type is ContradictionType.CLAIM_VS_EVIDENCE]
    assert cve and all(d.severity is Severity.MEDIUM for d in cve)


def test_mixed_cases_are_refused() -> None:
    a = ev("e1", {})
    b = StoredEvidence("e2", a.draft.model_copy(update={"case_id": "c2"}))
    with pytest.raises(ValueError, match="several cases"):
        detect_all(DetectionInput(evidence=(a, b)))
