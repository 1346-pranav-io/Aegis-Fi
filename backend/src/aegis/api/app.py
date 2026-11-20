"""The FastAPI application (FR-API-01..03).

Every route except /health depends on `require(...)` (deny by default; the RBAC matrix test walks
every route). Identity comes only from the verified token: `principal.user_id` is what gets
recorded as requester, decider, reviewer or feedback author; request bodies cannot carry one.
Domain errors map to HTTP: PermissionDenied 403, NotFound 404, ConflictError and IntegrityError 409,
AegisValidationError 422.
"""

from typing import Any, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from psycopg import errors as pgerr

from aegis.api import schemas as S
from aegis.api.auth import READERS, WORKERS, JwtVerifier, Principal, Role, require
from aegis.api.db import Databases
from aegis.api.settings import ApiSettings
from aegis.common.errors import (
    AegisError,
    AegisValidationError,
    ConflictError,
    IntegrityError,
    NotFound,
    PermissionDenied,
)
from aegis.domain.agents import Budget
from aegis.domain.cases import ApprovalAction, CaseStatus
from aegis.domain.evidence import ContradictionStatus
from aegis.domain.feedback import FeedbackIn, ValidationStatus
from aegis.entities import repo as er
from aegis.runtime.pg_wiring import build_postgres_services
from aegis.services import case_views, cases, contradictions, feedback, runs
from aegis.services.report_export import report_to_markdown
from aegis.services.reverify import claims_depending_on

TITLE = "Aegis-FI API"


