"""Postgres implementation of the evidence store (runs as the aegis_app role).

The caller owns the transaction: nothing here commits, because migration 0009 checks provenance
and citation rules at COMMIT (deferred constraint triggers), so an evidence item and its inputs
(or a claim and its citations) must be written in one transaction.
"""

import json
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from aegis.analytics.contradictions import fingerprint
from aegis.common.errors import IntegrityError, NotFound
from aegis.common.hashing import canonical_json
from aegis.common.ids import new_id
from aegis.domain.evidence import (
    Citation,
    ClaimDraft,
    ClaimKind,
    ClaimStructure,
    Completeness,
    ContradictionDraft,
    ContradictionStatus,
    ContradictionType,
    EvidenceDraft,
    EvidenceInput,
    EvidenceType,
    InputRefType,
    Producer,
    SelfReportedConfidence,
    Severity,
    SupportRole,
    VerificationCheck,
    VerificationDraft,
    VerificationStatus,
)
from aegis.evidence.canon import hashable
from aegis.evidence.drafts import with_query_refs
from aegis.evidence.store import (
    UNRESOLVED_STATUSES,
    evidence_id_for,
    normalised,
    validate_new_claim,
    validate_new_evidence,
)
from aegis.evidence.types import (
    ContradictionView,
    LeafHashes,
    StoredClaim,
    StoredEvidence,
    StoredVerification,
)


def _jsonb(obj: Any) -> Jsonb:
    """JSON-safe copy: Decimals/datetimes go through the canonical (hashed) text form."""
    return Jsonb(json.loads(canonical_json(hashable(obj))))


