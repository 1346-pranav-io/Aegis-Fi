# Phase 3 notes: evidence layer, LLM gateway, agent runtime (stream A)

Branch `claude/phase3-evidence-runtime-2d6b3c` (worktree). Nothing merged, pushed, or applied to the shared dev database. **No live Claude API call was made and no Anthropic SDK is installed** (Q-02 gate; the real provider is a marked TODO in `llm/providers.py`).

## 1. Status in one paragraph
Deliverables 1-5 of the brief exist and are tested. The whole pipeline (alert case -> specialists -> investigation -> verification -> report) runs end to end on in-memory services and on a real scratch Postgres with LangGraph's Postgres checkpointer, including kill-and-resume from a fresh process. Everything that calls a model uses a scripted stub; the experiments therefore describe the pipeline and the stub, not Claude. **Not done:** Intelligence agent (priority S, skipped), `BEHAV_MODEL_SCORE` (needs the model registry), a worker loop around the job queue, C6 re-verification (deferred to reconciliation by the plan).

## 2. What exists
| Area | Where | Notes |
|---|---|---|
| Evidence store | `evidence/store.py` (interface, in-memory), `evidence/pg_store.py` (Postgres) | Idempotent create via `UNIQUE(case_id, content_hash)`; evidence ids are **content-addressed** (`ev_` + hash of case+content) so they are stable across runs; claims must cite same-case evidence; `HUMAN_NOTE` is context-only (Q-10); contradictions created and listed (reads `evidence.contradiction_current`). Caller owns the transaction (deferred DB triggers fire at commit). |
| Provenance walk | `evidence/provenance.py` | claim -> evidence -> ... -> SOURCE/DOCUMENT/REFERENCE/HUMAN; recomputes each evidence hash, checks every input hash, dangling refs, cycles, cross-case, missing inputs. Returns all problems; `assert_intact()` raises. |
| Shared helpers | `evidence/{canon,checks,drafts}.py` | `hashable()` (floats are rejected by `common.hashing`, so analytics output is converted to fixed-precision Decimals); deterministic claim checks shared by Investigation and Verification (capabilities cannot import each other); draft builders; self-certifying `query:` references (see section 6). |
| LLM gateway | `llm/` (`gateway.py`, `providers.py`, `pseudonym.py`, `prompts.py`, `replay.py`, `budget.py`, `records.py`, `config.py`, `types.py`) | Provider interface + `ScriptedProvider`; RECORD/REPLAY/LIVE modes; HMAC-based stable reversible pseudonymiser; versioned prompt files with SHA-256; nested budgets; timeout + bounded retry; JSON-schema output validated by Pydantic with bounded repair; call records (memory + `llm.call_record`); model ids only from `LlmConfig` (no default). |
| Runtime | `runtime/` | Tool gateway (`tools.py`), capability registry/catalog, `InvestigationState` + single `Plan` schema (`state.py`), policies `fixed_pipeline` / `rule_adaptive` (`policies.py`), idempotent step engine (`engine.py`), LangGraph graph (`graph.py`), runner (`runner.py`), in-memory + Postgres services (`services.py`, `pg_services.py`), job queue, checkpointer helpers (`checkpoint.py`), composition root (`wiring.py`). |
| Capabilities | `capabilities/{transaction,graph,behaviour,investigation,verification,reporting}/` | Transaction/Graph/Behaviour deterministic; Investigation, Verification, Reporting use the gateway only through the tool gateway; prompts live in each package's `prompts/`. |
| Experiments | `eval/phase3/` + `experiments/p3_fixture_experiments.py` + `experiments/configs/p3_fixture_experiments.json` | G1/V1/O1, results in `docs/research/P3_FIXTURE_EXPERIMENTS.md`. |
| Tests | `tests/phase3/` | See section 3. |

