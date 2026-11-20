"""Provenance walk (EVIDENCE_MODEL 9, SAC-01).

`walk(store, leaves, claim_id)` follows claim -> cited evidence -> input evidence -> ... down to
SOURCE / DOCUMENT / REFERENCE / HUMAN leaves and verifies at every node:
- the stored content hash equals a hash recomputed from the stored fields,
- each input's recorded hash equals the hash of the thing it points at (evidence: its stored
  hash; leaves: the hash the system holds now),
- no reference dangles, no cycle exists, and non-human evidence has inputs.
Problems are collected, not raised, so a report can show every defect; `assert_intact` raises.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from aegis.common.errors import IntegrityError, NotFound
from aegis.domain.evidence import InputRefType, ProducerKind
from aegis.evidence.drafts import with_query_refs
from aegis.evidence.store import EvidenceStore, normalised
from aegis.evidence.types import LeafHashes

LEAF_KINDS = frozenset({"SOURCE", "DOCUMENT", "REFERENCE", "HUMAN"})


class Problem(StrEnum):
    DANGLING_REF = "DANGLING_REF"
    EVIDENCE_HASH_MISMATCH = "EVIDENCE_HASH_MISMATCH"
    INPUT_HASH_MISMATCH = "INPUT_HASH_MISMATCH"
    LEAF_UNKNOWN = "LEAF_UNKNOWN"
    LEAF_HASH_MISMATCH = "LEAF_HASH_MISMATCH"
    NO_INPUTS = "NO_INPUTS"
    CYCLE = "CYCLE"
    CROSS_CASE = "CROSS_CASE"


@dataclass(frozen=True)
class Node:
    kind: str  # CLAIM | EVIDENCE | SOURCE | DOCUMENT | REFERENCE | HUMAN
    node_id: str
    hash: str | None


@dataclass
class ProvenanceReport:
    claim_id: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    problems: list[tuple[Problem, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def leaves(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind in LEAF_KINDS]

    def assert_intact(self) -> None:
        if self.problems:
            raise IntegrityError(
                f"provenance of {self.claim_id} broken: "
                + "; ".join(f"{p.value}({d})" for p, d in self.problems)
            )


def walk(store: EvidenceStore, leaves: LeafHashes, claim_id: str) -> ProvenanceReport:
    leaves = with_query_refs(leaves)
    claim = store.get_claim(claim_id)
    rep = ProvenanceReport(claim_id)
    rep.nodes[claim_id] = Node("CLAIM", claim_id, None)
    visiting: set[str] = set()
    done: set[str] = set()

    def visit_evidence(eid: str, parent: str, expected_hash: str | None) -> None:
        rep.edges.append((parent, eid))
        if eid in done:
            if expected_hash is not None and rep.nodes[eid].hash != expected_hash:
                rep.problems.append((Problem.INPUT_HASH_MISMATCH, f"{parent}->{eid}"))
            return
        if eid in visiting:
            rep.problems.append((Problem.CYCLE, eid))
            return
        try:
            ev = store.get_evidence(eid)
        except NotFound:
            rep.problems.append((Problem.DANGLING_REF, eid))
            return
        visiting.add(eid)
        d = ev.draft
        rep.nodes[eid] = Node("EVIDENCE", eid, ev.content_hash)
        if d.case_id != claim.draft.case_id:
            rep.problems.append((Problem.CROSS_CASE, eid))
        if normalised(d).content_hash() != ev.content_hash:
            rep.problems.append((Problem.EVIDENCE_HASH_MISMATCH, eid))
        if expected_hash is not None and expected_hash != ev.content_hash:
            rep.problems.append((Problem.INPUT_HASH_MISMATCH, f"{parent}->{eid}"))
        human = d.producer.kind is ProducerKind.HUMAN
        if not d.inputs:
            if human:  # a human-authored item is itself a leaf
                rep.nodes[eid] = Node("HUMAN", eid, ev.content_hash)
            else:
                rep.problems.append((Problem.NO_INPUTS, eid))
        for inp in d.inputs:
            if inp.ref_type is InputRefType.EVIDENCE:
                visit_evidence(inp.ref_id, eid, inp.ref_hash)
            else:
                lid = f"{inp.ref_type.value}:{inp.ref_id}"
                rep.edges.append((eid, lid))
                rep.nodes.setdefault(lid, Node(inp.ref_type.value, inp.ref_id, inp.ref_hash))
                held = leaves.hash_of(inp.ref_type, inp.ref_id)
                if held is None:
                    rep.problems.append((Problem.LEAF_UNKNOWN, lid))
                elif held != inp.ref_hash:
                    rep.problems.append((Problem.LEAF_HASH_MISMATCH, lid))
        visiting.discard(eid)
        done.add(eid)

    for cit in claim.draft.citations:
        visit_evidence(cit.evidence_id, claim_id, None)
    return rep


def walk_case(store: EvidenceStore, leaves: LeafHashes, case_id: str) -> list[ProvenanceReport]:
    return [walk(store, leaves, c.claim_id) for c in store.list_claims(case_id)]
