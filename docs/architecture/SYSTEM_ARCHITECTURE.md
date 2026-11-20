# System Architecture

Status: DRAFT · Version 0.1 · Decisions: D-01, D-03, D-04, D-16, D-17, D-18.

## 1. Style
**Modular monolith** (D-01): one Python package `aegis` with strict internal module boundaries, deployed as two processes (API, worker) from the same image, backed by PostgreSQL. MLflow runs as a separate local container. The Next.js frontend is a separate build artefact consuming the OpenAPI contract.

```
            ┌───────────────┐
            │ Next.js UI    │  (Phase 5)
            └──────┬────────┘
                   │ HTTPS/JSON (OpenAPI contract)
┌──────────────────▼─────────────────────────────────────────────┐
│ API process (FastAPI)                                          │
│  api/  ── auth, routers, DTOs                                  │
└──────┬───────────────────────────────────────────┬─────────────┘
       │ calls services                            │ enqueue job
┌──────▼──────────────────────────┐        ┌───────▼─────────────┐
│ services/  (use-cases)          │        │ Worker process      │
│  cases, approvals, feedback,    │        │  runtime/ (LangGraph│
│  reporting, experiments         │        │  graphs, orchestr.) │
└──────┬──────────────────────────┘        └───────┬─────────────┘
       │                                           │
┌──────▼───────────────────────────────────────────▼─────────────┐
│ capabilities/   (agent-interface modules)                      │
│  transaction  graph  behaviour  intelligence                   │
│  investigation  verification  contradiction  reporting         │
└──────┬─────────────┬──────────────┬───────────────┬────────────┘
       │             │              │               │
┌──────▼───┐  ┌──────▼─────┐ ┌──────▼──────┐ ┌──────▼──────┐
│ analytics│  │ entities   │ │ evidence    │ │ llm/        │
│ (pure fn)│  │ (ER)       │ │ (store,prov)│ │ gateway     │
└──────┬───┘  └──────┬─────┘ └──────┬──────┘ └──────┬──────┘
       └─────────────┴───────┬──────┘               │ Claude API
                      ┌──────▼──────┐               ▼ (pseudonymised)
                      │ store/ (DB) │
                      │ PostgreSQL  │◄── ml/ (training, registry ↔ MLflow)
                      └─────────────┘
```

## 2. Components and responsibilities
| Module | Responsibility | May depend on | Must not |
|---|---|---|---|
| `common/` | IDs, time, config, logging, errors, base Pydantic types | stdlib, pydantic | import any other aegis module |
| `domain/` | Pydantic domain models (transactions, entities, cases, evidence, claims) = data contracts | common | do I/O |
| `store/` | SQLAlchemy models, repositories, migrations, DB roles, job queue | domain, common | contain business rules |
| `ingestion/` | Dataset adapters, validation, quarantine, hashing, batch manifests | store, domain | call LLM; modify existing rows |
| `analytics/` | Pure deterministic functions: transaction metrics, graph metrics (NetworkX), behavioural deviation, typology rules | domain, common | touch DB or network (inputs passed in) |
| `entities/` | Entity resolution: blocking, scoring, link proposals, merge/unmerge | domain, store | auto-merge on fuzzy match |
| `evidence/` | Evidence/claim creation, hashing, provenance DAG, provenance walk | domain, store | modify evidence |
| `ml/` | Feature building, training, calibration, model registry (MLflow), scoring | analytics, domain, store | silently load unversioned artefacts |
| `llm/` | Gateway: pseudonymisation, budgets, retries, schema-validated output, record/replay, logging | common, domain | be imported by anything except `capabilities` that need LLM |
| `capabilities/` | The nine agent-interface implementations (see AGENT_SPECIFICATIONS.md) | analytics, entities, evidence, ml, llm, store(read via repos) | write to `source` schema; call each other directly |
| `runtime/` | LangGraph state graph, orchestration policies, checkpointing, budgets, tool gateway | capabilities (via registry), store, evidence | contain domain logic |
| `services/` | Application use-cases (open case, request approval, record feedback, build report) | runtime, store, evidence, entities | be imported by `capabilities` |
| `api/` | HTTP layer, authN/Z, DTO mapping | services | contain business logic |
| `eval/` | Experiment runner, metrics, temporal splits, ablation configs | everything read-only except experiment tables | write to production tables |

