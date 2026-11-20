# Testing Strategy

Status: DRAFT · Version 0.1. Coverage figures are targets (NFR-MNT-03); none have been measured.

## 1. Test pyramid
| Level | Scope | Tooling | Runs |
|---|---|---|---|
| Unit | pure functions, Pydantic models, policies, verifiers' deterministic checks | pytest, hypothesis | every commit |
| Contract | OpenAPI snapshot, agent I/O schemas, evidence-type schemas | pytest, schemathesis (optional) | every commit |
| Integration | repositories + real Postgres (roles, triggers), job queue, runtime with stubbed/recorded LLM | pytest, testcontainers | every commit / PR |
| Architecture | import-linter contracts, no direct Anthropic SDK imports, no UPDATE/DELETE SQL on protected tables (static check) | import-linter, custom checks | every commit |
| End-to-end | ingest → detect → investigate → verify → report on a small fixture dataset | pytest + Compose | PR / nightly |
| UI | component + E2E (Phase 5) | Vitest, Playwright | PR |
| Experiment | harness correctness on fixtures; metric implementations vs hand-computed values | pytest | every commit |
| Security | RBAC matrix, injection fixtures, permission denial | pytest + security-reviewer manual pass | PR / phase exit |

## 2. Invariant test catalogue (must exist before the phase that introduces the feature exits)
| ID | Invariant | Method | Maps to |
|---|---|---|---|
| T-INV-01 | App role cannot UPDATE/DELETE `source.*`, `evidence.*`, `audit.*` | DB integration test attempts each; expects permission error | SAC-02, FR-ING-04 |
| T-INV-02 | No LLM/agent code path writes to `source` | static scan + tool-gateway permission test | SAC-02 |
| T-INV-03 | `as_of` leakage: inserting future-dated record doesn't change any analytic output | property test across analytics + repositories | FR-DET-04 |
| T-INV-04 | Provenance walk resolves every claim to source leaves, hashes intact | walk over fixture runs | SAC-01 |
| T-INV-05 | Claim without valid evidence id rejected | unit + hypothesis | FR-AGT-05 |
| T-INV-06 | Verifier input contains no investigator context | capture `inputs_seen`; assert | FR-VER-02 |
| T-INV-07 | Unsupported/unverifiable claims never in report findings | property test | SAC-06 |
| T-INV-08 | Each gated action fails without approval, succeeds with approver role | parametrised integration | SAC-03 |
| T-INV-09 | `calibrated_probability` is null unless calibrator artefact referenced | constraint + unit | D-06 |
| T-INV-10 | Replay of recorded run reproduces stored outputs | replay test | SAC-04 |
| T-INV-11 | Each agent denied tools outside allow-list | parametrised | FR-AGT-06 |
| T-INV-12 | Prompt-injection fixtures (in transaction narrative / document) don't cause out-of-allow-list calls or altered outputs | fixture suite | NFR-SEC-05 |
| T-INV-13 | Ingestion idempotent; quarantine captures invalid rows | integration | FR-ING-02/03 |
| T-INV-14 | Labels not reachable from feature builders | architecture test | DATA_SCHEMA §9 |
| T-INV-15 | Budget limits halt runs with `BUDGET_EXHAUSTED`, partial results kept | integration | FR-ORC-02 |
| T-INV-16 | Kill/resume run yields same final state as uninterrupted run (with replay) | integration | FR-ORC-05 |
| T-INV-17 | Entity un-merge restores prior state; dependent claims re-flagged | integration | FR-ER-* |
| T-INV-18 | Open HIGH contradictions block closure until acknowledged | integration | FR-CON-* |

## 3. LLM-dependent testing
- No live API calls in the default test suite or CI.
- Record/replay fixtures keyed by (prompt id+version+hash, pseudonymised input hash). Prompt change ⇒ fixtures invalid ⇒ explicit re-record step (requires owner OK for cost).
- Stub LLM for logic tests (returns scripted structured outputs, including malformed ones, to test repair and failure paths).
- Optional, separately-gated "live smoke" job run manually with budget cap.

## 4. Fixtures and data
- Tiny hand-built datasets with known patterns (structuring, cycle, fan-in/out, rapid movement, drift, near-duplicate entities, contradictory attributes).
- Fixture generator is code with seed; fixtures versioned.
- No real data. Large benchmark datasets used only in experiments, not in CI.

## 5. Quality gates
- Phase exit requires: all invariant tests for that phase green; lint/type clean; coverage floor on core modules (target ≥ 85%, line + branch for domain/evidence/verification/runtime); security-reviewer report with no open CRITICAL/HIGH; documentation updated.
- Mutation testing (optional) on verification and provenance logic.

## 6. Evaluation-harness testing
Metric implementations tested against hand-computed examples; split generator tested for chronological ordering and no overlap; leakage tests; seeded-perturbation generator tested for determinism.

## 7. Non-functional tests
Benchmarks (ingestion rate, analytics latency, API latency) run on demand and recorded as measurements (replacing the targets in NFR-PERF); failure injection (DB down, LLM timeout, malformed LLM output, worker kill).

## 8. Defect handling
Failing tests are never disabled without a `decisions.md`/issue note and owner visibility. Flaky tests are quarantined with evidence and a fix deadline.

## 9. Traceability
Each FR/NFR/SAC id maps to ≥1 test id; the traceability table is generated/maintained (documentation-engineer + test-engineer).
