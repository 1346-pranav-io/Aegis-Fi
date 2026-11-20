# Reconciliation of Phase 3 and Phase 4

Date: 2026-11-12 · Both streams were merged into `main` (commits `bae1c7f`, `2f0bc03`); this document records the integration work done afterwards against `docs/PHASE3_4_PLAN.md` section 5. The reconciliation changes are **uncommitted** in the working tree at the time of writing.

## Status of the plan's checklist
| # | Item | State |
|---|---|---|
| 1 | Replace Phase 3's fake `ERLinkReader` with Phase 4's | **Partly.** `PgERLinkReader` gained `entity_roots()` and is what the runtime tools call when the Postgres services are wired; the in-memory runs still use `FakeERLinkReader`. The Postgres wiring (`runtime/pg_services.py`) was **not** changed to construct `PgERLinkReader`: that is still to do |
| 2 | Register the Contradiction capability in the runtime | **Done.** `runtime/wiring.default_registry` registers it; it now runs as step 6 of 7 in every standard run (in-memory and on Postgres). Three new read-only tools (`list_case_evidence`, `list_case_claims`, `list_case_er_links`) give it the shapes it parses; only the Contradiction agent may use them; they refuse another case or a later `as_of` |
| 3 | Wire `ExplanationProposer` to the real gateway | **Not done.** The capability still takes an optional proposer and none is set, so no explanation proposals are produced in runs. Needs a prompt, a schema and a decision on pseudonymisation of contradiction text |
| 4 | Re-verification when a link is rejected (C6) | **Done at the service level.** `services/reverify.py` finds the claims that rely on changed links (declared dependencies or cited `ER_LINK_EVIDENCE`) and queues a `reverify` run (new policy: Verification, Contradiction scan, Reporting; no specialists). Tested in memory. **Not wired** to the ER operations: nothing calls it automatically when a link is rejected or a merge undone |
| 5 | Fresh-database migration check, security re-review | **Migrations 0001-0022 apply on a fresh database** (every integration test builds one). Independent security review done (one subagent pass, read-only, ran no code): see the section below |
| 6 | Import contracts incl. capability independence | **Done**: the "capabilities never import each other" contract now checks real submodules and is kept |
| 7 | Joint exit review | **Not done** |
| - | Checkpoint tables outside the migration runner (P-2) | **Open decision.** Unchanged |
| - | E2/E3 label clash | **Done**: notes added to the two research files; Phase 3's experiments are G1/V1/O1 |

