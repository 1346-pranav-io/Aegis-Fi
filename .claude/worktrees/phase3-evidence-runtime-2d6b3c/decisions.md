# Aegis-FI — Decision Log

This is the single place to understand *why* the project looks the way it does.
Rule (set by the project owner): **every major decision passes through the owner with an explanation.**
Agents may propose; only the owner approves. Nothing marked `PROPOSED` is final.

**Status values:** `OWNER-SET` (given in the project brief) · `PROPOSED` (architect recommendation, awaiting owner approval) · `APPROVED` (owner confirmed) · `REJECTED` · `SUPERSEDED`.

**Change rule:** an approved architecture decision may only be changed by adding a new entry that supersedes it, with the reason. Never edit history silently.

Last updated: 2026-11-11 (initial foundation phase)

---

## 1. Decisions set by the owner (brief)

| ID | Decision | Status |
|----|----------|--------|
| U-01 | Project = Aegis-FI, AML investigation primary, payment fraud secondary | OWNER-SET |
| U-02 | Dual goal: credible scalable system **and** reproducible research implementation | OWNER-SET |
| U-03 | Stack: Python 3.11/3.12, FastAPI, PostgreSQL, LangGraph, Claude API, scikit-learn, XGBoost, NetworkX, PyTorch/PyG (later), Next.js + TypeScript, Docker, Pytest, MLflow | OWNER-SET |
| U-04 | Five research contributions (adaptive orchestration; evidence grounding + independent verification; confidence-aware ER + contradiction handling; HITL feedback validation; investigation-level evaluation + temporal robustness) | OWNER-SET |
| U-05 | Nine runtime agents: Master Orchestrator, Transaction, Graph, Behaviour, Intelligence, Investigation, Verification, Contradiction, Reporting | OWNER-SET |
| U-06 | Nine development subagents: principal-architect, research-analyst, backend-architect, ml-graph-engineer, agent-systems-engineer, security-reviewer, test-engineer, frontend-engineer, documentation-engineer | OWNER-SET |
| U-07 | No application implementation until the owner approves the foundation | OWNER-SET |
| U-08 | Restrictions: no needless microservices; no agent where a deterministic function suffices; LLMs never modify source records; model confidence is not a verified probability; no fabricated datasets/results/metrics; no undocumented architecture change; no unrestricted subagent write access; no external deployment | OWNER-SET |
| U-09 | Mandatory engineering properties: typed interfaces, schema validation, modular architecture, unit + integration tests, structured logging, error handling, versioned model artefacts, reproducible experiments, immutable source financial records, evidence provenance, human approval for consequential actions | OWNER-SET |

---

## 2. Architect proposals (need owner approval)

Each entry: decision · reason · alternative rejected · consequence.

### D-01 Modular monolith, not microservices — APPROVED (2026-11-11)
- **Decision:** One Python codebase, deployed as an API process and a worker process, plus PostgreSQL, MLflow and (later) the Next.js frontend.
- **Why:** Restriction U-08 forbids needless microservices; a research system needs easy reproducibility and debugging. Module boundaries are enforced by import rules, so extraction later is possible.
- **Rejected:** Service-per-agent (network failures, distributed tracing, deployment burden with no scale need).

### D-02 Python 3.12 only — APPROVED (2026-11-11)
- **Why:** Brief allows 3.11/3.12; testing one version removes a CI matrix and a source of irreproducibility. PyG/XGBoost support to be confirmed at Phase 1.
- **Fallback:** drop to 3.11 if a required dependency lacks 3.12 wheels.

### D-03 PostgreSQL is the only datastore in v1 — APPROVED (2026-11-11)
- **Decision:** Postgres holds source data, derived data, evidence, cases, audit log and the job queue (`SELECT … FOR UPDATE SKIP LOCKED`). Graphs are built in NetworkX from Postgres. No Redis, Kafka, or graph database.
- **Why:** Fewest moving parts; transactional guarantees across evidence and state.
- **Revisit when:** measured graph size exceeds in-memory limits (see Risk R-05).

### D-04 Immutable source records enforced at the database — APPROVED (2026-11-11)
- **Decision:** Source tables live in a `source` schema. The application role has `INSERT` and `SELECT` only; triggers reject `UPDATE`/`DELETE`. Corrections are new rows that `supersede` the old one. Each record carries a content hash.
- **Why:** Requirement U-09/U-08 — "LLMs must not modify source records" is enforced by permissions, not by prompt instructions.

