# Phase 1 Exit Review

Date: 2026-11-11 · Reviewer: principal architect (+ read-only security-reviewer subagent, second attempt; first attempt failed on a model error) · Verdict (updated after fixes): S-1, S-2, S-3, S-5 fixed and covered by tests; **exit pending independent re-review** and the open items listed under 'After fixes'.

## Exit criteria (IMPLEMENTATION_ROADMAP, Phase 1)
| Criterion | State | Evidence |
|---|---|---|
| T-INV-01 source/audit insert-only | Met, with a caveat (S-8) | 30 integration tests, run on real Postgres 16 |
| T-INV-03 `as_of` leakage (repo layer) | Met | test_as_of.py; as_of must come from the DB clock |
| T-INV-13 idempotent ingestion + quarantine | Met | test_ingest_csv.py |
| T-INV-14 labels unreachable from feature code | Partially met | import-linter rule exists; guards only the (empty) analytics package |
| Ingest fixture + chosen dataset | Met | 5,078,345 txns / 515,088 accounts / 5,177 labels / 0 quarantined, match file profile |
| One-command local start (SAC-07) | Partly met | `scripts/dev.py up` exists; not tested from a clean machine; no API/worker yet |
| Dependency compatibility (TECH_DECISIONS checklist) | Partly met | uv resolved a Python 3.12 plan for all planned packages; nothing installed or imported; LangGraph Postgres checkpointer and Anthropic structured output not exercised |
| Security review, no open CRITICAL/HIGH | **Not met** | 0 critical, 2 HIGH open (below) |
| Measured performance replaces NFR-PERF-01 target | Done, target missed | 5,014 txn/s vs 50k/s target |
| Dataset licence read; pattern file linked | **Not done** | |
Tests at review time: 12 unit + 35 integration pass; ruff, mypy strict, import-linter clean.

## Security findings (subagent report; I checked #1, #3 and #5 against my own code and they are accurate)
| ID | Sev | Finding | Proposed fix |
|---|---|---|---|
| S-1 | HIGH | Ingest runs as the Postgres superuser then `SET ROLE aegis_ingest`; SET ROLE is not a boundary, so the immutability guarantees do not hold on the ingest path | Log in directly as `aegis_ingest` with its own password; superuser used only for migrations; test that the ingest session cannot RESET ROLE / DISABLE TRIGGER |
| S-2 | HIGH | No mechanism sets passwords for `aegis_ingest`/`aegis_app`; `config.py` ships a credential-bearing default | Dev bootstrap reading passwords from env; remove default from config |
| S-3 | MED | `batch_guard` allows any status change (COMPLETE→RUNNING etc.) and rewriting counts; defeats idempotency check; no truncate trigger on ingestion_batch | Allow only RUNNING→COMPLETE/FAILED, terminal afterwards; audit each transition; add truncate trigger |
| S-4 | MED | audit.event actor fields are caller-supplied (forgeable); not tamper-evident; ingest role can read it | Bind actor to session user; hash chain; restrict approval-class actions; drop SELECT for ingest |
| S-5 | MED | Migrator can rewrite populated source tables (0002 was an ALTER TYPE); migrations applied only by initdb on empty volume; no migration tracking | schema_migrations table with checksums; owner approval for migrations touching source tables |
| S-6 | LOW | Quarantine stores raw rows readable by app role; failure path can mask original error | Masked view for app; log exception type; `raise ... from` |
| S-7 | LOW | Concurrent ingest race (no unique constraint on batch identity) | Unique index on (dataset_name, dataset_version, file_hashes) for non-failed batches |
| S-8 | LOW | For roles without UPDATE grant, test failures may come from missing privilege, not the trigger | Tests for DISABLE TRIGGER, RESET ROLE, status regression, audit forgery |
| S-9 | LOW | `.gitignore` lacked `*.csv`, `.env.*`, key patterns | **Done** (added now) |
Reviewer positives: no SQL injection (all statements parameterised), compose port bound to 127.0.0.1, `data/` ignored, no write path to triggers for non-owner roles.
Reviewer could not verify: git history (a local repo exists, nothing committed), running DB behaviour, parts of the code base, lockfile/CI scanning. Two findings (S-2 password auth behaviour, PG defaults) rest on PostgreSQL knowledge, not tests.

## Known weaknesses of this review
- The security review was one pass by a subagent; no human review. No CI exists.
- The as_of test first failed for a reason I did not identify; fixed by using the DB clock.
- Immutability is proven for the schema as created by `0001` in test databases; the dev database got `0002/0003` applied by hand.

