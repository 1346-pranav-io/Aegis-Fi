# Phase 2 Exit Review

Date: 2026-11-11 · Reviewer: principal architect (+ read-only security-reviewer subagent, one pass, ran no code) · Verdict: **NOT READY TO EXIT.** The standards require a security review with no open CRITICAL/HIGH finding; one HIGH is open (H-1) and needs an owner decision. Functional deliverables are otherwise largely met, with gaps listed below.

## 1. Roadmap Phase 2 deliverables
| Deliverable (IMPLEMENTATION_ROADMAP) | State | Evidence |
|---|---|---|
| `analytics`: transaction, graph, behaviour modules | Built | `features.py` (23 point-in-time features), `graph.py`, `behaviour.py`; "transaction" analytics = feature set plus rules, no separate agent-shaped module yet |
| Typology rule packs v1 | Built | `rules.py` (v1) and `rules_v2.py` + `ml/rules_fit.py` (v2, D-26) |
| `ml`: features, XGBoost/sklearn baselines (B0, B1) | Built | `experiments/e1_detection.py`, `e1b_rules_v2.py` |
| Temporal split utilities | Built | `eval/splits.py` + config (D-24). Rolling-origin utility **not** built |
| MLflow registry | Partial | Runs logged to a local SQLite MLflow store. Models are registered by hash-checked manifest files; the Postgres `ml.model_artefact` table is **not** built |
| Calibration tooling | Partial | ECE/Brier in `eval/metrics.py`; isotonic calibrator exists only inside the E1 script, not as a reusable module |
| Signals / alerts / cases workflow (FR-DET, FR-CASE) | Built, with deviations | migration 0006, `services/cases.py` (see section 5) |
| E1 and measured analytics performance | Built, partly | E1 done and reproduced; graph latency measured; end-to-end per-case latency across all analytics **not** measured |

## 2. Exit criteria
| Criterion | State | Evidence |
|---|---|---|
| Fixture-exact analytics | Met | hand-computed fixtures for features, rules, behaviour; graph cycle enumeration cross-checked against NetworkX |
| T-INV-03 (`as_of`/window leakage) | Met | property tests for features, graph, behaviour; repo-layer integration test from Phase 1 |
| T-INV-09 (`calibrated_probability` null unless calibrator named) | Met | DB CHECK constraint, tested |
| E1 reproducible from config | **Met (same machine)** | second full run: split hash, dataset manifest hash and feature version identical; every B1 PR-AUC/ROC-AUC (3 seeds x 3 windows) differs by exactly 0; same tree counts; B0 metrics identical; calibration error identical. The code hash differs only because I edited unrelated code (artefact path check) between runs. Not tested on another machine or thread count |
| Graph size limit measured (R-05) | Met | whole-graph NetworkX 1.5M edges = 17 s / 465 MB (5.08M not built); ego limits declared, truncation flagged and measured (10.5% of random subgraphs) |
| Security review, no open CRITICAL/HIGH | **Not met** | 0 critical, 1 high (H-1) |
| Tests | Met | 52 unit + 58 integration = 110 pass; ruff, mypy strict, import-linter clean |
| Coverage floor (target 85%) | Met | 95% line+branch over `src/aegis` (1,029 statements, 212 branches). 0% on `common/config.py` and `domain/labels.py`; 71% on `common/logging.py`. Coverage is a floor, not evidence of correctness |