### D-05 Deterministic-first agent design — APPROVED (2026-11-11)
- **Decision:** The Transaction, Graph and Behaviour agents are **deterministic analytic modules exposed through the common agent interface** (typed task in, typed result out). They contain no LLM. The Orchestrator in v1 is a deterministic planner (rules over typed signals); a learned/adaptive policy is added in a later phase as a research variant and compared against it. LLMs are used only in: Investigation (reasoning/synthesis), Verification (semantic support checks, after deterministic checks), Contradiction (adjudication proposals, after deterministic detection), Reporting (drafting), Intelligence (extraction from unstructured text only).
- **Why:** Restriction "do not introduce agents where deterministic functions are sufficient". The three analytic agents keep the agent *interface* so the orchestrator treats all capabilities uniformly and the adaptive-allocation research question remains testable.
- **Consequence:** "Agent" in this project means *a capability with a typed contract, tool allow-list and permissions*, not necessarily an LLM loop.

### D-06 Scores are not probabilities — APPROVED (2026-11-11)
- **Decision:** Every model output is stored as `score` (uncalibrated). A field `calibrated_probability` is nullable and may be set only when a registered calibration artefact exists, with its calibration error measured on a temporally later validation set. LLM "confidence" is never numeric-trusted: it is reported only as an ordinal label (`low/medium/high`) labelled *self-reported*, and is evaluated empirically, not assumed.
- **Why:** Restriction U-08(5).

### D-07 Claims must cite evidence — APPROVED (2026-11-11)
- **Decision:** Any statement the Investigation or Reporting agent makes about a case is a `Claim` linked to ≥1 `Evidence` records. A claim with no verified support is displayed as `UNSUPPORTED` and may not appear in a report's findings section.
- **Why:** Core contribution #2.

### D-08 Independent verification — APPROVED (2026-11-11)
- **Decision:** The Verification Agent receives only (claim text, cited evidence content). It never sees the investigator's reasoning or conversation. Deterministic checks (evidence exists, hash matches, numbers/dates/entities in claim appear in evidence) run first; the LLM check is only for semantic entailment. Where budget allows, a different model/prompt is used than the Investigation Agent.
- **Caveat:** Same-vendor models can share failure modes; this is stated as a limitation and measured (ablation), not assumed solved.

### D-09 Consequential actions need human approval — APPROVED (2026-11-11)
- **Actions gated:** (a) submitting/recommending a regulatory report (SAR/STR) filing, (b) closing a case with a disposition, (c) any restriction/freeze recommendation to a downstream system, (d) entity merges that are not exact-identifier matches, (e) promoting a model artefact to "active", (f) accepting feedback into training data.
- **How:** an `Approval` record by a user with an approver role; agents can only create `PendingApproval` records. The system performs no external side effects in v1 at all.

### D-10 Pseudonymise before calling the Claude API — APPROVED (2026-11-11)
- **Decision:** An LLM gateway module replaces direct identifiers (names, account numbers, addresses) with stable tokens before sending text to the API, and restores them locally. All LLM calls are logged (model id, prompt version, input hash, output) for replay.
- **Open:** whether real customer data may be used with an external API at all — see Q-02.

### D-11 Research data: public/synthetic only, availability unverified — APPROVED (2026-11-11)
- **Decision:** No real customer data in the research repo. Candidate public/synthetic AML and fraud datasets are listed in `docs/research/EVALUATION_FRAMEWORK.md` as **candidates whose availability, licence and fit must be verified** in Phase 1 before any is chosen.

### D-12 Reproducibility mechanics — APPROVED (2026-11-11)
- **Decision:** MLflow for runs and model registry; dataset manifests with content hashes; fixed seeds recorded; experiment configs are files committed to the repo; temporal splits only (no random splits for headline results); LLM calls recorded for replay.

### D-13 Tooling — APPROVED (2026-11-11)
- uv (deps + lockfile), ruff (lint+format), mypy `--strict` on core packages, pytest, Pydantic v2 (all boundary schemas), SQLAlchemy 2.0 + Alembic (migrations), structlog (JSON logs), Docker Compose (local only).

### D-14 Backend first; UI later — APPROVED (2026-11-11)
- **Decision:** The OpenAPI schema is the frontend contract. Next.js work starts in Phase 5, after the API stabilises.

### D-15 Auth model — APPROVED (2026-11-11)
- OIDC-ready bearer-token auth; roles `analyst`, `approver`, `model_risk`, `auditor`, `admin`. Local dev uses static dev tokens clearly marked non-production.

