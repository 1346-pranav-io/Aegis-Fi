# API and CLI (Phase 5)

Status: DRAFT · Decisions D-15, D-30 · Local use only (no deployment, U-08). The contract is the generated OpenAPI document; a snapshot test (`tests/integration/snapshots/openapi.json`) fails when it changes.

## Run it locally
Needs the database up (`uv run python scripts/dev.py up`, then `migrate`) and these environment variables (no defaults):
`AEGIS_API_JWT_KEY` (HS256 secret, at least 32 bytes; for RS256 a PEM public key plus `AEGIS_API_JWT_ALGORITHM=RS256`), `AEGIS_API_JWT_ISSUER`, optional `AEGIS_API_JWT_AUDIENCE` (default `aegis-api`), `AEGIS_API_APP_URL`, `AEGIS_API_APPROVER_URL`, `AEGIS_API_REVIEWER_URL` (one URL per database login), and for the CLI worker and report `AEGIS_APP_URL`.
```bash
uv run python -m aegis.cli dev-token --user alice --roles analyst        # LOCAL DEV token only
uv run python -m aegis.cli serve --port 8000                              # 127.0.0.1 only
uv run python -m aegis.cli worker --once                                  # process queued runs
uv run python -m aegis.cli report <case_id> --format markdown --out r.md  # read-only export
```
`worker --llm stub` uses the labelled fixture stub (canned outputs, not a model; needs `AEGIS_PSEUDONYM_KEY`). The default is no model: LLM steps end DENIED and runs finish PARTIAL. **Live Claude calls remain disabled (D-29).**

## Identity
Bearer JWT with `sub`, `roles`, `iss`, `aud`, `exp`; algorithm pinned (HS256 or RS256, never `none`); unknown role strings are ignored. The human id recorded everywhere (requester, decider, reviewer, feedback author) is the token subject; **no request body can carry one** (bodies forbid extra fields; the OpenAPI test checks no schema has an identity field).

| Role | May |
|---|---|
| analyst | read queue/cases/runs/reports/feedback; transition cases; submit runs; request closure; close with an approved decision; give feedback |
| approver | everything an analyst may, plus decide approvals, second-review feedback, set contradiction status, confirm/reject/reopen entity links (uses the approver and reviewer database logins) |
| auditor | read endpoints and `/audit` |
| model_risk | export validated feedback (needs an approved FEEDBACK_TO_TRAINING decision) |
| admin | read endpoints, `/audit`, export validated feedback |

## Routes
`GET /health` (public) · `GET /queue` · `GET /cases/{id}` · `POST /cases/{id}/transition` · `POST /cases/{id}/runs` (202, async) · `GET /runs/{id}` · `POST /cases/{id}/closure-requests` · `GET /approvals/pending` · `POST /approvals/{id}/decision` · `POST /cases/{id}/close` · `GET /cases/{id}/report?format=json|markdown` · `POST /feedback` · `POST /feedback/{id}/second-review` · `POST /feedback/export-requests` · `POST /feedback/export` · `POST /contradictions/{id}/status` · `POST /er-links/{id}/decision` · `GET /audit`.

## Guarantees and limits
- Consequential actions only create requests or records; nothing is sent anywhere. Case closure is gated by the database on an APPROVED decision requested after the case entered review, by a different human id than the requester.
- Rejecting or reopening an entity link queues a `reverify` run for each case with a dependent claim; confirming queues nothing.
- Feedback never changes claims, evidence or source. Exports omit user ids and carry a count of SIMULATED items.
- Feedback export: model_risk/admin first calls `/feedback/export-requests`, which returns the manifest hash; an approver decides that request; `/feedback/export` then succeeds only if the approval's subject is that exact hash (migration 0026), so changed feedback needs a new approval.
- Markdown export escapes all untrusted text; feedback `source` cannot be set over the API (always HUMAN); a second review must be by a different human than the author and cannot override structural rule failures; one active user run per case.
- **Limits:**  one API process holds all three database logins, so a compromised process can act as any of them; human ids are verified only as far as the token issuer is trusted; `/docs` and `/openapi.json` are served without authentication; there is no rate limiting; any reader role can read any case.