## 3. Tests and results (all run before writing this)
`uv run python scripts/dev.py check`: ruff, ruff format, mypy --strict (92 source files), import-linter (5 contracts kept), **306 unit tests passed** (60 pre-existing + 246 new).
`AEGIS_TEST_DATABASE_URL=...@127.0.0.1:5432/aegis uv run python scripts/dev.py test-int`: **83 passed, 1 skipped** (the skip is the pre-existing IBM HI-Small test: data not downloaded in this worktree; 74 pre-existing + 10 new).

New unit tests per file: tool_gateway 106 (exhaustive agent x tool allow-list matrix), e2e_pipeline 60, runtime_runs 32, llm_gateway 17, specialists 14, evidence_store 10, provenance 7. New integration tests: evidence_pg 2, phase3_migrations 4, runtime_pg 4.

| Invariant | Covered by |
|---|---|
| T-INV-02 no agent write path to source | tool permission vocabulary has no source-write class; no tool maps to one; static scan of `capabilities/runtime/llm/evidence` for `INSERT/UPDATE/DELETE source.` (`test_tool_gateway`) |
| T-INV-03 as_of (through the agents) | future-ingested row changes no specialist output (`test_specialists`), gateway refuses a later `as_of` |
| T-INV-04 provenance walk | walk tests incl. every tamper kind (`test_provenance`), over fixture runs (`test_e2e_pipeline`) and over real source rows in Postgres (`test_runtime_pg`) |
| T-INV-05 claim without valid evidence | store, gateway context rule, hypothesis property test, and end-to-end invented ids |
| T-INV-06 verifier isolation | provider requests contain only `claim` + `evidence` blocks; canary from investigator absent; `inputs_seen_hash` recomputed independently |
| T-INV-07 unsupported never in findings | 40-seed property test on `build_report_body`, plus end-to-end adversarial narratives |
| T-INV-10 replay | recorded run replays with no provider to the same claims/statuses/report |
| T-INV-11 allow-lists | exhaustive matrix + permission-class test |
| T-INV-12 injection fixtures | hostile text as a counterparty and in prompt data stays inside the data fence; extra fields/tool names rejected; no tool outside the allow-list attempted. **Limited:** the stub is not a model that can be talked into things; this tests the containment layers, not model robustness |
| T-INV-15 budgets | max_steps 1..5 -> `BUDGET_EXHAUSTED`, partial results kept; step-level budget exhaustion; nested LLM budgets |
| T-INV-16 kill/resume | 9 in-memory combinations (3 crash points x 3 steps) and 2 on Postgres from a **new process**; resumed run equals uninterrupted run, nothing duplicated |

## 4. Measured numbers
All from `docs/research/P3_FIXTURE_EXPERIMENTS.md`, run id `b7c544398b2e`, commit `727dad7`, 40 seeded fixture cases, **stub LLM only**. Names are G1-grounding, V1-verification, O1-orchestration because `E2_GRAPH.md`/`E3_BEHAVIOUR.md` already use E2/E3 for other things (the clash with EVALUATION_FRAMEWORK section 5 should be fixed there by the architect).
* **G1:** at stub fault rate 0.25, 24.0% of drafted claims carry an injected fault (235/979); after the citation/context check 17.8% of stored claims do (161/905). Grounding only removes claims citing evidence outside their context; the other fault kinds pass it by design.
* **V1 (fault rate 0.3):** with no verification 20.5% of report findings are faulty (182/889). Deterministic checks alone: 0/697 faulty in findings, but 10/707 clean claims excluded (all clean `TYPOLOGY_HYPOTHESIS` claims, which need the semantic check). Deterministic + faithful stub verifier: 0/707 and 0/707. Deterministic + rubber-stamp stub verifier: 34/741 faulty reach findings, all `UNSUPPORTED_LEAP` (0/34 caught) while wrong numbers and wrong entities are still caught by the deterministic layer. A reject-all verifier excludes 707/707 clean claims.
* **O1:** `fixed_pipeline` finds the explanatory evidence at every budget but produces **no report below 6 steps**; `rule_adaptive` gets a complete report in 4 steps. At `max_steps=3` rule_adaptive reserves all steps for the mandatory tail and gathers no evidence (0/40 explanatory evidence, run PARTIAL): a policy design flaw I did not fix, because the fix is a design choice (see open risks).
* The "isolation off" ablation is **not measurable** with a stub (it ignores context by construction).
* Experiment V1 found a real bug (a wrong value that equals some other number in the evidence passed `AMOUNT_MATCHES`); fixed in `evidence/checks.py` and regression-tested. The first run (id `9f3903c2ebba`, before the fix) showed 2 such leaks; it is superseded.
* No MLflow logging for these fixture runs (JSON in `experiments/results/`, git-ignored, plus the markdown).