### D-16 Single LLM gateway — APPROVED (2026-11-11)
- All Claude calls go through one module that enforces budgets, timeouts, retries, pseudonymisation, structured-output validation, logging and replay caching. Model IDs live in config, never in code. Agents cannot import the Anthropic SDK directly (lint rule).

### D-17 Time handling — APPROVED (2026-11-11)
- All timestamps UTC. Every record has `event_time` (when it happened) and `ingested_at` (when we learned it). Analyses take an `as_of` timestamp and may only read data with `ingested_at <= as_of`. This prevents temporal leakage and enables temporal-robustness experiments.

### D-18 Repo layout — APPROVED (2026-11-11)
`backend/` (src package `aegis`), `frontend/`, `experiments/`, `docs/`, `.claude/agents/`, `deploy/` (local Docker only).

### D-19 Fraud extension reuses the same contracts — APPROVED (2026-11-11)
- No new agents; adds typology configs, features and a dataset adapter (Phase 7).

### D-20 GNN deferred — APPROVED (2026-11-11)
- PyG models only after classical graph features + XGBoost baselines exist (Phase 7). GNNs must beat the baselines to justify inclusion.

### D-21 Development subagent permissions — APPROVED (2026-11-11)
- Reviewers (security-reviewer, research-analyst for code) are read-only; builders can Edit/Write; only test-engineer, backend-architect, ml-graph-engineer, agent-systems-engineer get Bash. Path scoping is stated in each agent's prompt (frontmatter cannot enforce paths) and checked in review. Dev subagents are never mixed with runtime agents (`docs/architecture/AGENT_ARCHITECTURE.md` §1).

### D-22 RQ5 reframed to short-horizon temporal robustness — APPROVED (owner, 2026-11-11)
- **Decision:** RQ5/H5b evaluate day-level robustness on the ~18-day IBM HI-Small span; no claims about long-term drift.
- **Why:** Measured data span is 2022/09/01-09/18 (DATASET_VERIFICATION.md). Claiming long-horizon drift would be unsupported.
- **Rejected:** downloading Medium/Large variants (unverified to span longer; extra size) for now. Can be revisited by superseding entry.
- **Consequence:** E8 uses small day-level windows; null results acceptable and must be reported.

### D-23 Investigation cases are built from the 370 pattern blocks; non-pattern laundering handled separately — APPROVED (owner, 2026-11-11)
- **Facts (measured, DATASET_VERIFICATION.md):** 370 pattern blocks cover 3,209 of 5,177 laundering transactions (62.0%); 1,968 laundering transactions belong to no block and the dataset does not say why. No block has all accounts under one Entity ID. Entity names have no noisy variants.
- **Proposal:** (a) Investigation-level gold (METHODOLOGY 3.4) uses only the 370 blocks (typology + exact member transactions). (b) The 1,968 non-pattern laundering transactions are used for detection-level metrics (E1) only and are reported separately, never as investigation gold, unless the cause is found. (c) Entity-resolution experiments (RQ3/E5) rely on seeded name perturbations of entity names, since the data has no natural variants; Entity ID gives true account-to-entity grouping but must not be treated as 'same launderer'.
- **Why:** avoids claiming gold we do not have. **Alternative:** treat the 1,968 as single-transaction cases with a binary label; rejected for now because it would inflate case counts with items that have no structural truth.
- **Consequence:** ~370 positive investigation cases (40-54 per pattern type), a small sample; confidence intervals will be wide and must be reported.

### D-24 Headline temporal split uses Sep 1-10; the Sep 11-18 tail is a separate stress slice — APPROVED (owner, 2026-11-11)
- **Facts (measured from the loaded DB):** daily rows fall from 208k-1.1M (Sep 1-10) to 396, 281, 184, 121, 46, 46, 23, 11 (Sep 11-18); positives per day stay 8-232, so the tail is about 59% positive (655 of 1,108 rows) vs about 0.1% before. Cause unknown (likely the generator letting laundering chains finish after benign activity stops; unverified).
- **Decision:** train Sep 1-6, validation Sep 7-8, test Sep 9-10 (all UTC days, half-open). Sep 11-18 is reported only as a labelled 'tail regime' stress slice, never mixed into headline metrics, with the class-balance caveat stated.
- **Rejected:** chronological use of all 18 days (test would be mostly positive, inflating PR-AUC); dropping the tail (loses 655 positives and the only later-window shift evidence).
- **Consequence:** headline test positives = 956 of about 862k rows. RQ5 (short-horizon) compares Sep 7-8 and Sep 9-10 performance plus the tail slice.

