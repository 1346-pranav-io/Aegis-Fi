# ruff: noqa: S311, S105  # seeded simulation RNG (not cryptographic); enum labels, not secrets
"""Seeded perturbation generator with ground truth (D-23, EVALUATION_FRAMEWORK section 4/5).

IBM entity names are single synthetic strings with no natural variants, so entity-resolution
experiments (E5) and contradiction experiments (E6) use *seeded* corruptions of known-clean data.
Everything here is deterministic given a seed (each item draws from `random.Random` seeded by a
string built from the seed and the item index, so results do not depend on iteration order or on
the Python hash seed) and returns the ground truth alongside the data. The generator is versioned
(`PERTURBATION_VERSION`); a changed catalogue or changed random draw order MUST bump it.

Honest limits (state them wherever results are reported): these perturbations are my catalogue of
plausible errors, not a sample of natural errors; they may be easier or harder than real ones, and
the contradiction decoy mix is chosen by the generator, not measured from real investigations.
"""

import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from aegis.analytics.contradictions import DetectionInput, StoredClaim, StoredEvidence
from aegis.domain.entities import ERLinkView, LinkMethod, LinkStatus
from aegis.domain.evidence import (
    AuthorKind,
    CheckKind,
    CheckOutcome,
    Citation,
    ClaimDraft,
    ClaimKind,
    ClaimStructure,
    Completeness,
    ContradictionType,
    EvidenceDraft,
    EvidenceInput,
    EvidenceType,
    InputRefType,
    Producer,
    ProducerKind,
    SupportRole,
    VerificationCheck,
    VerificationDraft,
    VerificationStatus,
)

PERTURBATION_VERSION = "1.0.0"
SEED_AS_OF = datetime(2022, 9, 10, tzinfo=UTC)


# --------------------------------------------------------------------------- name perturbations
class NameKind(StrEnum):
    TYPO_SUBSTITUTE = "TYPO_SUBSTITUTE"
    TYPO_DELETE = "TYPO_DELETE"
    TYPO_INSERT = "TYPO_INSERT"
    TYPO_TRANSPOSE = "TYPO_TRANSPOSE"
    TOKEN_SWAP = "TOKEN_SWAP"
    TOKEN_DROP = "TOKEN_DROP"
    SUFFIX_CHANGE = "SUFFIX_CHANGE"
    CASE_CHANGE = "CASE_CHANGE"
    PUNCTUATION = "PUNCTUATION"
    WHITESPACE = "WHITESPACE"
    DUPLICATE = "DUPLICATE"
    NUMBER_CHANGE = (
        "NUMBER_CHANGE"  # a DIFFERENT entity that looks almost identical (hard negative)
    )


IDENTITY_PRESERVING: frozenset[NameKind] = frozenset(
    k for k in NameKind if k is not NameKind.NUMBER_CHANGE
)
DEFAULT_MENTION_KINDS: tuple[NameKind, ...] = tuple(
    sorted(IDENTITY_PRESERVING, key=lambda k: k.value)
)

_SUFFIX_SWAPS: dict[str, tuple[str, ...]] = {
    "corporation": ("Corp", "Corp.", "Co"),
    "partnership": ("Partners", "Prtnshp"),
    "sole proprietorship": ("Sole Prop", "Sole Proprietor"),
    "incorporated": ("Inc", "Inc."),
    "limited": ("Ltd", "Ltd."),
    "corp": ("Corporation",),
    "inc": ("Incorporated",),
    "ltd": ("Limited",),
}
_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _letter_positions(s: str) -> list[int]:
    return [i for i, ch in enumerate(s) if ch.isalpha()]


def _typo_substitute(name: str, rng: random.Random) -> str:
    pos = _letter_positions(name)
    if not pos:
        return name
    i = rng.choice(pos)
    repl = rng.choice([c for c in _LETTERS if c != name[i].lower()])
    return name[:i] + (repl.upper() if name[i].isupper() else repl) + name[i + 1 :]


def _typo_delete(name: str, rng: random.Random) -> str:
    pos = _letter_positions(name)
    if len(pos) < 2:
        return name
    i = rng.choice(pos)
    return name[:i] + name[i + 1 :]


def _typo_insert(name: str, rng: random.Random) -> str:
    pos = _letter_positions(name)
    if not pos:
        return name
    i = rng.choice(pos)
    return name[:i] + rng.choice(_LETTERS) + name[i:]