## 5. Deviations from the specs (all deliberate; flag if you disagree)
1. **Graph nodes:** AGENT_ARCHITECTURE lists `plan, dispatch, collect, synthesise, verify, contradict, resolve_entities, report, finalise`. Built: `plan -> dispatch -> collect -> (dispatch | finalise)`; synthesise/verify/contradict/report are tasks run by `dispatch` (keeps the graph free of domain logic). `resolve_entities` is Phase 4's.
2. **`analytics.transactions` does not exist** (Phase 2 built features/graph/behaviour only); transaction computations live in `capabilities/transaction/compute.py`.
3. **Specialist evidence goes through `AgentResult.evidence`**, persisted by the runtime ("runtime wraps AgentResult"); no `create_evidence` tool exists, so no agent can write evidence directly. `get_features`, `get_rule_pack`, `get_model`, `lookup_reference`, `get_document` tools are not built (no backing store yet).
4. **Evidence ids are content-addressed**, not random ULIDs (needed so recorded prompts replay; ULIDs differ per run). Shape `ev_` + 26 chars unchanged. Claim/verification/report ids remain random, so prompts never contain them: the Reporting prompt uses aliases `C1..Cn`.
5. **Reports cover the claims of one run** (reports are versioned per case); open contradictions are case-wide.
6. **Empty-data evidence** (`TXN_AGGREGATE` with 0 rows, graph with no edges) needs an input but there is no source row to cite: it cites a self-certifying `REFERENCE` of the form `query:<digest of the query>` (see request R3).
7. **`Budget.max_steps`** is used as the run's agent-step limit and (x50) as a per-task tool-call cap; `max_tokens=0` means "no LLM allowed", not "exhausted".
8. **Policy `rule_adaptive` allocator** counts step slots (steps minus the mandatory tail), ties broken by relative cost units from the catalog; not a token/cost optimiser.
9. **Report template is a placeholder** (Q-04: official FIU-IND STR format unverified); stated in every report.
10. **Intelligence agent skipped** (priority S).
11. **`aegis.eval.phase3`** is a new subpackage in `eval/` (outside the ownership table but not frozen); `eval` is not in any import-linter contract.

## 6. Migrations awaiting owner approval (not in `APPROVALS.json`)
The runner refuses unapproved protected migrations, so on this branch **`scripts/migrate.py` will refuse 0010 and 0011 until the owner adds entries**. Suggested entries (hashes of the files as committed):

| File | What it does | sha256 |
|---|---|---|
| `0010_llm_call_record.sql` | schema `llm`; insert-only `llm.call_record` (pseudonymised responses only); grants to `aegis_app` | `450f1d24945a9a1d91be1e11ccff50da6f3c7cb31d039a348194a59f428021b9` |
| `0011_runtime_schema.sql` | schema `runtime`; insert-only `investigation_run`, `run_status_event` (legal-transition trigger), `agent_step` (`UNIQUE(run_id, task_id)`), `report` (next-version trigger); mutable `job` queue; view `run_current`; grants | `8d76c6769bb876274a1d6e1213f16b91f46095c493e3e1da1ec21afca5eb9da8` |