### D-25 Phase 2 detection unit is the transaction — APPROVED (owner, 2026-11-11)
- **Decision:** B0 (rules) and B1 (XGBoost) score each transaction against Is Laundering. Features are point-in-time: for a transaction at time t they may use only records with event_time strictly earlier than t (and ingested_at <= as_of). Account-level aggregates are features, not the unit.
- **Why:** matches the dataset's labels; avoids inventing an account-level label definition.
- **Rejected:** account-level unit (needs a made-up label rule). Can be added later by a superseding entry.

### D-26 B0 v2 procedure (pre-registered before running) — APPROVED (owner asked "Build B0 v2", 2026-11-11)
- **Why:** B0 v1 sat at the base rate because its thresholds were global quantiles dominated by hub accounts (E1_RESULTS_RUN1.md item 2). v1 stays in the record and is always reported next to v2.
- **Procedure (fixed now, before any v2 number exists):** eight indicator rules: the six v1 rules plus FRESH_DEST (d_in_all low) and FRESH_ORIGIN (o_out_all low), both plausible mule-account typologies. For each rule the threshold is chosen from a declared grid of feature quantiles (upper tail .5/.75/.9/.95/.99/.999, or lower tail .001/.01/.05/.1/.25/.5 for 'low is suspicious' features) by maximising the indicator's average precision on TRAIN, requiring at least 500 firings and lift > 1, else the rule is disabled. Rule weight = log(train precision / train base rate). Score = sum of weights of fired rules. Fitting uses TRAIN labels only (in `aegis.ml`, not `aegis.analytics`); validation is for inspection; test and stress are evaluated once.
- **Disclosure:** the v1 per-rule test-window statistics had already been seen by the architect when this was designed, so v2 is not a blind test of the idea of rules; it is a pre-registered procedure whose parameters were not tuned on test.
- **Not claimed:** that v2 is a strong detector. It is a deterministic baseline.

### D-27 Phases 3 and 4 run in parallel on a shared, frozen contract — PROPOSED (owner asked to start both together)
- **Owner instruction (2026-11-12):** "Start phase 3 and phase 4 together if they are independent; later fix the correlations (if any)."
- **Finding:** they are mostly independent but have seven coupling points (docs/PHASE3_4_PLAN.md section 1). The three that block parallel work (evidence/claim/contradiction tables, the capability/tool-gateway contract, the ER link read interface) were resolved up front by building a small shared contract: `domain/evidence.py`, `domain/agents.py`, `domain/entities.py`, migration 0009, import-linter contracts. That deviates slightly from "start both and fix later" by doing about one day of shared work first; the reason is that retrofitting a shared schema after two streams have built different versions is the expensive kind of correlation.
- **Frozen after this point:** those shared files change only through the architect with a decisions entry. Migration ranges: Phase 3 uses 0010-0019, Phase 4 uses 0020-0029. Neither stream may self-approve a migration.
- **Not yet decided (owner):** how to run the streams (commit first for separate worktrees, shared folder, or sequentially) - plan section 6.
- **Also flagged:** migration 0009 was approved only by implication of the instruction "start phases 3 and 4"; it follows the approved EVIDENCE_MODEL, but please review it.

---

## 3. Open questions for the owner

