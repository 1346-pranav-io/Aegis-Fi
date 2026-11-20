"""Postgres evidence store against a real scratch database: same contract as the in-memory store,
plus real SOURCE hashes (T-INV-04) and the database-level rules."""

from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from p3_builders import claim_draft, ev_draft

from aegis.common.errors import AegisValidationError
from aegis.domain.evidence import (
    ContradictionDraft,
    ContradictionMember,
    ContradictionType,
    EvidenceInput,
    EvidenceType,
    InputRefType,
    MemberType,
    Severity,
    VerificationStatus,
)
from aegis.evidence.pg_store import PostgresEvidenceStore, PostgresLeafHashes
from aegis.evidence.provenance import walk
from aegis.ingestion.adapters import CsvMapping
from aegis.ingestion.writer import ingest_csv
from aegis.services import cases as svc

pytestmark = pytest.mark.integration

MAPPING = CsvMapping(
    adapter_name="t", adapter_version="1", ref_column="id", time_column="ts",
    time_format="%Y-%m-%d %H:%M", origin_columns=("o",), destination_columns=("d",),
    amount_column="a", fixed_currency="USD",
)  # fmt: skip


def _setup(
    ingest_url: str, app_url: str, tmp_path: Path
) -> tuple[psycopg.Connection, str, list[str]]:
    f = tmp_path / "a.csv"
    f.write_text("id,ts,o,d,a\nt1,2022-09-10 10:00,A,B,5\nt2,2022-09-10 11:00,B,C,6\n")
    with psycopg.connect(ingest_url) as ic:
        ingest_csv(ic, f, MAPPING, "d1", "v1")
        txn_ids = [r[0] for r in ic.execute("SELECT txn_id FROM source.transaction ORDER BY 1")]
    c = psycopg.connect(app_url)
    r = svc.create_alerts(
        c, [svc.ScoredItem("t1", "A", 0.5, ("X",))], signal_type="S",
        as_of=datetime(2022, 9, 10, tzinfo=UTC), model_artefact_id="m",
    )  # fmt: skip
    c.commit()
    return c, r[0].case_id, txn_ids


def test_postgres_store_end_to_end(ingest_url: str, app_url: str, tmp_path: Path) -> None:
    c, case, txns = _setup(ingest_url, app_url, tmp_path)
    leaves = PostgresLeafHashes(c)
    s = PostgresEvidenceStore(c, leaves)
    inputs = []
    for t in txns:
        h = leaves.hash_of(InputRefType.SOURCE, t)
        assert h
        inputs.append(EvidenceInput(ref_type=InputRefType.SOURCE, ref_id=t, ref_hash=h))
    e1, new1 = s.create_evidence(ev_draft(case, tuple(inputs), content={"n": "2"}))
    e1b, new2 = s.create_evidence(ev_draft(case, tuple(reversed(inputs)), content={"n": "2"}))
    c.commit()
    assert new1 and not new2 and e1.evidence_id == e1b.evidence_id
    assert e1.content_hash == e1.draft.content_hash()  # round trip through jsonb keeps the hash

    link = EvidenceInput(
        ref_type=InputRefType.EVIDENCE, ref_id=e1.evidence_id, ref_hash=e1.content_hash
    )
    e2, _ = s.create_evidence(
        ev_draft(case, (link,), content={"cycles": "0"}, etype=EvidenceType.GRAPH_CYCLES)
    )
    cl = s.create_claim(claim_draft(case, [e2.evidence_id]))
    c.commit()
    rep = walk(s, leaves, cl.claim_id)
    assert rep.ok and {n.node_id for n in rep.leaves} == set(txns)
    assert s.get_claim(cl.claim_id).status is None
    assert [x.claim_id for x in s.list_claims(case)] == [cl.claim_id]

    forged = EvidenceInput(ref_type=InputRefType.SOURCE, ref_id=txns[0], ref_hash="forged")
    with pytest.raises(AegisValidationError):
        s.create_evidence(ev_draft(case, (forged,), content={"n": "9"}))
    with pytest.raises(AegisValidationError):
        s.create_claim(claim_draft(case, ["ev_nope"]))
    c.rollback()

    from aegis.domain.evidence import (
        CheckKind,
        CheckOutcome,
        VerificationCheck,
        VerificationDraft,
    )  # fmt: skip

    chk = (
        VerificationCheck(check_id="X", kind=CheckKind.DETERMINISTIC, outcome=CheckOutcome.PASS),
    )
    for st in (VerificationStatus.UNVERIFIABLE, VerificationStatus.VERIFIED):
        s.record_verification(VerificationDraft(
            claim_id=cl.claim_id, status=st, checks=chk, rationale="r",
            verifier_name="v", verifier_version="1", inputs_seen_hash="h"))  # fmt: skip
    c.commit()
    assert s.get_claim(cl.claim_id).status is VerificationStatus.VERIFIED
    assert [v.draft.status for v in s.verifications(cl.claim_id)] == [
        VerificationStatus.UNVERIFIABLE, VerificationStatus.VERIFIED]  # fmt: skip

    ctr = s.create_contradiction(ContradictionDraft(
        case_id=case, type=ContradictionType.AMOUNT_MISMATCH, severity=Severity.HIGH,
        detector_name="d", detector_version="1",
        members=(ContradictionMember(member_type=MemberType.EVIDENCE, member_id=e1.evidence_id),
                 ContradictionMember(member_type=MemberType.EVIDENCE, member_id=e2.evidence_id)),
    ))  # fmt: skip
    c.commit()
    un = s.unresolved_contradictions(case)
    assert [u.contradiction_id for u in un] == [ctr.contradiction_id]
    assert set(un[0].member_ids) == {e1.evidence_id, e2.evidence_id}
    c.close()


def test_database_still_enforces_rules_behind_the_service(
    ingest_url: str, app_url: str, tmp_path: Path
) -> None:
    c, case, txns = _setup(ingest_url, app_url, tmp_path)
    with pytest.raises(psycopg.errors.RaiseException, match="at least one input"):
        c.execute(
            "INSERT INTO evidence.evidence_item (evidence_id, case_id, evidence_type, content, "
            "summary_text, producer, as_of, content_hash, completeness, schema_version) "
            "VALUES ('ev_x',%s,'SIGNAL','{}','s','{\"kind\":\"TOOL\"}',now(),'h','FULL',1)",
            (case,),
        )
        c.commit()
    c.rollback()
    c.close()