## 3. Measured results (details in the linked docs)
- **E1** ([E1_RESULTS_RUN1.md](research/E1_RESULTS_RUN1.md)): B1 XGBoost test PR-AUC 0.490 +- 0.010 (base rate 0.0011), ROC-AUC 0.980; B0 v1 at the base rate.
- **E1b** ([E1B_RULES_V2.md](research/E1B_RULES_V2.md)): B0 v2 test PR-AUC 0.0038, ROC 0.76; still far below B1.
- **E1c/E1d** ([E1CD_CONTROLS.md](research/E1CD_CONTROLS.md)): the first shuffled-label control missed its pre-registered criteria (criterion was flawed); full permutation passes 3/3. No evidence of leakage; generator artefacts are NOT ruled out (pair-history alone gives ROC 0.90).
- **E2** ([E2_GRAPH.md](research/E2_GRAPH.md)): ego-subgraph p50 1.4 ms, p95 about 0.1 s; fan structures visible, cycle/flow-through weak.
- **E3** ([E3_BEHAVIOUR.md](research/E3_BEHAVIOUR.md)): about half of accounts lack enough history; new-counterparty share AUC 0.93, volume deviations 0.56-0.57.
- **Ingestion (Phase 1 target NFR-PERF-01)**: 5,014 txn/s measured vs 50k target, not met. Load of 5.08M rows into a dataframe 47-76 s; feature build for all rows about 15 s.
- **Short-horizon shift (RQ5):** B1 validation->test PR-AUC falls 0.03-0.04 in every seed, but validation was used for model selection and there is no window-noise estimate, so no degradation claim is made. B0 v2 is flat (0.00373 -> 0.00376).

## 4. Security review (one pass; subagent read the code, ran nothing)
| ID | Sev | Finding | Proposed action |
|---|---|---|---|
| H-1 | HIGH | `aegis_app` can insert a closure request and its own approval (decider_role is caller-supplied text; requester may equal decider) and then close any case. The D-09 gate protects against bugs, not against a compromised app login | Migration 0007: a separate `aegis_approver` login role as the only holder of INSERT on `workflow.approval`; decider bound to `session_user`; trigger rejecting requester = decider. **Needs owner decision (new role, new policy)** |
| M-1 | MED | `aegis_app` can edit `priority_score`, `last_alert_at`, `assigned_to` with no audit trail (bury a case, force alert merging) | Revoke column UPDATE; change through audited definer functions that keep `priority_score` non-decreasing and `last_alert_at` <= now() |
| M-2 | MED | Approvals are not tied to the case lifecycle (an approval requested before the case existed or before review still passes) | Require request time after the case's PENDING_REVIEW event |
| M-3 | MED | Artefact manifest and model sit together, so anyone who can write the folder can replace both; manifest `file` was unvalidated | `file` validation **fixed now** (plain file names only, tested). Pinning the expected hash outside the folder waits for the model-registry table / MODEL_ACTIVATION gate |
| L-1 | LOW | `case_event.reason` is uncapped text | length CHECK (with 0007) |
| L-2 | LOW | Definer functions run as the migration role, a superuser locally | dedicated non-superuser owner for any real deployment |
| L-3 | LOW | Label-isolation contract covered only `aegis.analytics` | **Fixed now**: also covers `store.frames` and `eval.splits`; new integration test shows the loaded frame has no label columns |
| L-4 | LOW | `APPROVALS.json` is repo-editable | hand-review changes (already stated in `migrate.py`) |
Reviewer positives: no SQL string-building in services; `case_guard` hardened (fixed search_path, schema-qualified, EXECUTE revoked from PUBLIC); no INSERT grant on `case_event` (only the trigger writes it); closure_approval_id bound to the same case; no pickle/joblib; no hard-coded passwords in experiments (dev-only defaults live in `scripts/migrate.py` and test fixtures, local only).
Not verified by the reviewer: live grants in a running database; xgboost model loading path; `domain/cases.py`, `store/labels.py`, `ml/rules_fit.py`, and the integration tests. I did not do a separate manual review of those files beyond the tests.