**Dependency direction** (lower may not import higher): `common → domain → store/analytics/llm → entities/evidence/ml → capabilities → runtime → services → api`. Enforced by `import-linter` in CI (NFR-MNT-02). `eval/` sits beside `services`.

Capabilities never call each other; only `runtime/` composes them. This keeps the orchestrator the single owner of control flow.

## 3. Deployment topology (local only, U-08)
Docker Compose services: `postgres`, `mlflow`, `api`, `worker`, (`frontend` in Phase 5). No external deployment. One image for api+worker with different entry commands.

## 4. Database roles (D-04)
| Role | Rights |
|---|---|
| `aegis_migrator` | DDL; used only by migrations |
| `aegis_ingest` | INSERT/SELECT on `source`; used only by ingestion |
| `aegis_app` | SELECT on `source`; INSERT/SELECT (+ controlled UPDATE on mutable state tables such as `case`, `job`) on others; no UPDATE/DELETE on `source`, `evidence`, `audit` |
| `aegis_readonly` | SELECT for analytics/eval |

Schemas: `source` (immutable raw), `derived` (entities, features), `evidence`, `workflow` (cases, runs, approvals, feedback), `audit`, `ml`.

## 5. Cross-cutting concerns
- **Config:** typed settings (pydantic-settings), environment-driven, config hash stored with every run.
- **Logging:** structlog JSON; fields `ts, level, event, case_id, run_id, step_id, agent, correlation_id`; no raw PII.
- **Errors:** typed error hierarchy in `common.errors`; capabilities return `AgentResult` with `status` and `error` (never raise across the capability boundary for expected failures).
- **Time:** UTC; `as_of` semantics (D-17).
- **IDs:** prefixed ULIDs (`case_…`, `ev_…`, `clm_…`, `run_…`).
- **Idempotency:** job and evidence creation carry idempotency keys.

## 6. Failure handling
| Failure | Detection | Handling | Surfaced as |
|---|---|---|---|
| Invalid input record | schema validation | quarantine + reason | ingestion report |
| DB unavailable | connection error | bounded retry; API returns 503; worker job returns to queue | health endpoint, logs |
| LLM timeout / 5xx / rate limit | gateway | backoff retries; then typed `LLM_UNAVAILABLE` failure | failed step; run `PARTIAL` |
| LLM malformed output | schema validation | ≤N repair retries; then failed step | failed step |
| Budget exceeded | gateway/runtime | stop planning; keep partial evidence | run `BUDGET_EXHAUSTED` |
| Worker crash | job lease expiry | job re-queued; resume from checkpoint; idempotent evidence | run resumes |
| Agent returns invalid result | result schema check at runtime | reject result, record failure, orchestrator may re-plan | failed step |
| Evidence hash mismatch | verification | claim `UNVERIFIABLE`; security alert logged | audit event |
| Contradiction unresolved at closure time | closure precondition | closure blocked until acknowledged by approver | 409 |
| Model artefact missing/unversioned | registry lookup | refuse to score | explicit error |

## 7. Architectural boundaries (hard rules)
1. Only `ingestion` writes to `source`, and only by insert.
2. No LLM or agent has any DB write path other than through `evidence`/`workflow` repositories exposed via the tool gateway.
3. Agents act only through the **tool gateway** with per-agent allow-lists and schema-validated arguments.
4. Only `llm/` may import the Anthropic SDK.
5. Capabilities do not call each other; `runtime/` composes.
6. No external side effects in v1 (no filings, no notifications to external systems).
7. Any architecture change → new entry in `decisions.md` before code.

## 8. Extension points
Dataset adapters (`ingestion.adapters`), typology rule packs, orchestration policies, ER scorers, verifier checks, report templates, fraud typologies (Phase 7). Each is a registry keyed by a versioned name.
