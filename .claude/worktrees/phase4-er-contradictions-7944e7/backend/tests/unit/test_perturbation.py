# ruff: noqa: S311  # seeded RNG for reproducible test inputs
"""Seeded perturbation generator: determinism, catalogue behaviour, ground truth."""

import random

import pytest

from aegis.domain.evidence import ContradictionType
from aegis.eval.perturbation import (
    IDENTITY_PRESERVING,
    PERTURBATION_VERSION,
    NameKind,
    generate_contradiction_corpus,
    generate_er_corpus,
    item_rng,
    perturb_name,
)

NAMES = [f"Corporation #{n}" for n in range(33500, 33560)] + [
    f"Sole Proprietorship #{n}" for n in range(50400, 50430)
]


def test_version_is_declared() -> None:
    assert PERTURBATION_VERSION.count(".") == 2


def test_perturb_name_is_deterministic_for_a_seed() -> None:
    for kind in NameKind:
        a = perturb_name("Sole Proprietorship #50438", kind, item_rng(7, kind.value))
        b = perturb_name("Sole Proprietorship #50438", kind, item_rng(7, kind.value))
        assert a == b


def test_each_kind_changes_a_typical_name_except_duplicate() -> None:
    name = "Sole Proprietorship #50438"
    for kind in NameKind:
        outs = {perturb_name(name, kind, random.Random(s)) for s in range(30)}
        if kind is NameKind.DUPLICATE:
            assert outs == {name}
        else:
            assert outs != {name}, kind


def test_number_change_alters_only_digits_and_is_not_identity_preserving() -> None:
    assert NameKind.NUMBER_CHANGE not in IDENTITY_PRESERVING
    out = perturb_name("Corporation #33520", NameKind.NUMBER_CHANGE, random.Random(1))
    assert out != "Corporation #33520"
    assert out.startswith("Corporation #") and out[len("Corporation #") :].isdigit()
    assert sum(a != b for a, b in zip(out, "Corporation #33520", strict=True)) == 1


def test_identity_preserving_kinds_never_touch_digits_except_whitespace_or_punct() -> None:
    name = "Corporation #33520"
    for kind in (NameKind.TYPO_SUBSTITUTE, NameKind.TYPO_DELETE, NameKind.TYPO_INSERT,
                 NameKind.TYPO_TRANSPOSE, NameKind.TOKEN_DROP, NameKind.SUFFIX_CHANGE):  # fmt: skip
        for s in range(40):
            out = perturb_name(name, kind, random.Random(s))
            assert "33520" in out, (kind, out)


def test_er_corpus_is_deterministic_and_seed_sensitive() -> None:
    a = generate_er_corpus(NAMES, seed=1, perturbation_rate=0.5)
    b = generate_er_corpus(NAMES, seed=1, perturbation_rate=0.5)
    c = generate_er_corpus(NAMES, seed=2, perturbation_rate=0.5)
    assert a == b
    assert a.mentions != c.mentions


def test_er_corpus_ground_truth_and_rates() -> None:
    zero = generate_er_corpus(NAMES, seed=3, perturbation_rate=0.0, distractor_fraction=0.0)
    assert all(m.kind is None and not m.changed for m in zero.mentions)
    assert all(zero.entities[m.true_entity_idx] == m.text for m in zero.mentions)
    full = generate_er_corpus(NAMES, seed=3, perturbation_rate=1.0, distractor_fraction=0.0,
                              mentions_per_entity=3)  # fmt: skip
    assert len(full.mentions) == 3 * len(NAMES)
    assert all(m.kind in IDENTITY_PRESERVING for m in full.mentions)
    assert sum(m.changed for m in full.mentions) > 0.7 * len(full.mentions)


def test_distractors_are_distinct_near_collisions() -> None:
    corp = generate_er_corpus(NAMES, seed=4, perturbation_rate=0.0, distractor_fraction=0.5)
    assert corp.distractor_of
    assert len(set(corp.entities)) == len(corp.entities)
    for d, orig in corp.distractor_of.items():
        a, b = corp.entities[d], corp.entities[orig]
        assert a != b and len(a) == len(b)
        assert sum(x != y for x, y in zip(a, b, strict=True)) == 1


def test_er_corpus_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        generate_er_corpus(["A", "A"], seed=1, perturbation_rate=0.1)
    with pytest.raises(ValueError):
        generate_er_corpus(NAMES, seed=1, perturbation_rate=1.5)
    with pytest.raises(ValueError):
        generate_er_corpus(NAMES, seed=1, perturbation_rate=0.1, kinds=(NameKind.NUMBER_CHANGE,))


def test_contradiction_corpus_is_deterministic_with_ground_truth() -> None:
    a = generate_contradiction_corpus(seed=5, n_per_type=6, n_decoys_per_type=6)
    b = generate_contradiction_corpus(seed=5, n_per_type=6, n_decoys_per_type=6)
    assert a.evidence.keys() == b.evidence.keys()
    assert [s.members for s in a.seeded] == [s.members for s in b.seeded]
    kinds = {s.type for s in a.seeded}
    assert kinds == {
        ContradictionType.ATTRIBUTE_CONFLICT, ContradictionType.AMOUNT_MISMATCH,
        ContradictionType.TIMELINE_IMPOSSIBLE, ContradictionType.CLAIM_VS_EVIDENCE,
        ContradictionType.ER_CONFLICT,
    }  # fmt: skip
    assert len(a.seeded) == 5 * 6 and len(a.decoy_pairs) == 5 * 6
    known = set(a.evidence) | set(a.claims)
    assert all(m in known for s in a.seeded for m in s.members)
    assert all(len(s.members) == 2 for s in a.seeded)
    assert a.version == PERTURBATION_VERSION


def test_seed_changes_the_contradiction_corpus() -> None:
    a = generate_contradiction_corpus(seed=5, n_per_type=4, n_decoys_per_type=2)
    c = generate_contradiction_corpus(seed=6, n_per_type=4, n_decoys_per_type=2)
    assert {e.content_hash() for e in a.evidence.values()} != {
        e.content_hash() for e in c.evidence.values()
    }
