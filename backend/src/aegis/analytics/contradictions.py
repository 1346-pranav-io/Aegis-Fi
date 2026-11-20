"""Deterministic contradiction detectors (FR-CON-01, AG-08). Pure functions: no database, no
clock, no LLM, no randomness. Same input, same output, same order.

Input convention (the frozen contract in domain/evidence.py leaves `content` free-form, so the
shapes the detectors read are declared here and are an integration point for Phase 3's tools):

* evidence.content["facts"]  = [{"subject_ref", "predicate", "value", "unit"?, "time_scope"?}]
  Numeric (amount-like) facts carry a `unit` and a decimal STRING value (content hashes forbid
  floats). Categorical facts have no unit.
* evidence.content["events"] = [{"subject_ref", "kind", "at"}]   (`at`: ISO-8601 with offset)
* ER_LINK_EVIDENCE content   = {"link_id", "mention_id", "entity_id"}
* a claim is also a fact: structure(subject_ref, predicate, object_value, unit, time_scope)

Detectors (one per ContradictionType; every draft links exactly two items):

* ATTRIBUTE_CONFLICT   two sources give different values for the same single-valued categorical
                       predicate of the same subject and time scope.
* AMOUNT_MISMATCH      two sources give numeric values (same unit, subject, predicate, time
                       scope) that differ by more than `amount_tolerance`.
* TIMELINE_IMPOSSIBLE  an ordering rule is violated across two items (e.g. activity before the
                       account was opened).
* CLAIM_VS_EVIDENCE    a claim's latest verification is CONTRADICTED, or a claim cites evidence in
                       the CONTRADICTS role.
* ER_CONFLICT          one mention is linked, by live (CONFIRMED/PROPOSED) links, to two different
                       resolved entities.
* SOURCE_DISAGREEMENT  NOT implemented: the frozen evidence contract has no notion of "source
                       system", so it cannot be defined without inventing one (see PHASE4_NOTES).

Severity is rule-defined (stated per detector) and is not a probability.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aegis.common.hashing import content_hash
from aegis.domain.entities import ERLinkView, LinkStatus
from aegis.domain.evidence import (
    ClaimDraft,
    ContradictionDraft,
    ContradictionMember,
    ContradictionType,
    EvidenceDraft,
    EvidenceType,
    MemberType,
    Severity,
    SupportRole,
    VerificationDraft,
    VerificationStatus,
)
from aegis.domain.evidence_content import events_of, facts_of

DETECTOR_VERSION = "1.0.0"

DEFAULT_SINGLE_VALUED: frozenset[str] = frozenset(
    {"entity_type", "registered_country", "account_status", "beneficial_owner"}
)
DEFAULT_IDENTITY_PREDICATES: frozenset[str] = frozenset(
    {"entity_type", "registered_country", "beneficial_owner"}
)
# (first, then): `then` must not be earlier than `first`.
DEFAULT_TIMELINE_RULES: tuple[tuple[str, str], ...] = (
    ("ACCOUNT_OPENED", "TRANSACTION"),
    ("ACCOUNT_OPENED", "ACCOUNT_CLOSED"),
    ("TRANSACTION", "ACCOUNT_CLOSED"),
)


@dataclass(frozen=True)
class DetectorConfig:
    single_valued_predicates: frozenset[str] = DEFAULT_SINGLE_VALUED
    identity_predicates: frozenset[str] = DEFAULT_IDENTITY_PREDICATES
    timeline_rules: tuple[tuple[str, str], ...] = DEFAULT_TIMELINE_RULES
    amount_tolerance: Decimal = Decimal("0.005")  # absolute, in the fact's own unit
    high_relative_diff: Decimal = Decimal("0.10")
    medium_relative_diff: Decimal = Decimal("0.01")


DEFAULT_CONFIG = DetectorConfig()


@dataclass(frozen=True)
class StoredEvidence:
    evidence_id: str
    draft: EvidenceDraft


@dataclass(frozen=True)
class StoredClaim:
    claim_id: str
    draft: ClaimDraft
    verification: VerificationDraft | None = None  # the claim's latest verification, if any


@dataclass(frozen=True)
class DetectionInput:
    evidence: tuple[StoredEvidence, ...] = ()
    claims: tuple[StoredClaim, ...] = ()
    links: tuple[ERLinkView, ...] = ()
    entity_roots: Mapping[str, str] = field(default_factory=dict)  # entity -> root after merges


def fingerprint(d: ContradictionDraft) -> str:
    """Stable identity of a finding: type + detector + the set of members."""
    members = sorted((m.member_type.value, m.member_id) for m in d.members)
    return content_hash(
        {"type": d.type.value, "detector": [d.detector_name, d.detector_version], "m": members}
    )


# ------------------------------------------------------------------------------------- helpers
@dataclass(frozen=True)
class _Fact:
    member_type: MemberType
    source_id: str
    subject: str
    predicate: str
    value: str
    unit: str | None
    time_scope: str | None


def _norm_text(v: object) -> str:
    return " ".join(str(v).split()).casefold()


def _dec(v: str) -> Decimal | None:
    try:
        d = Decimal(v.strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _facts(inp: DetectionInput) -> list[_Fact]:
    out: list[_Fact] = []
    for e in inp.evidence:
        raw = e.draft.content.get("facts")
        if not isinstance(raw, list):
            continue
        for f in raw:
            if not isinstance(f, dict) or "value" not in f or f["value"] is None:
                continue
            if not f.get("subject_ref") or not f.get("predicate"):
                continue
            out.append(
                _Fact(
                    MemberType.EVIDENCE,
                    e.evidence_id,
                    str(f["subject_ref"]),
                    str(f["predicate"]),
                    str(f["value"]),
                    None if f.get("unit") is None else str(f["unit"]),
                    None if f.get("time_scope") is None else str(f["time_scope"]),
                )  # fmt: skip
            )
    for c in inp.claims:
        s = c.draft.structure
        if s.object_value is None:
            continue
        out.append(
            _Fact(MemberType.CLAIM, c.claim_id, s.subject_ref, s.predicate, str(s.object_value),
                  s.unit, s.time_scope)
        )  # fmt: skip
    return sorted(out, key=lambda f: (f.subject, f.predicate, f.time_scope or "", f.unit or "",
                                      f.member_type.value, f.source_id, f.value))  # fmt: skip


def malformed_content(inp: DetectionInput) -> list[str]:
    """Ids of evidence whose `facts` / `events` do not follow the shared declaration
    (domain.evidence_content). The detectors skip such entries; callers must REPORT them, because
    skipping silently would look like 'no contradictions'."""
    bad: list[str] = []
    for e in inp.evidence:
        try:
            facts_of(e.draft.content)
            events_of(e.draft.content)
        except ValueError:
            bad.append(e.evidence_id)
    return sorted(bad)


def _draft(
    case_id: str,
    ctype: ContradictionType,
    severity: Severity,
    name: str,
    members: list[ContradictionMember],
) -> ContradictionDraft:
    members = sorted(members, key=lambda m: (m.member_type.value, m.member_id))
    return ContradictionDraft(
        case_id=case_id, type=ctype, severity=severity, detector_name=name,
        detector_version=DETECTOR_VERSION, members=tuple(members),
    )  # fmt: skip


def _member(t: MemberType, i: str, role: str) -> ContradictionMember:
    return ContradictionMember(member_type=t, member_id=i, role=role[:200])


def _case_id(inp: DetectionInput) -> str:
    ids = {e.draft.case_id for e in inp.evidence} | {c.draft.case_id for c in inp.claims}
    if len(ids) > 1:
        raise ValueError("detection input mixes several cases")
    return next(iter(ids), "")


# ------------------------------------------------------------------------------------ detectors
def detect_attribute_conflicts(
    inp: DetectionInput, case_id: str, cfg: DetectorConfig = DEFAULT_CONFIG
) -> list[ContradictionDraft]:
    groups: dict[tuple[str, str, str], list[_Fact]] = defaultdict(list)
    for f in _facts(inp):
        if f.unit is None and f.predicate in cfg.single_valued_predicates:
            groups[(f.subject, f.predicate, f.time_scope or "")].append(f)
    out: list[ContradictionDraft] = []
    for key in sorted(groups):
        g = groups[key]
        for i, a in enumerate(g):
            for b in g[i + 1 :]:
                if (a.member_type, a.source_id) == (b.member_type, b.source_id):
                    continue
                if _norm_text(a.value) == _norm_text(b.value):
                    continue
                sev = Severity.HIGH if a.predicate in cfg.identity_predicates else Severity.MEDIUM
                out.append(
                    _draft(case_id, ContradictionType.ATTRIBUTE_CONFLICT, sev, "attribute_conflict",
                           [_member(a.member_type, a.source_id, f"{a.predicate}={a.value}"),
                            _member(b.member_type, b.source_id, f"{b.predicate}={b.value}")])
                )  # fmt: skip
    return out


def detect_amount_mismatches(
    inp: DetectionInput, case_id: str, cfg: DetectorConfig = DEFAULT_CONFIG
) -> list[ContradictionDraft]:
    groups: dict[tuple[str, str, str, str], list[tuple[_Fact, Decimal]]] = defaultdict(list)
    for f in _facts(inp):
        if f.unit is None:
            continue
        d = _dec(f.value)
        if d is not None:
            groups[(f.subject, f.predicate, f.time_scope or "", f.unit)].append((f, d))
    out: list[ContradictionDraft] = []
    for key in sorted(groups):
        g = groups[key]
        for i, (a, da) in enumerate(g):
            for b, db in g[i + 1 :]:
                if (a.member_type, a.source_id) == (b.member_type, b.source_id):
                    continue
                diff = abs(da - db)
                if diff <= cfg.amount_tolerance:
                    continue
                top = max(abs(da), abs(db))
                rel = diff / top if top else Decimal(0)
                sev = (
                    Severity.HIGH
                    if rel >= cfg.high_relative_diff
                    else Severity.MEDIUM
                    if rel >= cfg.medium_relative_diff
                    else Severity.LOW
                )
                unit = a.unit or ""
                out.append(
                    _draft(case_id, ContradictionType.AMOUNT_MISMATCH, sev, "amount_mismatch",
                           [_member(a.member_type, a.source_id, f"{a.predicate}={a.value} {unit}"),
                            _member(b.member_type, b.source_id, f"{b.predicate}={b.value} {unit}")])
                )  # fmt: skip
    return out


def _events(inp: DetectionInput) -> list[tuple[str, str, str, datetime]]:
    """(subject, kind, evidence_id, at) for well-formed, timezone-aware events."""
    out: list[tuple[str, str, str, datetime]] = []
    for e in inp.evidence:
        raw = e.draft.content.get("events")
        if not isinstance(raw, list):
            continue
        for ev in raw:
            if not isinstance(ev, dict) or not ev.get("subject_ref") or not ev.get("kind"):
                continue
            try:
                at = datetime.fromisoformat(str(ev["at"]))
            except (KeyError, ValueError):
                continue
            if at.tzinfo is None:
                continue  # an event without an offset cannot be ordered reliably
            out.append((str(ev["subject_ref"]), str(ev["kind"]), e.evidence_id, at))
    return sorted(out, key=lambda t: (t[0], t[1], t[2], t[3]))


def detect_timeline_impossible(
    inp: DetectionInput, case_id: str, cfg: DetectorConfig = DEFAULT_CONFIG
) -> list[ContradictionDraft]:
    by_subject: dict[str, list[tuple[str, str, datetime]]] = defaultdict(list)
    for subj, kind, eid, at in _events(inp):
        by_subject[subj].append((kind, eid, at))
    out: list[ContradictionDraft] = []
    for subj in sorted(by_subject):
        evs = by_subject[subj]
        for first_kind, then_kind in cfg.timeline_rules:
            for k1, e1, t1 in evs:
                if k1 != first_kind:
                    continue
                for k2, e2, t2 in evs:
                    if k2 != then_kind or e1 == e2 or t2 >= t1:
                        continue
                    out.append(
                        _draft(case_id, ContradictionType.TIMELINE_IMPOSSIBLE, Severity.HIGH,
                               "timeline_impossible",
                               [_member(MemberType.EVIDENCE, e1, f"{k1}@{t1.isoformat()}"),
                                _member(MemberType.EVIDENCE, e2, f"{k2}@{t2.isoformat()}")])
                    )  # fmt: skip
    return out


def claim_vs_evidence_findings(
    case_id: str,
    claim_id: str,
    cited: Iterable[tuple[str, SupportRole]],
    *,
    contradicted: bool,
) -> list[ContradictionDraft]:
    """The single definition of a CLAIM_VS_EVIDENCE finding, used by the detector below AND by the
    Verification agent so both produce IDENTICAL records (same fingerprint, so storing both is
    idempotent). One finding per contradicting evidence item: the items cited with role
    CONTRADICTS if any, otherwise (verifier said CONTRADICTED) every cited item. Severity is HIGH
    when the verifier refuted the claim, MEDIUM when only the citation role says so."""
    cited = sorted(set(cited))
    against = [eid for eid, role in cited if role is SupportRole.CONTRADICTS]
    if not contradicted and not against:
        return []
    ev_ids = sorted(set(against) if against else {eid for eid, _ in cited})
    sev = Severity.HIGH if contradicted else Severity.MEDIUM
    role = "verifier: CONTRADICTED" if contradicted else "cited as CONTRADICTS"
    return [
        _draft(case_id, ContradictionType.CLAIM_VS_EVIDENCE, sev, "claim_vs_evidence",
               [_member(MemberType.CLAIM, claim_id, "claim"),
                _member(MemberType.EVIDENCE, eid, role)])
        for eid in ev_ids
    ]  # fmt: skip


def detect_claim_vs_evidence(
    inp: DetectionInput, case_id: str, cfg: DetectorConfig = DEFAULT_CONFIG
) -> list[ContradictionDraft]:
    known = {e.evidence_id for e in inp.evidence}
    out: list[ContradictionDraft] = []
    for c in sorted(inp.claims, key=lambda c: c.claim_id):
        contradicted = c.verification is not None and (
            c.verification.status is VerificationStatus.CONTRADICTED
        )
        out += claim_vs_evidence_findings(
            case_id,
            c.claim_id,
            [(x.evidence_id, x.support_role) for x in c.draft.citations if x.evidence_id in known],
            contradicted=contradicted,
        )
    return out


def detect_er_conflicts(
    inp: DetectionInput, case_id: str, cfg: DetectorConfig = DEFAULT_CONFIG
) -> list[ContradictionDraft]:
    views = {v.link_id: v for v in inp.links}
    live = (LinkStatus.CONFIRMED, LinkStatus.PROPOSED)
    by_mention: dict[str, list[tuple[str, ERLinkView]]] = defaultdict(list)
    for e in sorted(inp.evidence, key=lambda e: e.evidence_id):
        if e.draft.evidence_type is not EvidenceType.ER_LINK_EVIDENCE:
            continue
        v = views.get(str(e.draft.content.get("link_id", "")))
        if v is not None and v.status in live:
            by_mention[v.mention_id].append((e.evidence_id, v))
    out: list[ContradictionDraft] = []
    for mention in sorted(by_mention):
        items = by_mention[mention]
        for i, (ea, va) in enumerate(items):
            for eb, vb in items[i + 1 :]:
                ra = inp.entity_roots.get(va.entity_id, va.entity_id)
                rb = inp.entity_roots.get(vb.entity_id, vb.entity_id)
                if ra == rb or ea == eb:
                    continue
                both = va.status is LinkStatus.CONFIRMED and vb.status is LinkStatus.CONFIRMED
                role_a = f"{va.link_id}:{va.status.value}->{ra}"
                role_b = f"{vb.link_id}:{vb.status.value}->{rb}"
                sev = Severity.HIGH if both else Severity.MEDIUM
                out.append(
                    _draft(
                        case_id,
                        ContradictionType.ER_CONFLICT,
                        sev,
                        "er_conflict",
                        [
                            _member(MemberType.EVIDENCE, ea, role_a),
                            _member(MemberType.EVIDENCE, eb, role_b),
                        ],
                    )
                )
    return out


_ALL = (
    detect_attribute_conflicts,
    detect_amount_mismatches,
    detect_timeline_impossible,
    detect_claim_vs_evidence,
    detect_er_conflicts,
)


def detect_all(
    inp: DetectionInput, cfg: DetectorConfig = DEFAULT_CONFIG, case_id: str | None = None
) -> list[ContradictionDraft]:
    """Run every detector; deduplicate by fingerprint; order by (type, fingerprint)."""
    cid = case_id if case_id is not None else _case_id(inp)
    seen: dict[str, ContradictionDraft] = {}
    for det in _ALL:
        for d in det(inp, cid, cfg):
            seen.setdefault(fingerprint(d), d)
    return sorted(seen.values(), key=lambda d: (d.type.value, fingerprint(d)))


def evidence_ids_for(d: ContradictionDraft, inp: DetectionInput) -> set[str]:
    """Evidence a proposed explanation may legitimately cite: the evidence members, plus the
    evidence cited by claim members."""
    ids = {m.member_id for m in d.members if m.member_type is MemberType.EVIDENCE}
    claims = {c.claim_id: c for c in inp.claims}
    for m in d.members:
        if m.member_type is MemberType.CLAIM and m.member_id in claims:
            ids |= {x.evidence_id for x in claims[m.member_id].draft.citations}
    return ids


def pairs(drafts: Iterable[ContradictionDraft]) -> set[frozenset[str]]:
    return {frozenset(m.member_id for m in d.members) for d in drafts}