def _typo_transpose(name: str, rng: random.Random) -> str:
    pairs = [i for i in range(len(name) - 1) if name[i].isalpha() and name[i + 1].isalpha()
             and name[i] != name[i + 1]]  # fmt: skip
    if not pairs:
        return name
    i = rng.choice(pairs)
    return name[:i] + name[i + 1] + name[i] + name[i + 2 :]


def _token_swap(name: str, rng: random.Random) -> str:
    toks = name.split()
    if len(toks) < 2:
        return name
    i = rng.randrange(len(toks) - 1)
    toks[i], toks[i + 1] = toks[i + 1], toks[i]
    return " ".join(toks)


def _token_drop(name: str, rng: random.Random) -> str:
    toks = name.split()
    droppable = [i for i, t in enumerate(toks) if not any(ch.isdigit() for ch in t)]
    if len(toks) < 2 or not droppable:
        return name
    del toks[rng.choice(droppable)]
    return " ".join(toks)


def _suffix_change(name: str, rng: random.Random) -> str:
    low = name.lower()
    # longest keys first so "sole proprietorship" is tried before shorter ones
    for key in sorted(_SUFFIX_SWAPS, key=len, reverse=True):
        m = re.search(rf"\b{re.escape(key)}\b", low)
        if m:
            return name[: m.start()] + rng.choice(_SUFFIX_SWAPS[key]) + name[m.end() :]
    return name


def _case_change(name: str, rng: random.Random) -> str:
    return rng.choice([name.upper(), name.lower(), name.title()])


def _punctuation(name: str, rng: random.Random) -> str:
    i = rng.randrange(5)
    if i == 0:
        return name.replace("#", "")
    if i == 1:
        return name.replace("#", "No. ")
    if i == 2:
        return name + "."
    if i == 3:
        return name.replace(" ", "-", 1)
    return name.replace(" ", ", ", 1)


def _whitespace(name: str, rng: random.Random) -> str:
    i = rng.randrange(3)
    if i == 0:
        return "  ".join(name.split(" "))
    if i == 1:
        return f" {name} "
    return name.replace(" ", "	", 1)


def _number_change(name: str, rng: random.Random) -> str:
    pos = [i for i, ch in enumerate(name) if ch.isdigit()]
    if not pos:
        return name
    i = rng.choice(pos)
    repl = rng.choice([d for d in "0123456789" if d != name[i]])
    return name[:i] + repl + name[i + 1 :]


def _duplicate(name: str, rng: random.Random) -> str:
    return name


_OPS: dict[NameKind, Callable[[str, random.Random], str]] = {
    NameKind.TYPO_SUBSTITUTE: _typo_substitute,
    NameKind.TYPO_DELETE: _typo_delete,
    NameKind.TYPO_INSERT: _typo_insert,
    NameKind.TYPO_TRANSPOSE: _typo_transpose,
    NameKind.TOKEN_SWAP: _token_swap,
    NameKind.TOKEN_DROP: _token_drop,
    NameKind.SUFFIX_CHANGE: _suffix_change,
    NameKind.CASE_CHANGE: _case_change,
    NameKind.PUNCTUATION: _punctuation,
    NameKind.WHITESPACE: _whitespace,
    NameKind.DUPLICATE: _duplicate,
    NameKind.NUMBER_CHANGE: _number_change,
}


def perturb_name(name: str, kind: NameKind, rng: random.Random) -> str:
    """Apply one perturbation. May return the input unchanged when the kind does not apply to
    this name (e.g. TOKEN_SWAP on a one-token name); callers read `changed` from the corpus."""
    return _OPS[kind](name, rng)


def item_rng(seed: int, *parts: object) -> random.Random:
    """An independent, order-insensitive random stream for one item."""
    return random.Random(":".join([str(seed), PERTURBATION_VERSION, *map(str, parts)]))


@dataclass(frozen=True)
class SeededMention:
    mention_idx: int
    true_entity_idx: int  # ground truth
    text: str
    kind: NameKind | None  # None = the canonical name, unperturbed
    changed: bool


@dataclass(frozen=True)
class ERCorpus:
    """Entities with canonical names, mentions with ground truth, and the parameters used."""

    entities: tuple[str, ...]  # canonical names; index = true entity idx
    mentions: tuple[SeededMention, ...]
    distractor_of: dict[int, int]  # distractor entity idx -> the entity it imitates
    seed: int
    perturbation_rate: float
    version: str = PERTURBATION_VERSION