## Seams that did not fit, and what was changed
- **Task type and tool names.** The runtime scheduled the agent as `scan`; the capability expected `detect_contradictions`, and called `get_evidence/get_claims/get_er_links` with different arguments and result shapes than the runtime's tools of the same name. Capability now uses type `scan` and the three new `list_case_*` tools.
- **Duplicate findings.** The Verification agent (Phase 3) and the Contradiction detector (Phase 4) both produced `CLAIM_VS_EVIDENCE` records, with different severity (MEDIUM vs HIGH), detector names and member sets, so a refuted claim gave two findings. The rule now lives in one function (`analytics.contradictions.claim_vs_evidence_findings`) used by both, so the records are identical and, since both evidence stores now record contradictions idempotently by fingerprint (migration 0021's unique index in Postgres, a dictionary in memory), only one is kept. Refuted claims therefore produce a **HIGH** finding, which blocks case closure (0022).
- **Silent mismatch of evidence content (R-4).** The detectors read `facts`/`events` inside `evidence.content` and nothing produced them. New `domain/evidence_content.py` declares the shape; the Transaction agent now emits well-formed facts (counts and per-currency totals with a time scope) and events; the scan reports `PARTIAL` with error `MALFORMED_EVIDENCE_CONTENT` when an item breaks the declaration instead of quietly finding nothing. A test shows a second source stating a different total is found as `AMOUNT_MISMATCH` inside a run.
- **ER evidence had no producer.** `entities/evidence.er_link_evidence` builds `ER_LINK_EVIDENCE` (link, mention, entity, method, status, similarity score as a string, merge ids). Its input is a self-certifying query reference, so it relies on open decision **P-3**.
- **Status service moved** from `capabilities/contradiction/` to `services/contradictions.py`.
- `erm` id prefix added to `common/ids.py` (Phase 4's request R-1).

## Measured state of the combined tree
386 unit tests and 104 integration tests pass, plus 1 expected skip (a Phase 3 test of the unapproved-migration state, which skips itself now that 0010/0011 are approved); ruff, mypy strict (105 files) and 5 import contracts are clean. These are test counts, not evidence about detection quality. Nothing has been run against a real model, and no LLM call leaves the machine.

## Still open (needs the owner or later work)
- Wire `PgERLinkReader` into the Postgres runtime services and call `reverify` from the ER operations (reject, reopen, supersede, unmerge).
- Explanation proposals (item 3).
- Decisions **P-1** (content-addressed evidence ids, already in use), **P-2** (checkpoint tables), **P-3** (query references as provenance, now also used by ER evidence), **P-4** (adaptive policy with a tiny step budget), **P-5** (automatic filing-approval request).
- Dev database still at migration 0009.
- Phase 4's experiment E5 was inconclusive; the closure gate does not check the approver role of the person who dismisses a HIGH finding; accounts file not in the database.
- Joint Phase 3/4 exit review (T-INV-02, 04-07, 10-12, 15-18).

## Independent security review of the merged code (one subagent pass; read-only, ran nothing)
**0022 (`case_guard` replacement): passed.** Every guard from 0008 survived (legal transitions, approval bound to the case and to a request made after the latest PENDING_REVIEW, finite and monotonic priority, `last_alert_at` bound, reason cap, audit rows, frozen CLOSED rows, fixed `search_path`).

| ID | Sev | Finding | State |
|---|---|---|---|
| H-1 | HIGH | `aegis_app` can write `contradiction_status_event` rows with `actor_kind='HUMAN'` itself (the kind is caller-asserted), so it can dismiss a HIGH finding and pass the closure gate with no human; it can also insert a contradiction with a chosen fingerprint and lower severity, which makes the unique index silently drop the real HIGH finding | **Open - needs your decision.** Same class as the Phase 2 approval finding. Proposed: a dedicated human-review login that is the only holder of INSERT on status events (as for approvals), and contradiction inserts only through a definer function that recomputes fingerprint and severity |
| H-2 | HIGH | `aegis_app` can create CONFIRMED ER links with no approver (`MANUAL` + asserted `HUMAN`), or mint a mention and an entity with equal identifiers to get an auto-confirmed EXACT_ID link; UNMERGE needs no approval | **Open - needs your decision**, same proposal: human decisions through a reviewer login/definer function, mention identifiers validated against source data |
| M-1 | MED | Pseudonymisation relies on values the calling agent registers plus a regex; entity/organisation names and document text are not registered by any capability; `sensitive` is chosen by the agent | Open. No live provider exists, so nothing leaves the machine today; this must be fixed before Q-02 is confirmed or any live call |
| M-2 | MED | LangGraph checkpoint tables are created outside the migration runner and `aegis_app` may update/delete them (resume state could be altered) | Open, = decision **P-2** |
| M-3 | MED | Contradictions returned by a capability were not checked against the task's case | **Fixed**: the engine rejects them (`OUTPUT_REJECTED`), tested |
| M-4 | MED | Claim dependencies on ER links came only from agent-declared ids | **Fixed**: also derived from cited `ER_LINK_EVIDENCE` (`evidence/er_deps.py`), tested |
| L-1 | LOW | job `finish()` does not check the worker or lease; no lease heartbeat | Open |
| L-2 | LOW | replay store is an unsigned file; poisoning needs file write access | Open |
| L-3 | LOW | prompt-injection handling reviewed (fenced data, escaped delimiters, schema validation, allow-lists outside the model); `aegis_app` can still create approval requests directly | Noted |
Not verified by the reviewer: any API/RBAC (none exists yet), dependency scanning, a secrets scan, the tests and the perturbation generator.

## Round 2 - owner said "Yes, build it": human-review login (migration 0023)
Built: login role `aegis_reviewer`; contradiction status events and human entity-link decisions (and undoing a merge) can only be written through it, checked in the database with `session_user`, not by a claimed actor kind; the unique index on findings now includes severity; the stored fingerprint is recomputed in SQL at commit and must match the finding's type, detector and members; a mention may only state an identifier that source data confirms (today: an account's stored reference) and an entity may only carry identifiers its founding mention carries; `case_guard` replaced again so `last_alert_at` is only checked when it changes. The password for the new role is set by `scripts/migrate.py` like the others (required argument, dev default for local hosts only). 386 unit + 113 integration tests pass (1 expected skip); ruff, mypy and import contracts clean.

Independent re-review of 0023 (one subagent pass, read-only, ran nothing):
| Finding | Verdict / state |
|---|---|
| H-1 (fake human dismissal, fingerprint pre-emption) | **Fixed** for the described attack; the status trigger is proven even with widened grants. Case guard compared line by line with 0022/0008: only the intended `last_alert_at` change |
| H-2 (forged confirmed links, undo merge, fabricated identifiers) | **Mostly fixed.** Gaps found by the re-review and handled below |
| M-1 the app could re-confirm a link a human rejected, or supersede a human-confirmed link | **Fixed**: only a human may leave REJECTED or change a link a human confirmed (the pipeline may still supersede a PROPOSED link); tested |
| M-2 an auto-confirmed EXACT_ID link says nothing about the NAME: the app chooses the mention and entity names, so a real account can be linked to an entity called "Bank X" | **Open (documented).** The link asserts that identifiers are equal and verified, not that the name is right; names are unverified free text. Needs a design decision (derive names from source, or require human confirmation for EXACT_ID too) |
| M-3 a contradiction without a fingerprint key bypasses the index and the check; a forged record with a valid fingerprint and the same severity wins over the real one, which loses its member roles and explanation | **Open.** Impact: duplicates, a fake HIGH finding blocks closing until a human dismisses it (a nuisance, not a bypass), metadata of a pre-empted real finding is lost. Fix would be to require the key and to write findings only through a definer function |
| M-4 first writer of a mention wins its name and attributes (idempotent insert) | **Open** |
| L-1 members can be added to a finding in a later transaction (fingerprint becomes stale) | Open |
| L-2 `entity_id` stays in the exact-identifier allow-list but can no longer be verified | Noted: dead until the IBM accounts file is ingested |
| L-3 one shared reviewer login; human ids are text the reviewer login records | Open (identity phase); an `IndexError` in `scripts/migrate.py` for URLs without `@` is fixed |
Not verified by the reviewer: default privileges, `pg_hba` authentication, live behaviour.
