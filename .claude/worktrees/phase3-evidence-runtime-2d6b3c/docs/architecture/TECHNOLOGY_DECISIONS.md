# Technology Decisions

Status: DRAFT · Version 0.1. Owner-mandated stack items are marked **[brief]**; additions are architect proposals (see decisions.md D-13). Version numbers are intentionally not pinned here; pin via lockfile at Phase 1 after checking compatibility.

| Area | Choice | Why | Alternatives considered | Risks / notes |
|---|---|---|---|---|
| Language | Python 3.12 **[brief: 3.11/3.12]** | Ecosystem for ML/graph/LLM; one version simplifies reproducibility | 3.11 | Verify wheels for XGBoost/PyG on 3.12 in Phase 1 |
| API | FastAPI **[brief]** | Pydantic-native, OpenAPI generation, async | Flask, Django | Keep sync DB driver or async consistently (decide in Phase 1) |
| Validation | Pydantic v2 | Single schema system for API, domain, LLM outputs | dataclasses+marshmallow | — |
| Database | PostgreSQL **[brief]** | Transactions, roles, triggers for immutability, JSONB, queue via SKIP LOCKED | SQLite (not enough guarantees), MySQL | JSONB schemas must still be validated in app |
| ORM/migrations | SQLAlchemy 2.0 + Alembic | Typed, mature | raw SQL, SQLModel | Source-table immutability done in SQL (triggers/grants), not ORM |
| Orchestration | LangGraph **[brief]** | State graph, checkpointing, branching | custom state machine, Temporal | Dependency churn risk; isolate behind `runtime/` |
| LLM | Claude API **[brief]** | Required; strong structured output & tool use | — | Cost/rate limits; data-residency (Q-02); model IDs in config only |
| Classical ML | scikit-learn, XGBoost **[brief]** | Strong tabular baselines | LightGBM | Calibration via sklearn utilities |
| Graph analytics | NetworkX **[brief]** | Readable, reference-quality algorithms | igraph, graph-tool, Neo4j | Scale limit → windowed subgraphs; revisit per R-05 |
| Deep graph | PyTorch / PyG **[brief, later]** | GNN extension | DGL | Phase 7 only; must beat baselines (D-20) |
| Frontend | Next.js + TypeScript **[brief]** | Typed UI, OpenAPI client generation | — | Phase 5; generated API client to prevent contract drift |
| Containers | Docker / Compose **[brief]** | Reproducible local env | devcontainers | Local only (U-08) |
| Testing | Pytest **[brief]** + hypothesis, pytest-cov, testcontainers | Property tests for invariants; real Postgres in tests | unittest | Testcontainers needs Docker in CI |
| Experiments | MLflow **[brief]** | Runs, params, model registry | W&B, DVC | Model registry stage semantics → we additionally gate via Approval (D-09e) |
| Package mgmt | uv | Fast, lockfile | poetry, pip-tools | — |
| Lint/format/types | ruff, mypy (strict on core) | Fast, strict | black+flake8+pyright | — |
| Architecture lint | import-linter | Enforces module layering | custom script | — |
| Logging | structlog (JSON) | Structured logs w/ context vars | stdlib logging | — |
| Settings | pydantic-settings | Typed config | dynaconf | — |
| Job queue | Postgres `SKIP LOCKED` (own thin implementation) | No new infra (D-03) | Celery+Redis, Arq, RQ | Revisit if throughput demands |
| Tracing | OpenTelemetry (optional, later) | Standard | — | Not needed before Phase 3 |
| Frontend tests | Vitest/Playwright | Typed unit + E2E | Cypress | Phase 5 |
| CI | GitHub Actions or equivalent (decide in Phase 1) | — | — | Cannot assume a remote exists yet |

## Explicit non-choices (and why)
- **No Kafka/Redis/Neo4j/vector DB in v1:** no measured need; each adds operational surface (D-03). Retrieval over cases is by structured query; embeddings may be considered in a later phase if retrieval of unstructured documents proves necessary.
- **No Kubernetes / cloud deployment:** U-08.
- **No agent framework other than LangGraph:** avoid overlapping orchestration layers.
- **No direct Anthropic SDK use outside `llm/`:** D-16.

## Version-compatibility checklist (Phase 1 gate)
1. Python 3.12 wheels for xgboost, torch, torch-geometric, scikit-learn, networkx.
2. LangGraph checkpointer for Postgres compatible with chosen psycopg/SQLAlchemy versions.
3. Anthropic SDK structured-output features required by gateway.
4. Record outcomes in decisions.md.