def create_app(settings: ApiSettings) -> FastAPI:
    app = FastAPI(title=TITLE, version="0.1.0")
    app.state.verifier = JwtVerifier(
        algorithm=settings.jwt_algorithm, key=settings.jwt_key,
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )  # fmt: skip
    db = Databases(settings)
    app.state.db = db

    # ---------------------------------------------------------------- error mapping
    @app.exception_handler(AegisError)
    async def _domain_error(_r: Request, exc: AegisError) -> JSONResponse:
        code = {
            PermissionDenied: 403, NotFound: 404, ConflictError: 409, IntegrityError: 409,
            AegisValidationError: 422,
        }.get(type(exc), 400)  # fmt: skip
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    @app.exception_handler(pgerr.RaiseException)
    async def _rule_refused(_r: Request, exc: pgerr.RaiseException) -> JSONResponse:
        # a database rule refused the change; show the rule's message, not internals
        return JSONResponse(status_code=409, content={"detail": str(exc).splitlines()[0]})

    @app.exception_handler(pgerr.InsufficientPrivilege)
    async def _no_privilege(_r: Request, _e: pgerr.InsufficientPrivilege) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "not permitted"})

    @app.exception_handler(pgerr.ForeignKeyViolation)
    async def _bad_reference(_r: Request, _e: pgerr.ForeignKeyViolation) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "referenced object not found"})

    @app.exception_handler(pgerr.UniqueViolation)
    async def _duplicate(_r: Request, _e: pgerr.UniqueViolation) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "already recorded"})

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # ---------------------------------------------------------------- queue and cases
    @app.get("/queue", response_model=list[S.QueueItem])
    def queue(
        limit: int = Query(100, ge=1, le=500),
        _p: Principal = Depends(require(*READERS)),
    ) -> list[S.QueueItem]:
        with db.connect() as c:
            return [
                S.QueueItem(case_id=i, priority_score=s, status=st)
                for i, s, st in cases.queue(c, limit)
            ]

    @app.get("/cases/{case_id}")
    def get_case(case_id: str, _p: Principal = Depends(require(*READERS))) -> dict[str, Any]:
        with db.connect() as c:
            return case_views.case_detail(c, case_id)

    @app.post("/cases/{case_id}/transition", status_code=204)
    def transition(
        case_id: str,
        body: S.TransitionIn,
        _p: Principal = Depends(require(*WORKERS)),
    ) -> None:
        with db.connect() as c:
            cases.transition(c, case_id, CaseStatus(body.to), body.reason)

    # ---------------------------------------------------------------- runs (async)
    @app.post("/cases/{case_id}/runs", status_code=202, response_model=S.RunAccepted)
    def submit_run(
        case_id: str,
        body: S.RunIn,
        _p: Principal = Depends(require(*WORKERS)),
    ) -> S.RunAccepted:
        budget = Budget(**body.budget.model_dump()) if body.budget else None
        with db.connect(autocommit=True) as c:
            sub = runs.submit_run(c, case_id, body.policy, budget)
        return S.RunAccepted(run_id=sub.run_id, job_id=sub.job_id)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, _p: Principal = Depends(require(*READERS))) -> dict[str, Any]:
        with db.connect() as c:
            return runs.run_status(c, run_id)

    # ---------------------------------------------------------------- approvals and closure
    @app.post("/cases/{case_id}/closure-requests", status_code=201, response_model=S.RequestCreated)
    def request_closure(
        case_id: str,
        body: S.ClosureRequestIn,
        p: Principal = Depends(require(*WORKERS)),
    ) -> S.RequestCreated:
        with db.connect() as c:
            case_views.case_detail(c, case_id)  # 404 for an unknown case
            rid = cases.request_approval(
                c, ApprovalAction.CASE_CLOSURE, case_id, p.user_id, body.justification
            )
        return S.RequestCreated(request_id=rid)

    @app.get("/approvals/pending")
    def pending(
        _p: Principal = Depends(require(Role.APPROVER, Role.AUDITOR, Role.ADMIN)),
    ) -> list[dict[str, Any]]:
        with db.connect() as c:
            return case_views.pending_approvals(c)

    @app.post("/approvals/{request_id}/decision", status_code=201, response_model=S.DecisionOut)
    def decide(
        request_id: str,
        body: S.DecisionIn,
        p: Principal = Depends(require(Role.APPROVER)),
    ) -> S.DecisionOut:
        # decided_by is the verified subject; the database also refuses requester == decider
        with db.connect("approver") as c:
            aid = cases.decide_approval(c, request_id, body.approve, p.user_id, body.reason)
        return S.DecisionOut(approval_id=aid)

    @app.post("/cases/{case_id}/close", status_code=204)
    def close(
        case_id: str,
        body: S.CloseIn,
        _p: Principal = Depends(require(*WORKERS)),
    ) -> None:
        with db.connect() as c:
            cases.close_case(c, case_id, body.disposition, body.approval_id, body.reason)

    # ---------------------------------------------------------------- reports
    @app.get("/cases/{case_id}/report")
    def report(
        case_id: str,
        format: Literal["json", "markdown"] = "json",
        _p: Principal = Depends(require(*READERS)),
    ) -> Any:
        with db.connect() as c:
            rep = case_views.latest_report(c, case_id)
        if format == "markdown":
            return PlainTextResponse(report_to_markdown(rep), media_type="text/markdown")
        rep["created_note"] = "export only; nothing is submitted anywhere"
        return JSONResponse(content=_jsonable(rep))

    # ---------------------------------------------------------------- feedback
    @app.post("/feedback", status_code=201, response_model=S.FeedbackOut)
    def give_feedback(
        body: S.FeedbackBody, p: Principal = Depends(require(*WORKERS))
    ) -> S.FeedbackOut:
        role = "approver" if Role.APPROVER in p.roles else "analyst"
        with db.connect() as c:
            fid, status, results = feedback.submit_feedback(
                c, FeedbackIn(**body.model_dump()), user_id=p.user_id, user_role=role
            )
        return S.FeedbackOut(
            feedback_id=fid, validation_status=status,
            failed_rules=[r.rule for r in results if not r.passed],
        )  # fmt: skip

    @app.post("/feedback/{feedback_id}/second-review", status_code=204)
    def second_review(
        feedback_id: str,
        body: S.SecondReviewIn,
        p: Principal = Depends(require(Role.APPROVER)),
    ) -> None:
        with db.connect("reviewer") as c:
            feedback.second_review(
                c, feedback_id, ValidationStatus(body.status), p.user_id, body.note
            )

    @app.post("/feedback/export-requests", status_code=201, response_model=S.ExportRequestOut)
    def request_feedback_export(
        body: S.ExportRequestIn,
        p: Principal = Depends(require(Role.MODEL_RISK, Role.ADMIN)),
    ) -> dict[str, Any]:
        with db.connect(autocommit=True) as c:
            return feedback.request_export_approval(c, p.user_id, body.justification, body.case_id)

    @app.post("/feedback/export", status_code=201, response_model=S.ExportOut)
    def export_feedback(
        body: S.ExportIn,
        p: Principal = Depends(require(Role.MODEL_RISK, Role.ADMIN)),
    ) -> dict[str, Any]:
        with db.connect() as c:
            return feedback.export_validated(c, body.approval_id, p.user_id, body.case_id)

    # ---------------------------------------------------------------- human review decisions
    @app.post("/contradictions/{contradiction_id}/status", status_code=204)
    def contradiction_status(
        contradiction_id: str, body: S.ContradictionStatusIn,
        p: Principal = Depends(require(Role.APPROVER)),
    ) -> None:  # fmt: skip
        if body.status is ContradictionStatus.OPEN:
            raise HTTPException(
                422, "a human decision moves a finding to EXPLAINED, RESOLVED or DISMISSED"
            )
        with db.connect("reviewer") as c:
            contradictions.set_status(
                c,
                contradiction_id,
                body.status,
                actor=p.user_id,
                actor_kind="HUMAN",
                note=body.note,
            )

    @app.post("/er-links/{link_id}/decision", response_model=S.LinkDecisionOut)
    def link_decision(
        link_id: str,
        body: S.LinkDecisionIn,
        p: Principal = Depends(require(Role.APPROVER)),
    ) -> S.LinkDecisionOut:
        change = {"confirm": er.confirm_link, "reject": er.reject_link, "reopen": er.reopen_link}
        with db.connect("reviewer") as rc:
            change[body.action](rc, link_id, p.user_id, body.reason)
        # The decision above is already committed. Queuing re-verification is a separate step: a
        # failure here must not turn a recorded decision into an error response.
        started: dict[str, str] = {}
        failed: list[str] = []
        if body.action != "confirm":  # C6: claims that relied on the link are re-verified
            try:
                with db.connect(autocommit=True) as c:
                    flagged = er.PgERLinkReader(c).flagged_claims()
                    cases = sorted({f.case_id for f in flagged if link_id in f.unconfirmed})
                    for case_id in cases:
                        try:
                            ids = claims_depending_on(
                                build_postgres_services(c).backend.evidence, case_id, {link_id}
                            )
                            if ids:
                                sub = runs.submit_run(
                                    c, case_id, "reverify", allow_system_policy=True,
                                    reverify_claim_ids=ids,
                                )  # fmt: skip
                                started[case_id] = sub.run_id
                        except (AegisError, psycopg.Error):
                            failed.append(case_id)
            except (AegisError, psycopg.Error):
                failed.append("*")
        return S.LinkDecisionOut(
            link_id=link_id, action=body.action, reverification_runs=started,
            reverification_failed_cases=failed,
        )  # fmt: skip

    # ---------------------------------------------------------------- audit
    @app.get("/audit", response_model=list[S.AuditEvent])
    def audit(
        limit: int = Query(100, ge=1, le=500),
        _p: Principal = Depends(require(Role.AUDITOR, Role.ADMIN)),
    ) -> list[dict[str, Any]]:
        with db.connect() as c:
            return case_views.recent_audit(c, limit)

    return app


def _jsonable(obj: Any) -> Any:
    import json

    return json.loads(json.dumps(obj, default=str))