## Recommended next actions
1. Owner decision: fix S-1..S-5 now (my recommendation: S-1, S-2, S-3, S-5 now; S-4 hash chain can wait for Phase 3 when approvals exist but actor binding should be done now), then re-run the security review.
2. Read the CDLA-Sharing-1.0 text; link pattern-file lines to transactions (needed before Phase 2 gold labels).
3. Profile ingestion (5k txn/s) only if it blocks work; it is not on the critical path.
4. Phase 2 needs separate owner approval.

## After fixes (2026-11-11, owner approved S-1,S-2,S-3,S-5)
| ID | State | How verified |
|---|---|---|
| S-1 | Fixed | ingest/app roles log in directly; test asserts non-superuser and that DISABLE TRIGGER, session_replication_role, DROP CONSTRAINT/TRIGGER, CREATE TABLE in source all fail; ingestion tests run as `aegis_ingest` |
| S-2 | Fixed | `scripts/migrate.py` sets role passwords from env (dev-only defaults, localhost-bound DB); `Settings.database_url` has no default |
| S-3 | Fixed | 0004 trigger; tests for terminal states, count rewrite, delete/truncate, audit row with acting user |
| S-5 | Fixed (process) | checksummed runner; edit-after-apply detected; unapproved source-touching migration refused (heuristic regex, not a SQL parser); tracking table protected even from owner |
| S-6 | Partial | failure log now includes exception type; raw_record still readable by app role |
| S-4, S-7 | Open | audit actor still caller-supplied (ingest can no longer read audit); batch race not prevented because a unique index would strand batches left RUNNING by a crash (needs a stale-batch recovery design) |
| S-8 | Partial | new tests run as direct logins; still not separating "trigger blocked it" from "privilege missing" for every case |
Known caveat: 0002 (`ALTER COLUMN TYPE` on populated source table) predates the approval rule and is marked `retroactive`, not approved.
Tests: 12 unit + 42 integration pass; ruff, mypy strict, import-linter clean.

## Second round (owner: "Fix both now" — audit forgery and approval gate)
Re-review by the security-reviewer subagent (read-only; it ran no code) found S-2/S-3/S-5/S-8 only partly fixed and raised H-1, M-1, M-2, M-3, L-1, L-2. Actions:
| ID | State | What changed / how verified |
|---|---|---|
| H-1 approval gate | Fixed (as a process control) | `APPROVALS.json` holds per-file entries bound to the file's SHA-256; header comments are ignored; the match list is broad and fail-closed (grants, roles, functions, triggers, search_path, DO/EXECUTE, source./audit., ...). Tests: header-only refused, wrong-hash refused, exact-hash accepted, 5 constructs the old regex missed now refused. Limit: the file is repo-editable, so it is a reviewed record, not a cryptographic control; 0002 is recorded as `retroactive`. |
| M-3 / S-4 audit forgery | Fixed | Migration 0005: `audit.record()` and `batch_guard` are SECURITY DEFINER with a fixed search_path; actor is `session_user`; INSERT and sequence rights on audit.event revoked from app roles. Tests: direct INSERT and `nextval` give InsufficientPrivilege; sanctioned path records the real login role. Limit: definer functions are owned by the (local, superuser) migration role; no hash chain yet, so audit rows are not tamper-evident against the owner. |
| M-1 known dev passwords | Fixed | refused for non-local hosts in `scripts/migrate.py` and in the test fixture |
| M-2 weak tests | Fixed for the trigger cases | owner-run tests assert the trigger's own error text |
| L-1 stuck RUNNING batch | Open (deferred) | not stuck permanently (re-run creates a new batch) but no sweeper recovers crashed ones |
| S-6 / L-2 | Open | quarantine raw rows readable by app role; failure path lacks `raise ... from`; role passwords briefly appear in client-side SQL (visible if `log_statement` is on) |
| S-7 | Open | batch-identity race |
Tests now: 12 unit + 50 integration pass (62); ruff, mypy strict, import-linter clean. Dev DB migrated through 0005.
Still not done for Phase 1 exit: dataset licence text, pattern-file linking, clean-machine start test, no human security review, no CI.

## Owner declaration
On 2026-11-11 the owner declared Phase 1 done, accepting the carried-forward gaps (clean-machine start, human security review, CI, L-1, S-6/L-2, S-7, no audit hash chain, 1,968 unexplained non-pattern laundering rows, 5k txn/s ingest speed, dependencies resolved but not imported). These are inherited by later phases and must be re-listed at the Phase 2 entry review.