## 5. Known deviations in what was built (all also in decisions.md)
- Alerts reference signals, not evidence items (evidence store is Phase 3), so FR-DET-05 is met in weaker form.
- Roles are asserted by the caller; no users/RBAC until the API phase (this is H-1's root cause).
- Only the CASE_CLOSURE gate has a downstream check; the other five D-09 actions have request/decision records but nothing consumes them yet (T-INV-08 partly met).
- FR-AGT-01..03 exist as analytics only; the agent wrappers (typed task/result, tool gateway, permissions, `as_of` enforcement) are Phase 3.
- FR-EXP-04: no rolling-origin utility; FR-EXP-05: only one ablation (intrinsic-only features), none for grounding/verification (not yet built).
- NFR-PERF-02 (30 s p95 per case end-to-end): not measured.

## 6. Carried-forward gaps from Phase 1 (status now)
Clean-machine start test: still not done. Human security review and CI: still none. Stale RUNNING batches (L-1), quarantine raw rows readable by the app role and error chaining (S-6/L-2), batch-identity race (S-7): open. Audit hash chain: not built (actor forgery by app roles was closed in 0005). The 1,968 laundering transactions outside any pattern block: cause still unknown. Ingest speed 5k txn/s: unchanged. Dependency stack: now installed and exercised for numpy, pandas, scikit-learn, xgboost, mlflow, sqlalchemy, alembic, networkx; **torch/PyG, LangGraph, the Anthropic SDK, FastAPI are still only resolved, not imported** (Phase 3+).

## 7. Unverified claims and weaknesses of this review
- Single security pass by a subagent, no human review; it did not run code.
- E1 reproducibility shown on one machine only.
- The detection results are on one synthetic dataset; the strongest signal (never-seen counterparty pair, payment format) may be generator-specific.
- Early stopping/seed selection used validation; test and stress were each evaluated once per config, but v1 per-rule test statistics were seen before v2 was designed (D-26 disclosure).
- Section 2 coverage number was measured before and after adding tests; only the final run (95%) is quoted.

## 8. Decisions requested from the owner
1. **H-1:** approve migration 0007 (separate approver login role, decider bound to the login, requester != decider, audited column changes, approval tied to the PENDING_REVIEW event, reason length cap)? Alternative: accept the risk for now, with the gap written into the Phase 3 entry conditions. Recommendation: do 0007 now, because Phase 3 adds more gated actions that will reuse the same approval tables.
2. Whether Phase 2 may exit with the non-security gaps in section 5 carried into Phase 3 (recommended), or whether the Postgres model registry table and a packaged calibration module must be finished first.
3. Phase 3 (evidence layer, agent runtime, LLM gateway) needs your separate approval, plus Q-02-related confirmation that the pseudonymisation design is acceptable before any Claude API call is made.

## 9. Round 2 - owner approved migration 0007 ("Approve 0007"); independent re-review
Tests now: 52 unit + 66 integration = 118 pass; ruff, mypy strict, import-linter clean. Dev database migrated through 0007. The re-review was a second subagent pass (read-only, ran no code), so this is still not a human review.

| Prior finding | Re-review verdict | Notes |
|---|---|---|
| H-1 forged approval | **FIXED** (limit below) | only `aegis_approver` can insert decisions; logins and times are set by DB triggers; requester != decider on human ids; tested with the forged-insert attack |
| M-1 unaudited priority/assignment edits | **PARTIALLY FIXED** | column UPDATE revoked, monotonic checks, audited `assign_case`; but see NEW-1, NEW-2 |
| M-2 stale approvals | **FIXED** | request must postdate the latest entry into PENDING_REVIEW; rework loop now has its own test |
| L-1 reason cap | **FIXED** | CHECK + `left(...,500)` |

Remaining limit on H-1: `requested_by` / `decided_by` are still caller-asserted text, so requester != decider is cosmetic at the human level. The real control is login separation (app service vs approver login); one shared approver login means one compromised approver credential can approve under any name (NEW-5). Verified human identity needs the API phase.

New findings from the re-review (none fixed yet; 0008 would need your approval):
| ID | Sev | Finding | Status |
|---|---|---|---|
| NEW-1 | MED | `bump_case` (callable by aegis_app) accepts any score: NaN/Infinity would pin a case to the top of the queue forever; it also refreshes `last_alert_at` without any alert and is unaudited | Open - a defect I introduced in 0007 |
| NEW-2 | MED | aegis_app can INSERT cases with arbitrary `priority_score` and `assigned_to` (unaudited assignment at insert) | Open - same |
| NEW-3 | MED | `audit.record` is still callable by aegis_app with free-text action names, so it can write audit rows that look like genuine `case.closed` / `approval.*` events; approval requests/decisions write no audit.event of their own (their insert-only tables are the record) | Open |
| NEW-4 | LOW | the disposition chosen at closure is not bound to the approved request | Open |
| NEW-5 | LOW | one shared approver login | Open (identity phase) |
| NEW-6 | LOW | approver role created without a password; `set_role_passwords` allowed omitting it | **Fixed now** (argument required) |
| NEW-7 | LOW | dev default approver password in scripts/tests | Accepted: local-host only, test fixture refuses non-local DBs |
Tests the reviewer called weak were strengthened: the M-2 test now matches the trigger's message; the "may not decrease" test targets one open case instead of all rows; new tests exercise the approver-only branch and the same-login branch by widening grants in a scratch database, and cover the rework loop.
Not verified by the reviewer: pg_hba authentication, default PUBLIC privileges outside `workflow`, ownership of definer functions (superuser migration role locally).

## 10. Verdict after round 2
The HIGH finding is closed, so the "no open CRITICAL/HIGH" gate is met on the evidence of one subagent pass. Three MEDIUM findings (NEW-1..3) remain open; NEW-1 and NEW-2 are small defects in my own 0007 and I recommend a small migration 0008 (reject non-finite scores, require an existing alert for `bump_case`, force `assigned_to` NULL and bound `priority_score` on insert, audit the bump). Whether to restrict `audit.record` to an allow-list is a design choice for you (NEW-3). **Exit is possible if you accept NEW-1..3 as carried risks, or after 0008.**

## 11. Round 3 - owner approved migration 0008 ("Build 0008")
Tests: 52 unit + 69 integration = 121 pass; ruff, mypy strict, import-linter clean; dev database migrated through 0008. **0008 has not had an independent security re-review**; the checks below are my own tests.

| Finding | State | What changed |
|---|---|---|
| NEW-1 `bump_case` | **Fixed (own tests)** | takes the justifying alert; refuses NaN, +-Infinity, NULL, an alert not attached to that case, and unknown/closed cases; audits a priority increase (`case.priority_raised`); the service now attaches the alert before bumping |
| NEW-2 case insert | **Fixed (own tests)** | inserts must have no assignee and a finite priority; the update guard refuses a non-finite priority; `derived.signal.score` must be finite |
| NEW-3 audit | **Fixed (own tests); a design choice of mine** | application logins can no longer call `audit.record`; they get `audit.record_app`, which only accepts actions starting with `app.`, so they cannot write entries that look like system events. Approval requests and decisions now write `approval.requested` / `approval.approved|denied` rows themselves. You did not specify the allow-list rule, so please confirm or change the `app.` prefix |
| NEW-4 disposition not bound to the request | Open (LOW) | |
| NEW-5 one shared approver login | Open (LOW, identity phase) | |
Small leftovers: 0008 grants `audit.record_app` to `aegis_approver` although that role has no USAGE on schema `audit`, so the grant is harmless but unused (not edited, because the applied migration's hash is recorded). Human ids on approvals are still caller-asserted text.

**Verdict:** no open CRITICAL/HIGH; the three MEDIUM findings from round 2 are fixed on my own tests but not independently re-reviewed. Remaining Phase 2 gaps are the non-security items in section 5. Phase 2 can exit on your say-so. Phase 3 is not authorised.

## 12. Owner declaration
On 2026-11-12 the owner declared Phase 2 done, accepting the carried-forward gaps (see decisions.md). They must be re-listed at the Phase 3 entry review.
