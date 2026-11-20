# Phase 5: exit review (API, HITL feedback, report export, CLI)

Date: 2026-11-12 · Author: principal architect · Inputs: D-30, `docs/API.md`, one read-only security review pass (subagent, ran no code), the test suite. **Verdict: scope delivered and tested locally. Declared DONE by the owner on 2026-11-12, accepting the open items in section 4.**

## 1. Scope delivered (D-30)
| Item | State |
|---|---|
| FastAPI app, bearer-JWT identity, RBAC (analyst, approver, model_risk, auditor, admin) | Done. Human id is the token subject; no request body can carry an id (OpenAPI test) |
| Async runs + worker (`aegis worker`) | Done. 202 on submit, status endpoint, one active user run per case |
| Approvals, closure requests, closure over HTTP | Done. Closure still gated by the database on an approved decision by a different human |
| Contradiction status and entity-link decisions | Done, via the reviewer login; reject/reopen queue `reverify` runs and report queuing failures |
| Feedback capture, rule validation, second review, export | Done (migrations 0025, 0026). Export needs an approved FEEDBACK_TO_TRAINING decision naming the exact manifest hash |
| Report export JSON and Markdown | Done. Markdown escapes untrusted text, always prints the placeholder-template notice, limitations and open contradictions |
| CLI (`dev-token`, `serve`, `worker`, `report`) | Done. `serve` binds 127.0.0.1 |
| Out of scope, not built | Web UI (Q-11), retraining from feedback / model activation, PDF, automatic filing |

## 2. Tests and checks
395 unit + 133 integration pass, 1 expected skip (Phase 3 migration approval test); ruff, mypy strict (119 files) and 5 import contracts clean. Phase 5 tests cover: token strictness, an RBAC matrix over every route, OpenAPI snapshot, closure workflow, async run + worker + report, feedback validation/review/export gate (including the hash binding), role checks on human-review routes, link rejection queuing re-verification, the CLI, and Markdown escaping. Coverage was not re-measured for this phase. Dev database is migrated through 0026; IBM data intact.

## 3. Security
One read-only reviewer pass (not a human review, nothing run). Findings fixed: JWT algorithm-confusion and claim handling, unbounded request bodies and budgets, client-settable feedback `source`, self second-review, second review overriding structural failures, Markdown injection, missing security headers, unhandled FK/unique violations, link-decision partial failure, and the export approval not being bound to its contents (H-2, migration 0026). The fixes have **not** been re-reviewed by an independent pass.

## 4. Open items (accepted risk unless the owner says otherwise)
1. One API process holds all three database logins; a compromised process can act as any of them.
2. Human ids are only as trustworthy as the token issuer. HS256 dev tokens are for local use only.
3. No rate limiting; `/docs` and `/openapi.json` are unauthenticated; any reader role can read any case and reads are not audited.
4. Feedback poisoning: an opposite verdict by another user quarantines earlier feedback until a second review.
5. Link decision then re-verification queuing is not atomic (an outbox was deferred); failures are reported in the response, not retried.
6. Hash derivation for the export is the application's; the database checks the binding only.
7. Live Claude calls remain disabled (D-29); the worker's `--llm stub` is canned output, not a model. Nothing here is evidence about model behaviour.
8. Migrations 0025 and 0026 were approved by approach, not line by line.
9. No end-to-end run against a real identity provider; local Docker only (no deployment).

## 5. Recommendation
Optionally run a second independent security pass on the fixes first. Otherwise Phase 5 meets D-30. Phase 6 is not authorised.
