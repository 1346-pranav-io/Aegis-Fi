"""The HTTP API on a real database: identity, RBAC, the closure workflow, async runs, reports,
feedback, human-review decisions and the OpenAPI contract (FR-API-01..03, FR-HITL-02, NFR-SEC-01)."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import jwt
import psycopg
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aegis.api.app import create_app
from aegis.api.auth import JwtVerifier, Role, issue_dev_token
from aegis.api.settings import ApiSettings
from aegis.common.errors import PermissionDenied
from aegis.services import cases as svc
from aegis.services import runs

pytestmark = pytest.mark.integration
SECRET = "unit-test-secret-of-at-least-32-bytes!!"
ISS, AUD = "https://issuer.test", "aegis-api"
NOW = datetime(2022, 9, 10, tzinfo=UTC)
SNAPSHOT = Path(__file__).parent / "snapshots" / "openapi.json"


def settings(app_url: str, approver_url: str, reviewer_url: str) -> ApiSettings:
    return ApiSettings(
        jwt_algorithm="HS256", jwt_key=SECRET, jwt_issuer=ISS, jwt_audience=AUD,
        app_url=app_url, approver_url=approver_url, reviewer_url=reviewer_url,
    )  # fmt: skip


def token(user: str, *roles: Role, **kw: object) -> dict[str, str]:
    t = issue_dev_token(secret=SECRET, user_id=user, roles=roles, issuer=ISS, audience=AUD, **kw)  # type: ignore[arg-type]
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def api(app_url: str, approver_url: str, reviewer_url: str) -> TestClient:
    return TestClient(create_app(settings(app_url, approver_url, reviewer_url)))


def mk_case(app_url: str, acct: str = "A") -> str:
    with psycopg.connect(app_url) as c:
        r = svc.create_alerts(
            c, [svc.ScoredItem(f"t-{acct}", acct, 0.7, ("CYCLE",))],
            signal_type="S", as_of=NOW, model_artefact_id="m",
        )  # fmt: skip
        c.commit()
        return r[0].case_id


ANALYST = lambda: token("alice", Role.ANALYST)  # noqa: E731
APPROVER = lambda: token("boss", Role.APPROVER)  # noqa: E731


# ------------------------------------------------------------------------------ identity
def test_tokens_are_checked_strictly() -> None:
    v = JwtVerifier(algorithm="HS256", key=SECRET, issuer=ISS, audience=AUD)
    good = issue_dev_token(
        secret=SECRET, user_id="u", roles=[Role.ANALYST, Role.AUDITOR], issuer=ISS, audience=AUD
    )
    assert v.verify(good).roles == {Role.ANALYST, Role.AUDITOR}
    other = "another-secret-of-at-least-32-bytes!!!"
    bad = {
        "wrong secret": issue_dev_token(secret=other, user_id="u", roles=[], issuer=ISS, audience=AUD),
        "wrong issuer": issue_dev_token(secret=SECRET, user_id="u", roles=[], issuer="x", audience=AUD),
        "wrong audience": issue_dev_token(secret=SECRET, user_id="u", roles=[], issuer=ISS, audience="x"),
        "expired": issue_dev_token(secret=SECRET, user_id="u", roles=[], issuer=ISS, audience=AUD, ttl_s=-10),
        "alg none": jwt.encode({"sub": "u", "iss": ISS, "aud": AUD, "exp": int(time.time()) + 99}, "", algorithm="none"),
        "no subject": jwt.encode({"iss": ISS, "aud": AUD, "exp": int(time.time()) + 99}, SECRET, algorithm="HS256"),
        "not a token": "abc.def.ghi",
    }  # fmt: skip
    for why, t in bad.items():
        with pytest.raises(PermissionDenied):
            v.verify(t)
        assert why  # (named for the failure message)
    # a role the system does not know cannot be invented by a token
    sneaky = jwt.encode(
        {"sub": "u", "roles": ["analyst", "superuser"], "iss": ISS, "aud": AUD, "exp": int(time.time()) + 99},
        SECRET, algorithm="HS256",
    )  # fmt: skip
    assert v.verify(sneaky).roles == {Role.ANALYST}
    with pytest.raises(ValueError):
        JwtVerifier(algorithm="HS256", key="short", issuer=ISS, audience=AUD)
    with pytest.raises(ValueError):
        JwtVerifier(algorithm="none", key=SECRET, issuer=ISS, audience=AUD)


# ----------------------------------------------------------------------------- RBAC matrix
def _routes(api: TestClient) -> list[tuple[str, str, frozenset[Role]]]:
    out = []
    for r in api.app.routes:  # type: ignore[attr-defined]
        if not isinstance(r, APIRoute) or r.path == "/health":
            continue
        allowed: frozenset[Role] | None = None
        for dep in r.dependant.dependencies:
            allowed = getattr(dep.call, "allowed_roles", allowed)
        assert allowed is not None, f"{r.path} has no role requirement (deny by default)"
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            out.append((method, r.path, allowed))
    return out


def test_every_route_enforces_its_roles(api: TestClient) -> None:
    routes = _routes(api)
    assert len(routes) >= 15
    for method, path, allowed in routes:
        url = path.replace("{case_id}", "case_x").replace("{run_id}", "run_x") \
            .replace("{request_id}", "apr_x").replace("{feedback_id}", "fdb_x") \
            .replace("{contradiction_id}", "ctr_x").replace("{link_id}", "erl_x")  # fmt: skip
        assert api.request(method, url, json={}).status_code == 401, (method, path)
        assert (
            api.request(method, url, json={}, headers={"Authorization": "Bearer junk"}).status_code
            == 401
        )
        for role in Role:
            code = api.request(method, url, json={}, headers=token("someone", role)).status_code
            if role in allowed:
                assert code not in (401, 403), (method, path, role, code)
            else:
                assert code == 403, (method, path, role, code)  # before any body validation
    assert api.get("/health").status_code == 200


def test_openapi_contract_is_stable(api: TestClient) -> None:
    spec = json.dumps(api.get("/openapi.json").json(), indent=1, sort_keys=True)
    if not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(spec, encoding="utf-8")
        pytest.skip("snapshot written for the first time; review and commit it")
    assert spec == SNAPSHOT.read_text(encoding="utf-8"), (
        "the API contract changed; if intended, delete tests/integration/snapshots/openapi.json, "
        "re-run to regenerate, and review the diff"
    )
    # no request body can carry an identity: none of the request schemas has such a field
    names = {
        k.lower()
        for s in json.loads(spec)["components"]["schemas"].values()
        for k in s.get("properties", {})
    }
    assert not names & {"user_id", "decided_by", "requested_by", "roles", "user_role", "actor"}


# -------------------------------------------------------------------------- the workflow
def test_closure_workflow_uses_verified_identities_and_the_database_gate(
    api: TestClient, app_url: str
) -> None:
    case = mk_case(app_url)
    assert case in [q["case_id"] for q in api.get("/queue", headers=ANALYST()).json()]
    d = api.get(f"/cases/{case}", headers=ANALYST()).json()
    assert d["status"] == "OPEN" and d["priority_score"] == 0.7 and d["alerts"] == 1

    r = api.post(
        f"/cases/{case}/transition",
        json={"to": "INVESTIGATING", "reason": "start"},
        headers=ANALYST(),
    )
    assert r.status_code == 204
    assert (
        api.post(
            f"/cases/{case}/transition", json={"to": "OPEN", "reason": "x"}, headers=ANALYST()
        ).status_code
        == 422
    )
    assert (
        api.post(
            f"/cases/{case}/transition",
            json={"to": "PENDING_REVIEW", "reason": "done"},
            headers=ANALYST(),
        ).status_code
        == 204
    )

    # the request is recorded under the TOKEN's subject; a body cannot name someone else
    spoof = api.post(
        f"/cases/{case}/closure-requests",
        json={"justification": "ok", "requested_by": "boss"},
        headers=ANALYST(),
    )
    assert spoof.status_code == 422
    req = api.post(
        f"/cases/{case}/closure-requests",
        json={"justification": "evidence reviewed"},
        headers=ANALYST(),
    )
    assert req.status_code == 201
    request_id = req.json()["request_id"]
    assert [
        p["requested_by"] for p in api.get("/approvals/pending", headers=APPROVER()).json()
    ] == ["alice"]

    # closing without a decision is refused by the database gate
    bad = api.post(
        f"/cases/{case}/close",
        json={"approval_id": "apv_none", "disposition": "NOT_SUSPICIOUS", "reason": "x"},
        headers=ANALYST(),
    )
    assert bad.status_code == 409

    # an analyst cannot decide; the requester cannot approve their own request even as approver
    assert (
        api.post(
            f"/approvals/{request_id}/decision",
            json={"approve": True, "reason": "ok"},
            headers=ANALYST(),
        ).status_code
        == 403
    )
    both = token("alice", Role.ANALYST, Role.APPROVER)
    own = api.post(
        f"/approvals/{request_id}/decision", json={"approve": True, "reason": "ok"}, headers=both
    )
    assert own.status_code == 409 and "cannot decide their own" in own.json()["detail"]

    ok = api.post(
        f"/approvals/{request_id}/decision",
        json={"approve": True, "reason": "agreed"},
        headers=APPROVER(),
    )
    assert ok.status_code == 201
    done = api.post(
        f"/cases/{case}/close",
        json={
            "approval_id": ok.json()["approval_id"],
            "disposition": "NOT_SUSPICIOUS",
            "reason": "closed",
        },
        headers=ANALYST(),
    )
    assert done.status_code == 204
    assert api.get(f"/cases/{case}", headers=ANALYST()).json()["status"] == "CLOSED"

    audit = api.get("/audit", headers=token("ann", Role.AUDITOR)).json()
    actions = {(a["action"], a["actor_id"]) for a in audit}
    assert ("approval.requested", "aegis_app") in actions and (
        "approval.approved",
        "aegis_approver",
    ) in actions


def test_unknown_things_are_404(api: TestClient) -> None:
    assert api.get("/cases/case_nope", headers=ANALYST()).status_code == 404
    assert api.get("/runs/run_nope", headers=ANALYST()).status_code == 404
    assert api.get("/cases/case_nope/report", headers=ANALYST()).status_code == 404


# ------------------------------------------------------------------- async runs and reports
def test_run_is_accepted_at_once_processed_by_a_worker_and_reported(
    api: TestClient, app_url: str
) -> None:
    case = mk_case(app_url, "R")
    acc = api.post(f"/cases/{case}/runs", json={"policy": "fixed_pipeline"}, headers=ANALYST())
    assert acc.status_code == 202 and acc.json()["status"] == "QUEUED"
    run_id = acc.json()["run_id"]
    assert api.get(f"/runs/{run_id}", headers=ANALYST()).json()["status"] == "QUEUED"
    assert (
        api.post(f"/cases/{case}/runs", json={"policy": "reverify"}, headers=ANALYST()).status_code
        == 422
    )
    assert api.get(f"/cases/{case}/report", headers=ANALYST()).status_code == 404  # none yet

    assert (
        runs.work_once(app_url, "worker-1") == run_id
    )  # no model configured: LLM steps are denied
    assert runs.work_once(app_url, "worker-1") is None  # queue empty
    final = api.get(f"/runs/{run_id}", headers=ANALYST()).json()
    assert final["status"] in {"COMPLETE", "PARTIAL"} and final["steps"]

    rep = api.get(f"/cases/{case}/report", headers=ANALYST())
    assert rep.status_code == 200 and rep.json()["version"] >= 1
    md = api.get(f"/cases/{case}/report?format=markdown", headers=ANALYST())
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    for must in ("## Findings", "## Limitations", "## Open contradictions", "not probabilities"):
        assert must in md.text
    assert "PLACEHOLDER" in md.text  # the unverified template is always disclosed


# ----------------------------------------------------------------------- feedback + review
def _claim(app_url: str, case: str) -> str:
    with psycopg.connect(app_url) as c:
        c.execute(
            "INSERT INTO evidence.evidence_item (evidence_id, case_id, evidence_type, content, "
            "summary_text, producer, as_of, content_hash, completeness, schema_version) "
            "VALUES ('ev_api',%s,'TXN_AGGREGATE','{}','s','{\"kind\":\"TOOL\"}',now(),'h-api','FULL',1)",
            (case,),
        )
        c.execute("INSERT INTO evidence.evidence_input VALUES ('ev_api','SOURCE','t','h')")
        c.execute(
            "INSERT INTO evidence.claim (claim_id, case_id, author_kind, author_name, author_version, "
            "structure, text, claim_kind, self_reported_confidence) "
            "VALUES ('clm_api',%s,'AGENT','inv','1','{}','a claim','AGGREGATE','MEDIUM')",
            (case,),
        )
        c.execute("INSERT INTO evidence.claim_evidence_link VALUES ('clm_api','ev_api','SUPPORTS')")
        c.commit()
    return "clm_api"


def test_feedback_review_and_export_gate(api: TestClient, app_url: str) -> None:
    case = mk_case(app_url, "F")
    claim = _claim(app_url, case)
    body = {"case_id": case, "target_type": "CLAIM", "target_id": claim, "action": "ACCEPT"}
    assert (
        api.post("/feedback", json={**body, "user_id": "mallory"}, headers=ANALYST()).status_code
        == 422
    )
    first = api.post("/feedback", json=body, headers=ANALYST())
    assert first.status_code == 201 and first.json()["validation_status"] == "VALIDATED"
    clash = api.post(
        "/feedback",
        json={**body, "action": "REJECT", "reason": "wrong"},
        headers=token("carol", Role.ANALYST),
    )
    assert (
        clash.json()["validation_status"] == "QUARANTINED"
        and "NO_CONFLICTING_VERDICT" in clash.json()["failed_rules"]
    )
    # the analyst cannot do the second review; the approver can (reviewer login)
    sr = {"status": "VALIDATED", "note": "carol is right"}
    assert (
        api.post(
            f"/feedback/{clash.json()['feedback_id']}/second-review", json=sr, headers=ANALYST()
        ).status_code
        == 403
    )
    assert (
        api.post(
            f"/feedback/{clash.json()['feedback_id']}/second-review", json=sr, headers=APPROVER()
        ).status_code
        == 204
    )
    detail = api.get(f"/cases/{case}", headers=ANALYST()).json()
    assert detail["feedback_by_validation"] == {"QUARANTINED": 1, "VALIDATED": 1}
    assert (
        detail["claims"][0]["self_reported_confidence"] == "MEDIUM"
    )  # labelled as such, no number

    # export: model_risk/admin only, and only with an approved FEEDBACK_TO_TRAINING decision
    mr = token("mona", Role.MODEL_RISK)
    assert (
        api.post("/feedback/export", json={"approval_id": "apv_x"}, headers=ANALYST()).status_code
        == 403
    )
    assert (
        api.post("/feedback/export", json={"approval_id": "apv_none"}, headers=mr).status_code
        == 409
    )
    assert (
        api.post(
            "/feedback/export-requests", json={"justification": "train"}, headers=ANALYST()
        ).status_code
        == 403
    )
    req = api.post("/feedback/export-requests", json={"justification": "train"}, headers=mr)
    assert req.status_code == 201 and len(req.json()["manifest_hash"]) == 64
    rid = req.json()["request_id"]
    apv = api.post(
        f"/approvals/{rid}/decision", json={"approve": True, "reason": "ok"}, headers=APPROVER()
    )
    out = api.post("/feedback/export", json={"approval_id": apv.json()["approval_id"]}, headers=mr)
    assert (
        out.status_code == 201 and out.json()["n_records"] == 1 and out.json()["n_simulated"] == 0
    )
    assert "user_id" not in json.dumps(out.json()["records"])
    assert out.json()["manifest_hash"] == req.json()["manifest_hash"]


def test_contradiction_and_link_decisions_need_the_approver_role(
    api: TestClient, app_url: str
) -> None:
    assert (
        api.post(
            "/contradictions/ctr_x/status",
            json={"status": "DISMISSED", "note": "n"},
            headers=ANALYST(),
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/contradictions/ctr_x/status", json={"status": "OPEN", "note": "n"}, headers=APPROVER()
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/contradictions/ctr_none/status",
            json={"status": "DISMISSED", "note": "n"},
            headers=APPROVER(),
        ).status_code
        == 404
    )
    assert (
        api.post(
            "/er-links/erl_x/decision", json={"action": "reject", "reason": "r"}, headers=ANALYST()
        ).status_code
        == 403
    )


def test_rejecting_a_link_over_http_queues_reverification_for_dependent_claims(
    api: TestClient, app_url: str
) -> None:
    from aegis.domain.entities import LinkMethod, LinkStatus
    from aegis.entities import repo
    from aegis.entities.pipeline import LinkProposal

    case = mk_case(app_url, "L")
    with psycopg.connect(app_url) as c:
        m = repo.add_mention(c, source_table="x", source_id="1", raw_name="Corporation #1")
        e = repo.create_entity(c, canonical_name="Corporation #1", created_by="u")
        link = repo.record_proposal(
            c, LinkProposal(m, e, LinkMethod.FUZZY_NAME, 0.9, LinkStatus.PROPOSED, {})
        )
        c.execute(
            "INSERT INTO evidence.evidence_item (evidence_id, case_id, evidence_type, content, "
            "summary_text, producer, as_of, content_hash, completeness, schema_version) "
            "VALUES ('ev_l',%s,'ER_LINK_EVIDENCE',%s,'s','{\"kind\":\"TOOL\",\"name\":\"er\",\"version\":\"1\"}',now(),'h-l','FULL',1)",
            (case, json.dumps({"link_id": link, "merge_ids": []})),
        )
        c.execute("INSERT INTO evidence.evidence_input VALUES ('ev_l','SOURCE','t','h')")
        c.execute(
            "INSERT INTO evidence.claim (claim_id, case_id, author_kind, author_name, "
            "author_version, structure, text, claim_kind, self_reported_confidence) "
            "VALUES ('clm_l',%s,'AGENT','inv','1','{\"subject_ref\":\"A\",\"predicate\":\"p\"}',"
            "'relies on the link','AGGREGATE','NONE')",
            (case,),
        )
        c.execute("INSERT INTO evidence.claim_evidence_link VALUES ('clm_l','ev_l','SUPPORTS')")
        c.commit()
    body = {"action": "reject", "reason": "different companies"}
    assert api.post(f"/er-links/{link}/decision", json=body, headers=ANALYST()).status_code == 403
    out = api.post(f"/er-links/{link}/decision", json=body, headers=APPROVER())
    assert out.status_code == 200
    started = out.json()["reverification_runs"]
    assert set(started) == {case}
    run = api.get(f"/runs/{started[case]}", headers=ANALYST()).json()
    assert run["policy"] == "reverify" and run["status"] == "QUEUED"
    # confirming never queues anything
    again = api.post(
        f"/er-links/{link}/decision",
        json={"action": "confirm", "reason": "after all"},
        headers=APPROVER(),
    )
    assert again.status_code == 200 and again.json()["reverification_runs"] == {}


# ------------------------------------------------------------------------------------ CLI
def test_cli_dev_token_report_and_worker(
    api: TestClient, app_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    from aegis import cli

    monkeypatch.setenv("AEGIS_API_JWT_KEY", SECRET)
    monkeypatch.setenv("AEGIS_API_JWT_ISSUER", ISS)
    monkeypatch.setenv("AEGIS_APP_URL", app_url)
    monkeypatch.setenv("AEGIS_PSEUDONYM_KEY", "cli-test-pseudonym-key-0123456789")
    assert cli.main(["dev-token", "--user", "dave", "--roles", "analyst", "auditor"]) == 0
    tok = capsys.readouterr().out.strip()
    who = JwtVerifier(algorithm="HS256", key=SECRET, issuer=ISS, audience=AUD).verify(tok)
    assert who.user_id == "dave" and who.roles == {Role.ANALYST, Role.AUDITOR}
    assert api.get("/queue", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    with pytest.raises(SystemExit):  # an unknown role is refused by the parser
        cli.main(["dev-token", "--user", "x", "--roles", "superuser"])

    case = mk_case(app_url, "C")
    sub = api.post(
        f"/cases/{case}/runs", json={"policy": "fixed_pipeline"}, headers=ANALYST()
    ).json()
    assert cli.main(["worker", "--once", "--llm", "stub"]) == 0  # drains the queue with the stub
    assert api.get(f"/runs/{sub['run_id']}", headers=ANALYST()).json()["status"] in {
        "COMPLETE",
        "PARTIAL",
    }
    out = tmp_path / "report.md"
    assert cli.main(["report", case, "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "## Findings" in text and "PLACEHOLDER" in text
    capsys.readouterr()  # discard worker logs and the "wrote ..." line
    assert cli.main(["report", case, "--format", "json"]) == 0
    raw = capsys.readouterr().out
    doc = raw[raw.index("{" + chr(10) + '  "') :]  # worker log lines may precede it
    assert json.loads(doc)["case_id"] == case