class PostgresLeafHashes:
    """SOURCE leaves resolve against source.transaction / source.account content hashes.
    DOCUMENT and REFERENCE leaves are not stored anywhere yet (no document store in Phase 3), so
    they resolve to None, i.e. they cannot be cited."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def hash_of(self, ref_type: InputRefType, ref_id: str) -> str | None:
        if ref_type is not InputRefType.SOURCE:
            return None
        for table, col in (("transaction", "txn_id"), ("account", "account_id")):
            row = self._conn.execute(
                f"SELECT content_hash FROM source.{table} WHERE {col} = %s",  # noqa: S608
                (ref_id,),
            ).fetchone()
            if row:
                return str(row[0])
        return None


class PostgresEvidenceStore:
    def __init__(self, conn: psycopg.Connection, leaves: LeafHashes) -> None:
        self._c = conn
        self._leaves = with_query_refs(leaves)

    # ---------------------------------------------------------------- evidence
    def _evidence_from_row(self, row: tuple[Any, ...]) -> StoredEvidence:
        (eid, case_id, run_id, step_id, etype, content, summary, producer, as_of, w0, w1, h,
         completeness, notes, scores, supersedes, created_at) = row  # fmt: skip
        inputs = sorted(
            (
                EvidenceInput(ref_type=InputRefType(t), ref_id=i, ref_hash=rh)
                for t, i, rh in self._c.execute(
                    "SELECT ref_type, ref_id, ref_hash FROM evidence.evidence_input "
                    "WHERE evidence_id = %s",
                    (eid,),
                ).fetchall()
            ),
            key=lambda i: (i.ref_type.value, i.ref_id),
        )
        draft = EvidenceDraft(
            case_id=case_id,
            run_id=run_id,
            step_id=step_id,
            evidence_type=EvidenceType(etype),
            content=content,
            summary_text=summary,
            producer=Producer(**producer),
            as_of=as_of,
            window_start=w0,
            window_end=w1,
            inputs=tuple(inputs),
            completeness=Completeness(completeness),
            quality_notes=notes,
            scores=scores,
            supersedes=supersedes,
        )
        return StoredEvidence(evidence_id=eid, content_hash=h, created_at=created_at, draft=draft)

    _EV_COLS = (
        "evidence_id, case_id, run_id, step_id, evidence_type, content, summary_text, producer, "
        "as_of, window_start, window_end, content_hash, completeness, quality_notes, scores, "
        "supersedes, created_at"
    )

    def get_evidence(self, evidence_id: str) -> StoredEvidence:
        row = self._c.execute(
            f"SELECT {self._EV_COLS} FROM evidence.evidence_item WHERE evidence_id = %s",  # noqa: S608
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise NotFound(evidence_id)
        return self._evidence_from_row(row)

    def _lookup(self, evidence_id: str) -> StoredEvidence | None:
        try:
            return self.get_evidence(evidence_id)
        except NotFound:
            return None

    def create_evidence(self, draft: EvidenceDraft) -> tuple[StoredEvidence, bool]:
        draft = normalised(draft)
        h = draft.content_hash()
        row = self._c.execute(
            "SELECT evidence_id FROM evidence.evidence_item "
            "WHERE case_id = %s AND content_hash = %s",
            (draft.case_id, h),
        ).fetchone()
        if row:
            return self.get_evidence(row[0]), False
        validate_new_evidence(
            draft,
            lambda i: (
                (e.draft.case_id, e.content_hash) if (e := self._lookup(i)) is not None else None
            ),
            self._leaves,
        )
        eid = evidence_id_for(draft.case_id, h)
        got = self._c.execute(
            "INSERT INTO evidence.evidence_item (evidence_id, case_id, run_id, step_id, "
            "evidence_type, content, summary_text, producer, as_of, window_start, window_end, "
            "content_hash, completeness, quality_notes, scores, supersedes, schema_version, "
            "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "clock_timestamp()) "
            "ON CONFLICT (case_id, content_hash) DO NOTHING RETURNING evidence_id",
            (
                eid, draft.case_id, draft.run_id, draft.step_id, draft.evidence_type.value,
                _jsonb(draft.content), draft.summary_text,
                _jsonb(draft.producer.model_dump(mode="json")), draft.as_of, draft.window_start,
                draft.window_end, h, draft.completeness.value, draft.quality_notes,
                _jsonb(draft.scores) if draft.scores is not None else None, draft.supersedes, 1,
            ),
        ).fetchone()  # fmt: skip
        if got is None:  # lost a race with an identical insert in another transaction
            row = self._c.execute(
                "SELECT evidence_id FROM evidence.evidence_item "
                "WHERE case_id = %s AND content_hash = %s",
                (draft.case_id, h),
            ).fetchone()
            if row is None:
                raise IntegrityError("evidence insert conflicted but no row found")
            return self.get_evidence(row[0]), False
        for i in draft.inputs:
            self._c.execute(
                "INSERT INTO evidence.evidence_input (evidence_id, ref_type, ref_id, ref_hash) "
                "VALUES (%s,%s,%s,%s)",
                (eid, i.ref_type.value, i.ref_id, i.ref_hash),
            )
        return self.get_evidence(eid), True

    def list_evidence(self, case_id: str) -> list[StoredEvidence]:
        rows = self._c.execute(
            f"SELECT {self._EV_COLS} FROM evidence.evidence_item WHERE case_id = %s "  # noqa: S608
            "ORDER BY created_at, evidence_id",
            (case_id,),
        ).fetchall()
        return [self._evidence_from_row(r) for r in rows]

    # ------------------------------------------------------------------ claims
    def _claim_from_row(self, row: tuple[Any, ...]) -> StoredClaim:
        (cid, case_id, run_id, ak, an, av, structure, text, kind, conf, deps, supersedes,
         created_at, status) = row  # fmt: skip
        cites = tuple(
            Citation(evidence_id=e, support_role=SupportRole(r))
            for e, r in self._c.execute(
                "SELECT evidence_id, support_role FROM evidence.claim_evidence_link "
                "WHERE claim_id = %s ORDER BY evidence_id",
                (cid,),
            ).fetchall()
        )
        draft = ClaimDraft(
            case_id=case_id,
            run_id=run_id,
            author_kind=ak,
            author_name=an,
            author_version=av,
            structure=ClaimStructure(**structure),
            text=text,
            claim_kind=ClaimKind(kind),
            citations=cites,
            self_reported_confidence=SelfReportedConfidence(conf),
            depends_on_er_links=tuple(deps),
            supersedes=supersedes,
        )
        return StoredClaim(
            claim_id=cid,
            created_at=created_at,
            draft=draft,
            status=VerificationStatus(status) if status else None,
        )

    _CL_SQL = (
        "SELECT c.claim_id, c.case_id, c.run_id, c.author_kind, c.author_name, c.author_version, "
        "c.structure, c.text, c.claim_kind, c.self_reported_confidence, c.depends_on_er_links, "
        "c.supersedes, c.created_at, v.status FROM evidence.claim c "
        "LEFT JOIN evidence.claim_current_verification v USING (claim_id) "
    )

    def create_claim(self, draft: ClaimDraft) -> StoredClaim:
        validate_new_claim(draft, self._lookup)
        cid = new_id("clm")
        self._c.execute(
            "INSERT INTO evidence.claim (claim_id, case_id, run_id, author_kind, author_name, "
            "author_version, structure, text, claim_kind, self_reported_confidence, "
            "depends_on_er_links, supersedes, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
            (
                cid, draft.case_id, draft.run_id, draft.author_kind.value, draft.author_name,
                draft.author_version, _jsonb(draft.structure.model_dump(mode="json")), draft.text,
                draft.claim_kind.value, draft.self_reported_confidence.value,
                list(draft.depends_on_er_links), draft.supersedes,
            ),
        )  # fmt: skip
        for c in draft.citations:
            self._c.execute(
                "INSERT INTO evidence.claim_evidence_link (claim_id, evidence_id, support_role) "
                "VALUES (%s,%s,%s)",
                (cid, c.evidence_id, c.support_role.value),
            )
        return self.get_claim(cid)

    def get_claim(self, claim_id: str) -> StoredClaim:
        row = self._c.execute(self._CL_SQL + "WHERE c.claim_id = %s", (claim_id,)).fetchone()
        if row is None:
            raise NotFound(claim_id)
        return self._claim_from_row(row)

    def list_claims(self, case_id: str) -> list[StoredClaim]:
        rows = self._c.execute(
            self._CL_SQL + "WHERE c.case_id = %s ORDER BY c.created_at, c.claim_id", (case_id,)
        ).fetchall()
        return [self._claim_from_row(r) for r in rows]

    # ------------------------------------------------------------ verification
    def record_verification(self, draft: VerificationDraft) -> StoredVerification:
        vid = new_id("ver")
        verifier = {
            "name": draft.verifier_name,
            "version": draft.verifier_version,
            "model_id": draft.model_id,
            "prompt_version": draft.prompt_version,
        }
        self._c.execute(
            "INSERT INTO evidence.verification_result (verification_id, claim_id, status, "
            "checks, rationale, verifier, inputs_seen_hash, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
            (
                vid, draft.claim_id, draft.status.value,
                _jsonb([c.model_dump(mode="json") for c in draft.checks]), draft.rationale,
                _jsonb(verifier), draft.inputs_seen_hash,
            ),
        )  # fmt: skip
        return self._verification(vid)

    def _verification(self, vid: str) -> StoredVerification:
        row = self._c.execute(
            "SELECT verification_id, claim_id, status, checks, rationale, verifier, "
            "inputs_seen_hash, created_at FROM evidence.verification_result "
            "WHERE verification_id = %s",
            (vid,),
        ).fetchone()
        if row is None:
            raise NotFound(vid)
        return self._ver_from_row(row)

    @staticmethod
    def _ver_from_row(row: tuple[Any, ...]) -> StoredVerification:
        vid, claim_id, status, checks, rationale, verifier, seen, created_at = row
        return StoredVerification(
            verification_id=vid,
            created_at=created_at,
            draft=VerificationDraft(
                claim_id=claim_id,
                status=VerificationStatus(status),
                checks=tuple(VerificationCheck(**c) for c in checks),
                rationale=rationale,
                verifier_name=verifier["name"],
                verifier_version=verifier["version"],
                model_id=verifier.get("model_id"),
                prompt_version=verifier.get("prompt_version"),
                inputs_seen_hash=seen,
            ),
        )

    def verifications(self, claim_id: str) -> list[StoredVerification]:
        rows = self._c.execute(
            "SELECT verification_id, claim_id, status, checks, rationale, verifier, "
            "inputs_seen_hash, created_at FROM evidence.verification_result "
            "WHERE claim_id = %s ORDER BY created_at, verification_id",
            (claim_id,),
        ).fetchall()
        return [self._ver_from_row(r) for r in rows]

    # ---------------------------------------------------------- contradictions
    def create_contradiction(self, draft: ContradictionDraft) -> ContradictionView:
        """Idempotent by fingerprint (migration 0021): recording the same finding again returns
        the existing record instead of a duplicate, so a resumed or repeated scan is harmless."""
        fp = fingerprint(draft)
        cid = new_id("ctr")
        row = self._c.execute(
            "INSERT INTO evidence.contradiction (contradiction_id, case_id, type, severity, "
            "detector, explanation_proposal) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (case_id, (detector->>'fingerprint'), severity) "
            "WHERE detector ? 'fingerprint' "
            "DO NOTHING RETURNING contradiction_id",
            (
                cid, draft.case_id, draft.type.value, draft.severity.value,
                _jsonb({"name": draft.detector_name, "version": draft.detector_version,
                        "fingerprint": fp}),
                _jsonb(draft.explanation_proposal) if draft.explanation_proposal else None,
            ),
        ).fetchone()  # fmt: skip
        if row is None:
            existing = self._c.execute(
                "SELECT contradiction_id, status FROM evidence.contradiction_current "
                "WHERE case_id=%s AND contradiction_id = (SELECT contradiction_id FROM "
                "evidence.contradiction WHERE case_id=%s AND detector->>'fingerprint'=%s "
                "AND severity=%s)",
                (draft.case_id, draft.case_id, fp, draft.severity.value),
            ).fetchone()
            if existing is None:  # pragma: no cover - the conflict proves the row exists
                raise NotFound("contradiction")
            return ContradictionView(
                contradiction_id=str(existing[0]),
                case_id=draft.case_id,
                type=draft.type,
                severity=draft.severity,
                status=ContradictionStatus(existing[1]),
                member_ids=tuple(m.member_id for m in draft.members),
            )
        for m in draft.members:
            self._c.execute(
                "INSERT INTO evidence.contradiction_member (contradiction_id, member_type, "
                "member_id, role) VALUES (%s,%s,%s,%s)",
                (cid, m.member_type.value, m.member_id, m.role),
            )
        return ContradictionView(
            contradiction_id=cid,
            case_id=draft.case_id,
            type=draft.type,
            severity=draft.severity,
            status=ContradictionStatus.OPEN,
            member_ids=tuple(m.member_id for m in draft.members),
        )

    def unresolved_contradictions(self, case_id: str) -> list[ContradictionView]:
        rows = self._c.execute(
            "SELECT contradiction_id, case_id, type, severity, status "
            "FROM evidence.contradiction_current WHERE case_id = %s AND status = ANY(%s) "
            "ORDER BY contradiction_id",
            (case_id, [s.value for s in UNRESOLVED_STATUSES]),
        ).fetchall()
        out = []
        for cid, case, typ, sev, status in rows:
            members = self._c.execute(
                "SELECT member_id FROM evidence.contradiction_member WHERE contradiction_id = %s "
                "ORDER BY member_id",
                (cid,),
            ).fetchall()
            out.append(
                ContradictionView(
                    contradiction_id=cid,
                    case_id=case,
                    type=ContradictionType(typ),
                    severity=Severity(sev),
                    status=ContradictionStatus(status),
                    member_ids=tuple(m[0] for m in members),
                )
            )
        return out