Also for the owner's awareness:
* **LangGraph checkpoint tables** (`public.checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are created by the library's own `setup()` via `runtime.checkpoint.install_checkpoint_tables(admin_url)`, run once as the schema owner, not by a migration file. This is a schema addition outside `migrations/` and needs a decision (alternative: vendor the DDL into a migration; fragile across library versions).
* **Dependencies added** (approved stack): `langgraph`, `langgraph-checkpoint-postgres` plus transitives (notably `langchain-core` and `langsmith`; LangSmith tracing is off unless its env vars are set; nothing sets them, and no network call is made by our code). No other dependency; `anthropic` is not installed.
* **Test infrastructure:** `tests/integration/conftest.py` now builds scratch databases from a temp copy of the migrations in which any file lacking an owner approval gets a *test-harness-only* approval (the repo's `APPROVALS.json` is never touched). Without this, adding any new protected migration breaks the whole existing integration suite. Phase 4 will need the same edit (expect a trivial merge conflict, or take either copy). `tests/phase3/conftest.py` does the same for its own fixtures. A real-gate test (`test_new_migrations_need_owner_approval`) asserts the unmodified checker still refuses 0010/0011 until approved, and skips once they are approved.

## 7. Requests for changes to frozen files (architect)
* **R1 `common/ids.py`:** add prefixes `call` (LLM call) and `rep` (report); the gateway/report store currently build these ids locally.
* **R2 `common/hashing.py` / `domain/evidence.py`:** floats are rejected in hashed content; every producer must call `evidence.canon.hashable`. Consider accepting it in `EvidenceDraft` validation or documenting it in EVIDENCE_MODEL.
* **R3 migration 0009 / `InputRefType`:** evidence about *absence of data* has no legal input. Options: a first-class `QUERY` ref type, or allow the `query:` REFERENCE convention (current workaround, resolved by `evidence.drafts.WithQueryRefs`). It proves which question was asked as of when, not that the answer was empty.
* **R4 `domain/agents.py`:** `AgentResult` carries one `error` and no warnings, so a PARTIAL step with a reason (e.g. `CLAIMS_REJECTED`, truncation) abuses `error`; consider `warnings: tuple[AgentError, ...]`.
* **R5 `pyproject.toml`:** `aegis.eval` is in no layer contract; `eval.phase3` imports runtime. Add it above `services` or document it as an application layer. The `capabilities.*` independence contract now has real members and passes.
* **R6 `store/migrate.py`:** `_PROTECTED` matches any migration containing `trigger`, `grant`, `source`, ... so every new schema needs an owner entry; acceptable, but the suite-wide effect described in section 6 should be solved centrally.
* **R7 docs:** EVALUATION_FRAMEWORK E2/E3/E4 labels clash with `E2_GRAPH.md`/`E3_BEHAVIOUR.md`; IMPLEMENTATION_ROADMAP Phase 3 exit names "E2 (grounding), E3 (verification), E4 (orchestration)".

## 8. Decisions I would put to the owner (not written into `decisions.md`, to avoid a conflicting edit with the Phase 4 stream)
* **P-1** Content-addressed evidence ids (section 5.4) - enables replay and cross-run reuse.
* **P-2** Checkpoint tables via library `setup()` (section 6).
* **P-3** Accept `query:` references as the provenance for "no data" evidence (R3).
* **P-4** What `rule_adaptive` should do when the step budget is smaller than the mandatory tail (see risks).
* **P-5** Whether `request_approval` for a filing recommendation should be requested by the Reporting agent automatically; currently only when the task param `request_filing_approval` is true (default false).

## 9. Open risks and known weaknesses
1. **Everything LLM-related is untested against a real model.** The stub cannot be argued with, so T-INV-12 only proves containment (fence, schema, allow-list), and V1's "faithful verifier" is a hand-written rule, not evidence that an LLM verifier works.
2. **Pseudonymisation is only as good as registration.** Names not registered by the caller are not detected; the regex layer catches long digit runs, e-mails, 8-12 char hex ids (it can over-match). Amounts are left in clear on purpose. The owner must confirm this design before any live call (Q-02). Short identifiers (< 4 chars) are matched case-sensitively; a masked capital letter round-trips, but a lower-case occurrence of a one-letter account would leak (and the leak guard would not see it because it is also case-sensitive).
3. **Same-vendor verifier correlation** (D-08 caveat) is not measurable here.
4. **`rule_adaptive` at tiny budgets** gathers no evidence (O1, `max_steps=3`). Needs a decision (P-4): e.g. always keep one specialist and drop VERIFICATION/REPORTING, or refuse to run.
5. **Evidence/claim order and ids in prompts:** replay depends on byte-identical prompts. Anything that injects a random id, timestamp, or unordered iteration into a prompt breaks replay with a visible `ReplayMiss`, not silently.
6. **Provenance for DOCUMENT/REFERENCE leaves:** no document store exists, so `PostgresLeafHashes` resolves SOURCE only; citing a DOCUMENT/REFERENCE (other than `query:`) is rejected until a store exists.
7. **No audit hash chain / human identity** (inherited Phase 1-2 gaps). Tool-denial audit rows use `audit.record_app('app.tool_denied', ...)`, relying on the owner-pending "app." prefix rule.
8. **Postgres backend assumes one autocommit connection per run**; the checkpointer uses a second. Not load-tested; the job queue has no worker loop (claim/finish only).
9. **Report `limitations` are partly boilerplate** and the narrative's only guard is the citation check (it can still restate a verified claim misleadingly).
10. **Wall-clock timeouts** in the gateway use a worker thread; a hung provider call keeps its thread until it returns (the call is abandoned, not killed).

## 10. Integration points for the reconciliation step (docs/PHASE3_4_PLAN.md section 5)
1. **ERLinkReader:** `RuntimeServices.er` is `FakeERLinkReader` (in `runtime/services.py`, marked "PHASE 3 STAND-IN"). Replace with Phase 4's implementation. It is used by tool `create_claim` (sets `depends_on_er_links` from `unconfirmed_dependencies`) and tool `get_er_links`; the Reporting agent lists claims with unconfirmed dependencies.
2. **Contradiction capability:** register it in `runtime/wiring.py:default_registry` and merge its `PROMPTS_DIR`/`SCHEMAS` into `default_prompts`/`default_schemas` (marked in the file). The policy tail already schedules `CONTRADICTION` when the catalog has it; `ALLOW_LISTS[CONTRADICTION]` already permits `get_evidence`, `get_claims`, `get_er_links`, `llm.generate_structured` (it needs its permissions to include the matching classes). Its `AgentResult.contradictions` are persisted by the engine (members must exist in the case). Reporting and `get_contradictions` already read `evidence.contradiction_current`.
3. **ExplanationProposer:** not wired. Implement `propose(subject)` by calling `LlmGateway.generate_structured` with a Phase 4 prompt (`contradiction.*`, so the tool's own-prompt rule holds) and a registered schema; recorded responses only.
4. **C6 (rejected link -> re-verify):** the engine reuses recorded steps by `(run_id, task_id)`, so re-verification should be a *new run* (or a new task id) whose Verification input refs are the re-flagged claims; no code yet.
5. **Migration order:** Phase 3 uses 0010-0011 only (0012-0019 free). Apply on a fresh DB with both streams' files, plus `install_checkpoint_tables`.
6. **Import contracts:** `capabilities.*` independence is meaningful now; add Phase 4's package to it when it lands.

## 11. How to reproduce
```
cd backend
uv run python scripts/dev.py check
docker compose -f ../deploy/docker-compose.yml up -d
AEGIS_TEST_DATABASE_URL=postgresql://aegis_migrator:dev-only-change-me@127.0.0.1:5432/aegis uv run python scripts/dev.py test-int
uv run python ../experiments/p3_fixture_experiments.py     # ~1 minute; rewrites docs/research/P3_FIXTURE_EXPERIMENTS.md
```
