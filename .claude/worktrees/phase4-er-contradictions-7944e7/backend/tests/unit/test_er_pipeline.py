"""Entity resolution: normaliser, scorer, blocking and the pure pipeline."""

import pytest

from aegis.domain.entities import LinkMethod, LinkStatus
from aegis.entities.blocking import BlockIndex, blocking_keys
from aegis.entities.pipeline import EntityIndex, EntityRecord, MentionRecord, propose_links
from aegis.entities.similarity import (
    SCORER_VERSION,
    bigram_dice,
    jaro_winkler,
    name_similarity,
    normalise_name,
)


def n(s: str) -> str:
    return normalise_name(s)


def test_scorer_is_versioned() -> None:
    assert SCORER_VERSION.startswith("name-sim-")


def test_normaliser_handles_case_punctuation_suffixes_and_no() -> None:
    assert n("Corporation #33520") == "corporation 33520"
    assert n("CORP. No. 33520") == "corporation 33520"
    assert n("Sole Prop, #5") == "sole proprietorship 5"
    assert n("Acme Ltd.") == "acme limited"


def test_string_primitives() -> None:
    assert jaro_winkler("abc", "abc") == 1.0
    assert jaro_winkler("martha", "marhta") == pytest.approx(0.9611, abs=1e-3)
    assert jaro_winkler("", "x") == 0.0
    assert bigram_dice("night", "nacht") == pytest.approx(0.25)
    assert bigram_dice("", "") == 1.0


def test_similarity_properties() -> None:
    a, b = n("Corporation #33520"), n("Corporaton #33520")
    assert name_similarity(a, a) == 1.0
    assert name_similarity(a, b) == name_similarity(b, a)  # symmetric
    assert 0.0 <= name_similarity(a, n("Zebra Holdings")) <= 1.0
    assert name_similarity(a, n("33520 corporation")) == 1.0  # token order is ignored


def test_typo_scores_above_a_different_entity_with_one_digit_changed() -> None:
    same = name_similarity(n("Corporation #33520"), n("Corporaton #33520"))
    other = name_similarity(n("Corporation #33520"), n("Corporation #33521"))
    assert same > 0.9
    assert other < same  # the digit penalty separates near-collisions from typos
    assert other < 0.9


def test_blocking_puts_one_digit_neighbours_together_and_skips_huge_blocks() -> None:
    assert blocking_keys(n("Corporation #33520")) & blocking_keys(n("Corporation #33521"))
    far = blocking_keys(n("Corporation #33520")) & blocking_keys(n("Corporation #99999"))
    assert not {k for k in far if k[0] in "nd"}  # no digit key shared
    idx = BlockIndex(max_block=2)
    for i in range(5):  # 'corp' key block becomes too large and is ignored
        idx.add(f"e{i}", f"corporation {i}{i}{i}{i}{i}")
    assert list(idx.candidates("corporation 11111")) == ["e1"]


def ent(i: str, name: str, **ids: str) -> EntityRecord:
    return EntityRecord(i, n(name), dict(ids))


def test_fuzzy_links_are_always_proposed_never_confirmed() -> None:
    index = EntityIndex([ent("e1", "Corporation #33520"), ent("e2", "Corporation #33521")])
    out = propose_links(MentionRecord("m1", n("Corporaton #33520")), index)
    assert out and out[0].entity_id == "e1"
    assert all(p.status is LinkStatus.PROPOSED for p in out)
    assert all(p.method is LinkMethod.FUZZY_NAME for p in out)
    assert out[0].match_score >= out[-1].match_score
    # even an identical name is only a proposal: a name is not a verified identifier
    exact_name = propose_links(MentionRecord("m2", n("Corporation #33520")), index)
    assert exact_name[0].match_score == 1.0 and exact_name[0].status is LinkStatus.PROPOSED


def test_near_duplicate_names_below_threshold_get_no_link() -> None:
    index = EntityIndex([ent("e1", "Corporation #33520")])
    assert propose_links(MentionRecord("m", n("Zebra Holdings 33520")), index) == []


def test_exact_identifier_confirms_only_when_it_identifies_one_entity() -> None:
    index = EntityIndex([ent("e1", "A", account_number="X1"), ent("e2", "B", account_number="X2")])
    out = propose_links(MentionRecord("m", n("whatever"), {"account_number": "X1"}), index)
    assert [(p.entity_id, p.status, p.method) for p in out] == [
        ("e1", LinkStatus.CONFIRMED, LinkMethod.EXACT_ID)
    ]
    # an unlisted identifier type never confirms
    out2 = propose_links(MentionRecord("m", n("A"), {"favourite_colour": "X1"}), index)
    assert all(p.status is LinkStatus.PROPOSED for p in out2)


def test_shared_identifier_is_a_conflict_not_a_confirmation() -> None:
    index = EntityIndex([ent("e1", "A", account_number="X"), ent("e2", "B", account_number="X")])
    out = propose_links(MentionRecord("m", n("A"), {"account_number": "X"}), index)
    assert {p.entity_id for p in out} == {"e1", "e2"}
    assert all(p.status is LinkStatus.PROPOSED for p in out)
    assert all(p.features.get("ambiguous_identifier") for p in out)


def test_two_identifiers_pointing_at_different_entities_are_proposals() -> None:
    index = EntityIndex([ent("e1", "A", account_number="X1"), ent("e2", "B", entity_id="Y1")])
    m = MentionRecord("m", n("A"), {"account_number": "X1", "entity_id": "Y1"})
    out = propose_links(m, index)
    assert {p.entity_id for p in out} == {"e1", "e2"}
    assert all(p.status is LinkStatus.PROPOSED for p in out)


def test_pipeline_is_deterministic() -> None:
    index = EntityIndex([ent(f"e{i}", f"Corporation #{33500 + i}") for i in range(40)])
    m = MentionRecord("m", n("Corporaton #33510"))
    assert propose_links(m, index) == propose_links(m, index)