| ID | Question | Why it matters |
|----|----------|----------------|
| Q-01 | Will any real (non-synthetic) transaction data ever be used? | Drives privacy, compliance and data-handling scope | **ANSWERED (owner, 2026-11-11): "Yes, later."** Synthetic-only for now; before any real data: retention/deletion policy (NFR-PRIV-02), data-handling review, and re-decision of Q-02 for real data. D-11 stays in force until then.
| Q-02 | May data (even pseudonymised) be sent to the Claude API? | Determines LLM gateway design and whether a local-model fallback is needed | **ANSWERED: Yes, pseudonymised** (D-10). Architect note: this is accepted for synthetic data; if real data is introduced (Q-01) it must be re-confirmed.
| Q-03 | Which public/synthetic dataset(s) should be the primary benchmark? | Determines schemas, labels, and what claims the paper can support | **ANSWERED: IBM synthetic AML first.** Availability, licence, size and label semantics still UNVERIFIED; Phase 1 verifies before adoption.
| Q-04 | Which regulatory regime to model for reports (e.g. FinCEN SAR vs. other)? | Report template and typology list | **ANSWERED (owner, 2026-11-11): India FIU-IND STR. Template must be built from the official FIU-IND format read directly from the source (not from memory); until verified, use a placeholder structure.**
| Q-05 | Target venue and deadline? | Sets phase priority and experiment scope | **ANSWERED: college research project; no venue/deadline given.** Treated as unspecified; architect will propose scope cuts if a deadline appears.
| Q-06 | LLM and compute budget ceiling (per experiment)? | Sets orchestrator budget defaults and ablation size | **ANSWERED (implicitly): college project, no figure given.** Working assumption: small budget; heavy use of record/replay and small ablations. A dollar cap is still wanted.
| Q-07 | Python version: confirm 3.12 only (D-02)? | Minor | **ANSWERED (owner, 2026-11-11): Yes, Python 3.12 only (D-02).**
| Q-08 | Is local Docker Compose sufficient as the only deployment target? Is there a git remote / CI host? | Brief says no external deployment; CI design | **ANSWERED (owner, 2026-11-11): Yes: local Docker Compose only; local git only, no remote or CI host. CI pipeline is defined as config but not hosted.**
| Q-09 | Who acts as human annotator / analyst reviewer, if anyone? | Validity of HITL claims (RQ4); otherwise feedback is simulated and must be disclosed | **ANSWERED (owner, 2026-11-11): No real reviewers: feedback is simulated and must be disclosed in the paper (RQ4 limitation).**
| Q-10 | May `HUMAN_NOTE` evidence support suspicion findings (default: context only)? | Evidence policy | **ANSWERED (owner, 2026-11-11): Notes are context only; they cannot support suspicion findings.**
| Q-11 | Is the web UI required for the paper, or are API + CLI enough? | Scope/time (roadmap Phase 5) | **ANSWERED (owner, 2026-11-11): API + CLI only; no web UI. Roadmap Phase 5 UI dropped; frontend-engineer unused unless re-scoped.**

---

## 4. Change history