def generate_er_corpus(
    names: list[str],
    *,
    seed: int,
    perturbation_rate: float,
    mentions_per_entity: int = 2,
    distractor_fraction: float = 0.2,
    kinds: tuple[NameKind, ...] = DEFAULT_MENTION_KINDS,
) -> ERCorpus:
    """Entities = `names` (must be distinct) plus near-collision distractor entities made with
    NUMBER_CHANGE (different entities whose names differ in one digit). Each entity gets
    `mentions_per_entity` mentions; each mention is perturbed with probability `perturbation_rate`
    using one identity-preserving kind from `kinds`. Ground truth = the entity the mention was
    generated from."""
    if len(set(names)) != len(names):
        raise ValueError("canonical names must be distinct")
    if not 0.0 <= perturbation_rate <= 1.0 or not 0.0 <= distractor_fraction <= 1.0:
        raise ValueError("rates must be in [0, 1]")
    if mentions_per_entity < 1:
        raise ValueError("mentions_per_entity must be >= 1")
    if any(k not in IDENTITY_PRESERVING for k in kinds):
        raise ValueError("mention perturbations must be identity-preserving")
    entities = list(names)
    taken = set(names)
    distractor_of: dict[int, int] = {}
    for i, name in enumerate(names):
        rng = item_rng(seed, "distractor", i)
        if rng.random() >= distractor_fraction:
            continue
        for _ in range(10):
            cand = perturb_name(name, NameKind.NUMBER_CHANGE, rng)
            if cand not in taken:
                taken.add(cand)
                distractor_of[len(entities)] = i
                entities.append(cand)
                break
    mentions: list[SeededMention] = []
    for ent_idx, canonical in enumerate(entities):
        for j in range(mentions_per_entity):
            rng = item_rng(seed, "mention", ent_idx, j)
            if rng.random() < perturbation_rate:
                kind = rng.choice(kinds)
                text = perturb_name(canonical, kind, rng)
                mentions.append(
                    SeededMention(len(mentions), ent_idx, text, kind, text != canonical)
                )
            else:
                mentions.append(SeededMention(len(mentions), ent_idx, canonical, None, False))
    return ERCorpus(tuple(entities), tuple(mentions), distractor_of, seed, perturbation_rate)


# ------------------------------------------------------------------ seeded contradiction corpus
@dataclass(frozen=True)
class SeededContradiction:
    """Ground truth: a contradiction that was deliberately planted."""

    type: ContradictionType
    members: frozenset[str]  # evidence/claim ids


@dataclass(frozen=True)
class ERLinkFixture:
    link_id: str
    mention_id: str
    entity_id: str
    status: LinkStatus


@dataclass
class ContradictionCorpus:
    case_id: str
    evidence: dict[str, EvidenceDraft] = field(default_factory=dict)
    claims: dict[str, ClaimDraft] = field(default_factory=dict)
    verifications: dict[str, VerificationDraft] = field(default_factory=dict)  # by claim id
    links: dict[str, ERLinkFixture] = field(default_factory=dict)
    seeded: list[SeededContradiction] = field(default_factory=list)
    decoy_pairs: list[frozenset[str]] = field(default_factory=list)  # consistent look-alikes
    seed: int = 0
    version: str = PERTURBATION_VERSION


_PRODUCER = Producer(kind=ProducerKind.TOOL, name="seed-generator", version=PERTURBATION_VERSION)
_INPUT = (EvidenceInput(ref_type=InputRefType.SOURCE, ref_id="seed", ref_hash="seed"),)
_SINGLE_VALUED = ("entity_type", "registered_country", "account_status", "beneficial_owner")
_VALUES = {
    "entity_type": ("Corporation", "Partnership", "Sole Proprietorship", "Individual"),
    "registered_country": ("India", "UK", "Germany", "Canada", "Portugal"),
    "account_status": ("ACTIVE", "DORMANT", "CLOSED"),
    "beneficial_owner": ("owner_a", "owner_b", "owner_c", "owner_d"),
}


def _verification(
    claim_id: str, status: VerificationStatus, outcome: CheckOutcome
) -> VerificationDraft:
    check = VerificationCheck(check_id="DET-1", kind=CheckKind.DETERMINISTIC, outcome=outcome)
    return VerificationDraft(
        claim_id=claim_id, status=status, checks=(check,), rationale="seeded",
        verifier_name="seed", verifier_version="1", inputs_seen_hash="h",
    )  # fmt: skip


