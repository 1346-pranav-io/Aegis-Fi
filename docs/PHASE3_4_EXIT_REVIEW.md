# Phases 3 and 4: joint exit review

Date: 2026-11-12 · Author: principal architect · Inputs: `docs/PHASE3_NOTES.md`, `docs/PHASE4_NOTES.md`, `docs/RECONCILIATION.md`, three read-only security review passes (subagent, ran no code), the test suite. **Verdict: functional exit criteria met on fixtures; NOT ready to declare done** until the open items in section 5 are decided. The owner decides whether to accept them and exit.

## 1. Exit criteria from the roadmap
Phase 3: T-INV-02, 04, 05, 06, 07, 10, 11, 12, 15, 16 green; grounding/verification/orchestration experiments run on fixtures. Phase 4: T-INV-17, 18 green; E5, E6 run.

| Invariant | What it checks | Where | State |
|---|---|---|---|
| T-INV-02 no write path to `source` | agents have no source-write tool or permission | `tests/phase3/test_tool_gateway.py` | Met (gateway level; DB grants also refuse) |
| T-INV-04 provenance walk | every claim resolves to leaves with hashes intact | `test_provenance.py`, `test_evidence_pg.py`, `test_e2e_pipeline.py`, `test_runtime_pg.py` | Met on fixtures and on Postgres runs. Leaves of kind DOCUMENT / REFERENCE (other than `query:`) are rejected until a document store exists |
| T-INV-05 claims need valid evidence | citations of unknown or out-of-context ids rejected before storage | `test_evidence_store.py`, `test_tool_gateway.py`, `test_e2e_pipeline.py` + DB triggers (0009) | Met |
| T-INV-06 verifier isolation | verifier input holds no investigator context | `test_e2e_pipeline.py` | Met against the stub; the isolation-off ablation is **not measurable** with a stub |
| T-INV-07 unsupported claims never reach findings | | `test_e2e_pipeline.py` | Met on fixtures |
| T-INV-10 replay | recorded run replays identically | `test_e2e_pipeline.py`, `test_llm_gateway.py` | Met (needs content-addressed evidence ids: P-1, approved) |
| T-INV-11 allow-lists | every agent denied every tool outside its list | `test_tool_gateway.py` (exhaustive agents x tools) | Met |
| T-INV-12 prompt injection | injected text does not widen tools or alter outputs | `test_e2e_pipeline.py`, `test_llm_gateway.py` | **Containment only** (fence, schema, allow-list). Never exercised against a real model |
| T-INV-15 budgets | runs stop with BUDGET_EXHAUSTED, partial results kept | `test_runtime_runs.py` | Met. Tiny budgets now refuse to run (P-4) |
| T-INV-16 kill and resume | a killed run resumes to the same final state | `test_runtime_pg.py`, `test_runtime_runs.py` | Met on Postgres; resume now re-checks ids (P-2) |
| T-INV-17 unmerge restores, dependents re-flagged | | `test_entity_resolution.py` | Met; auto re-verification queued by `services/er_review.py` (new, tested on Postgres) |
| T-INV-18 open HIGH contradiction blocks closing | | `test_contradiction_workflow.py`, `test_reconciliation_pg.py`, `test_human_review.py` | Met, and the human-only status path is enforced by the reviewer login |

Experiments (all measured; none involves a real model):
- **Phase 3 fixtures (G1, V1, O1),** run id `b7c544398b2e`, stub LLM: grounding removed only claims citing evidence outside their context (17.8% of stored claims still faulty by design); deterministic checks let 0 of 697 faulty claims into findings but excluded 10 clean hypothesis claims; a rubber-stamp stub verifier let 34 faulty claims through; `rule_adaptive` gave a complete report in 4 steps where `fixed_pipeline` needed 6. These describe the stub and the fixtures, not Claude.
- **E6 (contradiction detection):** the detectors score 1.000 precision and recall on the seeded set, which is mostly by construction (same author, same conventions); the naive baseline scores 0.5 / 0.4. It says nothing about natural contradictions.
- **E5 (entity resolution):** the pre-registered expectation (auto-merge produces silent errors that grow with perturbation) was **not observed**; silent propagation was 0.0000 in every cell. Post-hoc variants (not pre-registered) show proposal-only removes silent errors by construction and that the shipped scorer's margin is thin (a one-digit-different name scores about 0.79 against a 0.80 threshold). **E5 does not support the research claim for RQ3** and needs a better design if that claim matters.

## 2. Tests
393 unit + 117 integration = 510 pass, 1 expected skip; ruff, mypy strict (107 files) and 5 import contracts clean. Line+branch coverage of `src/aegis` 90% (5,669 statements, 1,284 branches): `eval/phase3/*` are 0% because the experiment scripts drive them, `common/config.py` and `domain/labels.py` 0%, `evidence/checks.py` 74%. Coverage is a floor, not a correctness claim.

## 3. Security
Three read-only reviewer passes, none a human review and none run against live behaviour.
- Round 1 (Phase 2 re-review 0007/0008) and round 2 (merged code): closed H findings by migrations 0007, 0008, 0023.
- Round 3 (re-review of 0023): H-1 fixed, H-2 mostly fixed; one gap fixed immediately (the app could undo a human's decision on a link).
- Since then: checkpoint tables now inside the approval gate (0024, P-2), pseudonymisation made central (D-29).
Open findings are listed in section 5. No CRITICAL or HIGH finding is known to be open, but that rests on the reviewers' reading only.

## 4. Decisions made in this phase pair
D-27 (parallel run on a frozen contract), D-28 (P-1..P-5), D-29 (pseudonymisation policy). Approvals: migrations 0010, 0011 (owner), 0020-0022 (owner, in the stream session), 0023 and 0024 (owner approved the approach, not the SQL line by line). Dev database `aegis` is now migrated through 0024 and the IBM data is intact (5,078,345 transactions, 5,177 laundering labels).

## 5. Open items (carried forward unless the owner decides otherwise)
Security (medium/low, from the reviews): unverified entity names in auto-confirmed exact-ID links (M-2); contradictions without a fingerprint key bypass the new controls and a forged record can pre-empt the real one's metadata (M-3); first writer of a mention fixes its name (M-4); members addable after commit (L-1); one shared reviewer login and one shared approver login; job `finish()` ignores the lease, no heartbeat; replay file unsigned; human ids everywhere are caller-asserted text until the API phase; closure gate does not check who dismisses a HIGH finding beyond the reviewer role.
Engineering: pseudonymisation is central now but still not proven against a real model, and `free_text` flags are not yet set by any capability (the rule only bites in LIVE mode, which is disabled); Intelligence agent not built; no explanation proposals; report template is a placeholder (FIU-IND format unverified, Q-04); documents/reference store absent; IBM accounts file not in the database (so `entity_id` identifiers cannot be verified); job queue has no worker loop; no API, no CLI, no CI, no human security review, no clean-machine start test.
Research: no result involves a real model; E5 inconclusive; all detection results are on one synthetic 18-day dataset.

## 6. Recommendation
Declare Phases 3 and 4 done on the condition that the owner accepts the section 5 items as carried risk, then move to Phase 5 (HITL, API and CLI, no UI per Q-11) after approving it separately. The most valuable next steps are: an API with real identities (it removes the shared-login and caller-asserted-id findings), a worker loop for the job queue, and a decision on whether to run the first live Claude test under D-29 on synthetic data.