| Date | Entry | By |
|------|-------|----|
| 2026-11-11 | Initial log created with U-01..U-09, D-01..D-21, Q-01..Q-08 | principal architect |
| 2026-11-11 | Owner approved D-01..D-21 as a batch ("Approve all"). Q-01..Q-11 remain open. Phase 1 start NOT yet authorised. | owner |
| 2026-11-11 | Owner answered Q-01 (real data: yes, later), Q-02 (yes, pseudonymised), Q-03 (IBM synthetic AML first), Q-05/06 (college research project, no specifics). Still open: Q-04, Q-07..Q-11. | owner |
| 2026-11-11 | Owner answered Q-04 (India FIU-IND STR), Q-07, Q-08 (local only), Q-09 (simulated feedback), Q-10 (context only), Q-11 (no UI). All Q-01..Q-11 now answered. Phase 1 not yet authorised. | owner |
| 2026-11-11 | Owner authorised Phase 1 (platform skeleton + data foundation, per IMPLEMENTATION_ROADMAP). Phases 2+ still need separate approval. | owner |
| 2026-11-11 | Phase 1 slice 1 done by architect: local git init (staged, not committed); backend skeleton (common, domain.source, ingestion.validate), SQL migration 0001 with DB-enforced immutability, local compose. Python 3.12 via uv (system default is 3.14). Migration is plain SQL for now; Alembic wrapper deferred (proposal, needs owner OK). Integration tests NOT run: Docker daemon not running. IBM dataset only partially verified (see docs/research/DATASET_VERIFICATION.md). | architect |
| 2026-11-11 | Docker (WSL mode) works. Migration 0001 applied to local Postgres 16; T-INV-01 integration tests (30) pass; added dev dependency psycopg[binary] (needed for DB tests; flagging per "ask before new dependency"). Test data is rolled back, DB left clean. | architect |
| 2026-11-11 | Ingestion slice: config-driven CSV adapter + idempotent insert-only batch writer (dedupe by ref+content hash, quarantine, labels separate, batch FAILED status on error). psycopg[binary] moved from dev to runtime dependency. No real dataset mapping shipped: IBM column names unverified. 45 tests pass (12 unit, 33 integration against throwaway DBs). | architect |
| 2026-11-11 | IBM HI-Small downloaded by owner and verified by inspection (docs/research/DATASET_VERIFICATION.md). Real mapping ingestion/mappings.py added; 20k-row sample ingests with 0 quarantined. Schema change (my own, pre-data): migration 0002 makes source.transaction.currency text because the source uses currency names, not ISO codes. FLAGGED FOR OWNER: dataset spans only ~18 days, limiting RQ5 temporal robustness; writer is row-by-row and too slow for 5M rows (needs COPY/batching) before full ingest. | architect |
| 2026-11-11 | **D-22 (owner decision): RQ5 reframed to short-horizon (day-level) temporal robustness** because HI-Small spans ~18 days. Long-term drift claims are out of scope. Medium/Large variants not downloaded. Updated RESEARCH_METHODOLOGY (RQ5, H5b), EVALUATION_FRAMEWORK (3.6, E8), RESEARCH_GAPS (G8), DATASET_VERIFICATION. | owner |
| 2026-11-11 | Writer rewritten to COPY-into-staging bulk path (migration 0003 adds label index). Full IBM HI-Small loaded into dev DB as role aegis_ingest: 5,078,345 transactions, 515,088 accounts, 5,177 laundering labels (5,078,345 label rows total), 0 quarantined, time range 2022-09-01 00:00 to 2022-09-18 16:18 UTC; matches the file profile exactly. MEASURED ingest rate 5,014 txn/s (1,013 s) on this workstation vs NFR-PERF-01 design target of 50k/s: target NOT met; bottleneck is likely Python-side hashing/ID generation, not yet profiled. One earlier attempt is retained as a FAILED batch (migration not yet applied to dev DB). | architect |
| 2026-11-11 | Phase 1 exit review written (docs/PHASE1_EXIT_REVIEW.md). Verdict: not ready to exit; security review found 2 HIGH (ingest runs as superuser via SET ROLE; no role password mechanism) and 3 MEDIUM findings. Added as_of repo + test, label-isolation lint rule, scripts/dev.py, .gitignore patterns. Awaiting owner decision on fixes. | architect |
| 2026-11-11 | Owner: "Yes, fix them" (S-1,S-2,S-3,S-5). Done: ingest/app roles now log in directly with passwords set by scripts/migrate.py (env, dev defaults labelled); no SET ROLE; config has no credential default; migration 0004 = batch state machine (RUNNING->COMPLETE/FAILED only, terminal after) + audit event per transition + truncate guards + REVOKE audit SELECT from ingest; checksummed migration runner (store/migrate.py, public.schema_migrations, protected by triggers) with OWNER-APPROVED header rule for migrations touching source tables; 0002 marked "retroactive" (it was applied before the process existed; disclosed). initdb no longer applies migrations. Dev DB adopted 0001-0003 then applied 0004. 54 tests pass (12 unit, 42 integration). NOT fixed: S-4 audit actor forgery/hash chain, S-7 batch-identity race (a unique index would strand crashed RUNNING batches), S-6 raw_record masking. Independent re-review requested. | architect |
| 2026-11-11 | Re-review of fixes done (docs/PHASE1_EXIT_REVIEW.md updated). Applied without needing a design decision: M-1 (known dev passwords refused for non-local hosts; tests refuse non-local DB) and M-2 (new tests assert the TRIGGER raises, run as table owner). OPEN, need owner decision: H-1 (OWNER-APPROVED header is only a convention; reviewer wants approvals recorded outside the migration file and fail-closed matching), M-3/S-4 (make audit trigger SECURITY DEFINER and revoke INSERT on audit.event from app roles, else audit rows are forgeable), L-1 (no recovery for batches stuck RUNNING after a crash). | architect |
| 2026-11-11 | Owner: "Fix both now". Done: migration 0005 (audit.record + batch_guard SECURITY DEFINER, actor=session_user, INSERT/sequence revoked from app roles) and hash-bound fail-closed approval gate (migrations/APPROVALS.json; in-file headers ignored). 62 tests pass (12 unit, 50 integration). Still open: L-1 stale RUNNING batches, S-6/L-2, S-7, no hash chain on audit. Approvals file is repo-editable (reviewed record, not crypto). | architect |
| 2026-11-11 | Dataset checks finished: CDLA-Sharing-1.0 text read (results unrestricted; publishing data or Enhanced Data must stay CDLA-Sharing, so ship a download script not data); pattern lines link exactly to Trans rows (3,209/3,209) but cover only 62.0% of laundering txns (cause unknown); Entity ID does not group launderer accounts. D-23 PROPOSED (needs owner approval). | architect |
| 2026-11-11 | Owner approved D-23. | owner |
| 2026-11-11 | **Owner declared Phase 1 DONE** with these gaps carried forward (not closed): clean-machine start test, human security review, CI, L-1 stale RUNNING batches, S-6/L-2 quarantine raw rows + error chaining, S-7 batch race, no audit hash chain, unexplained 1,968 non-pattern laundering rows, ingest speed 5k txn/s vs 50k target, dependency stack only resolved not imported. Phase 2 NOT authorised yet. Nothing committed to git. | owner |
| 2026-11-11 | Owner asked to start Phase 2 ("Lets start with phase2?"). Treated as authorisation to begin Phase 2 planning and build per roadmap; key research design choices (detection unit, split) go to owner first. | owner |
| 2026-11-11 | Owner approved D-24 (headline split Sep 1-10, tail as stress slice) and D-25 (transaction-level detection unit). | owner |
| 2026-11-11 | E1 run 1 done (docs/research/E1_RESULTS_RUN1.md, MLflow run b0b63f64..., artefacts manifest-verified). Measured: B1 XGBoost test PR-AUC 0.490 +- 0.010 (base rate 0.0011), ROC 0.980; B0 rules v1 at base rate (thresholds dominated by hub accounts; not tuned). Calibration holds on test (ECE 0.0003) but fails on the stress slice (0.24). New deps: numpy, pandas, scikit-learn, xgboost, mlflow-skinny, sqlalchemy, alembic, pandas-stubs (all within stack). MLflow uses a local SQLite store (file store is rejected by this MLflow version). Interim file-based artefact registry; Postgres ml.model_artefact table NOT built. | architect |
| 2026-11-11 | B0 v2 (D-26) run once: test PR-AUC 0.00376 (base 0.0011; v1 0.00122; B1 0.490), ROC 0.759. Still weak; score has ~30 distinct values so top-k ties dominate. Results in docs/research/E1B_RULES_V2.md (MLflow run recorded). Phase 2 remaining: graph analytics, behaviour module, signals/alerts/cases, per-case latency, shuffled-label check. | architect |
| 2026-11-11 | Label-shuffle controls (docs/research/E1CD_CONTROLS.md): E1c MISSED its pre-registered criteria (1 of 3 seeds in the ROC band) because the criterion was badly designed (noise-fit model still outputs a function of features that correlate with real test labels); E1d follow-up supports that (9 features have univariate AUC >0.1 from 0.5; pair_prior alone 0.90 reversed, fmt 0.81) and full-permutation passes 3/3. Does not test future-info leakage (property test does) nor generator artefacts (open). | architect |
| 2026-11-11 | Graph analytics built (analytics/graph.py, 10 unit tests incl. NetworkX cycle cross-check, window property test, truncation flags) + E2 benchmark (docs/research/E2_GRAPH.md): ego subgraph p50 1.4 ms / p95 ~0.1 s / max 0.57 s; 10.5% of subgraphs truncated by size limits; whole-graph NetworkX 1.5M edges 17 s / 465 MB (5.08M not built). Oracle-windowed block check: cycle through centre 57% for CYCLE blocks, 26-35% for other types, 0.5% for random controls; fan hubs separate clearly; flow-through median 0 everywhere (uninformative). Bug found and fixed: PageRank non-convergence crashed the run, now reported as pagerank_failed. Dependency added: networkx. | architect |
| 2026-11-11 | Behaviour module built (analytics/behaviour.py, 6 unit tests incl. window-leak property test; INSUFFICIENT_HISTORY status; self + peer deviation) and E3 run (docs/research/E3_BEHAVIOUR.md): 51-57% of accounts lack enough history; new-counterparty share AUC 0.93 (block vs control accounts), count z-scores 0.56-0.57, amount 0.64, peer percentile 0.65. Oracle-windowed, descriptive, not a detector evaluation. Unit tests now 48. | architect |
| 2026-11-11 | Alerts/cases workflow built (owner: "Alerts/cases workflow"): migration 0006 (approved in APPROVALS.json) adds derived.signal, workflow.alert/case_alert/investigation_case/case_event/approval_request/approval; services/cases.py (alerts need a producer + reason codes; grouping by account within 24 h of the last alert; deterministic queue by score desc, age, id; transitions; request/decide approval; close_case). DB enforces: legal transitions OPEN>INVESTIGATING>PENDING_REVIEW>CLOSED (+rework), CLOSED frozen, closing needs an APPROVED CASE_CLOSURE decision for that exact case (D-09), approve only with decider_role=approver, calibrated_probability only with a calibrator id (T-INV-09), signals/alerts/approvals insert-only, transitions audited via SECURITY DEFINER with actor=session_user. 108 tests pass (51 unit, 57 integration); dev DB migrated through 0006. KNOWN DEVIATIONS: alerts reference signals, not evidence items (evidence store is Phase 3, so FR-DET-05 is met in weaker form); decider_role is caller-asserted (no users/RBAC until the API phase); four-eyes (requester != approver) NOT enforced; model_artefact_id is a plain string (no ml.model_artefact table yet); only the CASE_CLOSURE gate has a downstream check, the other D-09 actions only have request/decision records. | architect |
| 2026-11-11 | Phase 2 exit review run (docs/PHASE2_EXIT_REVIEW.md): NOT READY - security review found 1 HIGH (H-1: aegis_app can forge a closure approval and close any case; roles caller-asserted, requester may equal decider), 3 MEDIUM, 4 LOW. E1 reproduced bit-identically on the same machine (max metric diff 0). Coverage 95% (110 tests). Fixed immediately: manifest file-name validation (M-3 part), label-isolation contract widened to store.frames and eval.splits (L-3), new frames/labels integration test. Proposed migration 0007 (approver role, decider=session_user, requester!=decider, audited column changes, approval after PENDING_REVIEW, reason cap) awaits owner decision. | architect |
| 2026-11-11 | Owner approved migration 0007 ("Approve 0007"). Built: aegis_approver login role (only holder of INSERT on workflow.approval), DB-bound request/decision logins and timestamps, requester!=decider on human ids, case priority/last_alert/assignee changes only via audited definer functions, closing approval must postdate latest PENDING_REVIEW entry, reason cap 500; set_role_passwords now requires the approver password. 118 tests pass. Independent re-review: H-1/M-2/L-1 FIXED, M-1 partial; new MEDIUM NEW-1 (bump_case accepts NaN/any score, unaudited), NEW-2 (case INSERT can set arbitrary priority/assignee), NEW-3 (audit.record free-text callable by app) OPEN; human ids still caller-asserted until the API phase. Proposed 0008 awaits owner. See PHASE2_EXIT_REVIEW sections 9-10. | owner+architect |
| 2026-11-12 | Owner: "Build 0008". Built migration 0008 (bump_case validated/audited, case insert guard, finite scores, audit.record restricted to internal use + audit.record_app requiring an "app." action prefix [my design choice, flagged], approval requests/decisions audited). 121 tests pass (52 unit, 69 integration). NOT independently re-reviewed. NEW-4/NEW-5 left open. | owner+architect |
| 2026-11-12 | **Owner declared Phase 2 DONE.** Carried into Phase 3 (not closed): 0008 not independently re-reviewed; NEW-4 (disposition not bound to approval request), NEW-5 (one shared approver login), human ids on approvals caller-asserted until the API phase; Postgres ml.model_artefact registry table not built (interim hash-checked manifests); calibration code not packaged as a module; no rolling-origin split utility; end-to-end per-case latency (NFR-PERF-02) not measured; only the CASE_CLOSURE gate has a downstream check; alerts reference signals not evidence items; agent wrappers for Transaction/Graph/Behaviour not built; the "app." audit-prefix rule awaits owner confirmation; plus all Phase 1 carried gaps (clean-machine start, human security review, CI, stale RUNNING batches, S-6/S-7, no audit hash chain, 1,968 non-pattern laundering rows unexplained, ingest 5k txn/s vs 50k target). Generator-artefact caveat on all detection results stands. Phase 3 NOT authorised. | owner |
| 2026-11-12 | Owner: "Start phase 3 and phase 4 together if they are independent, later fix the correlations". Architect built the shared contract slice (domain/evidence.py, agents.py, entities.py; migration 0009 evidence schema; import contracts; package stubs; 13 tests) and wrote docs/PHASE3_4_PLAN.md. 134 tests pass. Streams NOT yet launched; D-27 proposed. No Claude API call is authorised (Q-02 pseudonymisation design still unconfirmed). | architect |
| 2026-11-12 | Owner chose "Commit, use worktrees". Initial commit ce42b6c on branch main (renamed from master; 131 files; data/models/results git-ignored; commit made at the owner's request). Two worktree sessions started from main: phase3-evidence-runtime and phase4-er-contradictions (briefs enforce: frozen shared files, migration ranges 0010-0019 / 0020-0029, no self-approval of migrations, no migrations on the shared dev DB, no live Claude call). Merge and reconciliation (docs/PHASE3_4_PLAN.md section 5) are NOT done and need the owner. | owner+architect |