class _Builder:
    def __init__(self, corpus: ContradictionCorpus) -> None:
        self.c = corpus
        self.n = 0

    def evidence(
        self,
        *,
        facts: list[dict[str, object]] | None = None,
        events: list[dict[str, object]] | None = None,
        etype: EvidenceType = EvidenceType.TXN_AGGREGATE,
        extra: dict[str, object] | None = None,
    ) -> str:
        self.n += 1
        eid = f"ev_seed{self.c.seed}_{self.n:05d}"
        content: dict[str, object] = {"n": self.n}
        if facts is not None:
            content["facts"] = facts
        if events is not None:
            content["events"] = events
        content.update(extra or {})
        self.c.evidence[eid] = EvidenceDraft(
            case_id=self.c.case_id,
            evidence_type=etype,
            content=content,
            summary_text=f"seeded evidence {self.n}",
            producer=_PRODUCER,
            as_of=SEED_AS_OF,
            inputs=_INPUT,
            completeness=Completeness.FULL,
        )
        return eid

    def claim(
        self,
        subject: str,
        predicate: str,
        value: str | float | int,
        unit: str | None,
        cites: list[tuple[str, SupportRole]],
        time_scope: str | None = None,
    ) -> str:
        self.n += 1
        cid = f"clm_seed{self.c.seed}_{self.n:05d}"
        self.c.claims[cid] = ClaimDraft(
            case_id=self.c.case_id,
            author_kind=AuthorKind.AGENT,
            author_name="seed-generator",
            author_version=PERTURBATION_VERSION,
            structure=ClaimStructure(
                subject_ref=subject, predicate=predicate, object_value=value, unit=unit,
                time_scope=time_scope,
            ),
            text=f"{subject} {predicate} {value}",
            claim_kind=ClaimKind.AGGREGATE,
            citations=tuple(Citation(evidence_id=e, support_role=r) for e, r in cites),
        )  # fmt: skip
        return cid


def _fact(subject: str, predicate: str, value: object, unit: str | None = None,
          time_scope: str | None = None) -> dict[str, object]:  # fmt: skip
    return {"subject_ref": subject, "predicate": predicate, "value": value, "unit": unit,
            "time_scope": time_scope}  # fmt: skip


def generate_contradiction_corpus(
    *, seed: int, n_per_type: int = 20, n_decoys_per_type: int = 20, case_id: str = "case_seed"
) -> ContradictionCorpus:
    """Plant `n_per_type` contradictions of each detectable type and `n_decoys_per_type`
    consistent look-alikes (the decoys are what make precision measurable). Ground truth is in
    `seeded` (what MUST be found) and `decoy_pairs` (what must NOT be flagged)."""
    corpus = ContradictionCorpus(case_id=case_id, seed=seed)
    b = _Builder(corpus)

    for i in range(n_per_type):  # ----- ATTRIBUTE_CONFLICT: single-valued attribute, two values
        rng = item_rng(seed, "attr", i)
        pred = rng.choice(_SINGLE_VALUED)
        v1, v2 = rng.sample(_VALUES[pred], 2)
        subj = f"acct_{seed}_a{i}"
        e1 = b.evidence(facts=[_fact(subj, pred, v1)])
        e2 = b.evidence(facts=[_fact(subj, pred, v2)])
        corpus.seeded.append(
            SeededContradiction(ContradictionType.ATTRIBUTE_CONFLICT, frozenset({e1, e2}))
        )
    for i in range(n_decoys_per_type):
        rng = item_rng(seed, "attr_decoy", i)
        subj = f"acct_{seed}_ad{i}"
        form = i % 3
        if form == 0:  # same value, different case/whitespace
            pred = rng.choice(_SINGLE_VALUED)
            v = rng.choice(_VALUES[pred])
            e1 = b.evidence(facts=[_fact(subj, pred, v)])
            e2 = b.evidence(facts=[_fact(subj, pred, f" {v.upper()} ")])
        elif form == 1:  # multi-valued predicate legitimately differs
            e1 = b.evidence(facts=[_fact(subj, "counterparty", "acct_x")])
            e2 = b.evidence(facts=[_fact(subj, "counterparty", "acct_y")])
        else:  # same predicate, different time scope
            pred = rng.choice(_SINGLE_VALUED)
            v1, v2 = rng.sample(_VALUES[pred], 2)
            e1 = b.evidence(facts=[_fact(subj, pred, v1, time_scope="2022-08")])
            e2 = b.evidence(facts=[_fact(subj, pred, v2, time_scope="2022-09")])
        corpus.decoy_pairs.append(frozenset({e1, e2}))

    for i in range(n_per_type):  # ----- AMOUNT_MISMATCH: claim total vs evidence total
        rng = item_rng(seed, "amt", i)
        base = Decimal(rng.randrange(1_000, 900_000))
        factor = rng.choice((Decimal("1.005"), Decimal("1.03"), Decimal("1.25"), Decimal("0.5")))
        subj = f"acct_{seed}_m{i}"
        e1 = b.evidence(facts=[_fact(subj, "total_outflow", str(base), "USD", "2022-09")])
        cl = b.claim(
            subj,
            "total_outflow",
            float(base * factor),
            "USD",
            [(e1, SupportRole.SUPPORTS)],
            "2022-09",
        )
        corpus.seeded.append(
            SeededContradiction(ContradictionType.AMOUNT_MISMATCH, frozenset({e1, cl}))
        )
    for i in range(n_decoys_per_type):
        rng = item_rng(seed, "amt_decoy", i)
        base = Decimal(rng.randrange(1_000, 900_000))
        subj = f"acct_{seed}_md{i}"
        form = i % 3
        if form == 0:  # equal under formatting
            e1 = b.evidence(facts=[_fact(subj, "total_outflow", f"{base}.00", "USD", "2022-09")])
            e2 = b.evidence(facts=[_fact(subj, "total_outflow", str(base), "USD", "2022-09")])
        elif form == 1:  # different unit: not comparable
            e1 = b.evidence(facts=[_fact(subj, "total_outflow", str(base), "USD", "2022-09")])
            e2 = b.evidence(facts=[_fact(subj, "total_outflow", str(base * 2), "EUR", "2022-09")])
        else:  # different period
            e1 = b.evidence(facts=[_fact(subj, "total_outflow", str(base), "USD", "2022-08")])
            e2 = b.evidence(facts=[_fact(subj, "total_outflow", str(base * 3), "USD", "2022-09")])
        corpus.decoy_pairs.append(frozenset({e1, e2}))

    for i in range(n_per_type):  # ----- TIMELINE_IMPOSSIBLE: activity before the account existed
        rng = item_rng(seed, "tl", i)
        opened = SEED_AS_OF - timedelta(days=rng.randrange(10, 400))
        before = opened - timedelta(days=rng.randrange(1, 9))
        subj = f"acct_{seed}_t{i}"
        e1 = b.evidence(
            events=[{"subject_ref": subj, "kind": "ACCOUNT_OPENED", "at": opened.isoformat()}]
        )
        e2 = b.evidence(
            events=[{"subject_ref": subj, "kind": "TRANSACTION", "at": before.isoformat()}]
        )
        corpus.seeded.append(
            SeededContradiction(ContradictionType.TIMELINE_IMPOSSIBLE, frozenset({e1, e2}))
        )
    for i in range(n_decoys_per_type):
        rng = item_rng(seed, "tl_decoy", i)
        opened = SEED_AS_OF - timedelta(days=rng.randrange(10, 400))
        subj = f"acct_{seed}_td{i}"
        if i % 2 == 0:  # correct order
            when = opened + timedelta(days=rng.randrange(1, 9))
            e1 = b.evidence(
                events=[{"subject_ref": subj, "kind": "ACCOUNT_OPENED", "at": opened.isoformat()}]
            )
            e2 = b.evidence(
                events=[{"subject_ref": subj, "kind": "TRANSACTION", "at": when.isoformat()}]
            )
        else:  # out of order but about different accounts
            before = opened - timedelta(days=3)
            e1 = b.evidence(
                events=[{"subject_ref": subj, "kind": "ACCOUNT_OPENED", "at": opened.isoformat()}]
            )
            e2 = b.evidence(
                events=[
                    {
                        "subject_ref": subj + "_other",
                        "kind": "TRANSACTION",
                        "at": before.isoformat(),
                    }
                ]
            )
        corpus.decoy_pairs.append(frozenset({e1, e2}))

    for i in range(n_per_type):  # ----- CLAIM_VS_EVIDENCE: verifier says CONTRADICTED
        subj = f"acct_{seed}_c{i}"
        e1 = b.evidence(facts=[_fact(subj, "velocity_per_day", "3")])
        cl = b.claim(subj, "velocity_per_day_high", True, None, [(e1, SupportRole.CONTRADICTS)])
        corpus.verifications[cl] = _verification(
            cl, VerificationStatus.CONTRADICTED, CheckOutcome.FAIL
        )
        corpus.seeded.append(
            SeededContradiction(ContradictionType.CLAIM_VS_EVIDENCE, frozenset({e1, cl}))
        )
    for i in range(n_decoys_per_type):
        subj = f"acct_{seed}_cd{i}"
        e1 = b.evidence(facts=[_fact(subj, "velocity_per_day", "30")])
        cl = b.claim(subj, "velocity_per_day_high", True, None, [(e1, SupportRole.SUPPORTS)])
        status = (
            VerificationStatus.VERIFIED if i % 2 == 0 else VerificationStatus.PARTIALLY_SUPPORTED
        )
        corpus.verifications[cl] = _verification(cl, status, CheckOutcome.PASS)
        corpus.decoy_pairs.append(frozenset({e1, cl}))

    for i in range(n_per_type):  # ----- ER_CONFLICT: one mention linked to two different entities
        mention = f"men_seed{seed}_{i}"
        s2 = LinkStatus.CONFIRMED if i % 2 == 0 else LinkStatus.PROPOSED
        l1, l2 = f"erl_seed{seed}_{i}a", f"erl_seed{seed}_{i}b"
        corpus.links[l1] = ERLinkFixture(l1, mention, f"ent_{seed}_{i}_1", LinkStatus.CONFIRMED)
        corpus.links[l2] = ERLinkFixture(l2, mention, f"ent_{seed}_{i}_2", s2)
        e1 = b.evidence(etype=EvidenceType.ER_LINK_EVIDENCE, extra=_er_content(corpus.links[l1]))
        e2 = b.evidence(etype=EvidenceType.ER_LINK_EVIDENCE, extra=_er_content(corpus.links[l2]))
        corpus.seeded.append(
            SeededContradiction(ContradictionType.ER_CONFLICT, frozenset({e1, e2}))
        )
    for i in range(n_decoys_per_type):
        mention = f"men_seed{seed}_d{i}"
        l1, l2 = f"erl_seed{seed}_d{i}a", f"erl_seed{seed}_d{i}b"
        if i % 2 == 0:  # same entity twice
            corpus.links[l1] = ERLinkFixture(l1, mention, f"ent_{seed}_d{i}", LinkStatus.CONFIRMED)
            corpus.links[l2] = ERLinkFixture(l2, mention, f"ent_{seed}_d{i}", LinkStatus.PROPOSED)
        else:  # the competing link was rejected, so no conflict remains
            corpus.links[l1] = ERLinkFixture(
                l1, mention, f"ent_{seed}_d{i}_1", LinkStatus.CONFIRMED
            )
            corpus.links[l2] = ERLinkFixture(l2, mention, f"ent_{seed}_d{i}_2", LinkStatus.REJECTED)
        e1 = b.evidence(etype=EvidenceType.ER_LINK_EVIDENCE, extra=_er_content(corpus.links[l1]))
        e2 = b.evidence(etype=EvidenceType.ER_LINK_EVIDENCE, extra=_er_content(corpus.links[l2]))
        corpus.decoy_pairs.append(frozenset({e1, e2}))
    return corpus


def _er_content(link: ERLinkFixture) -> dict[str, object]:
    return {"link_id": link.link_id, "mention_id": link.mention_id, "entity_id": link.entity_id}


def to_detection_input(corpus: ContradictionCorpus) -> DetectionInput:
    """Wrap a seeded corpus in the detectors' input type (ids come from the corpus)."""
    links = tuple(
        ERLinkView(
            link_id=link.link_id,
            mention_id=link.mention_id,
            entity_id=link.entity_id,
            method=LinkMethod.FUZZY_NAME,
            match_score=0.9,
            status=link.status,
            scorer_version=PERTURBATION_VERSION,
        )
        for link in corpus.links.values()
    )
    return DetectionInput(
        evidence=tuple(StoredEvidence(i, d) for i, d in corpus.evidence.items()),
        claims=tuple(
            StoredClaim(i, d, corpus.verifications.get(i)) for i, d in corpus.claims.items()
        ),
        links=links,
    )
