# Aegis-FI

**Adaptive Evidence-Grounded Intelligence System for End-to-End Financial Crime Investigation.**
Primary domain: anti-money-laundering (AML) investigation. Secondary domain: payment fraud (designed for, not built).

Aegis-FI is a local research and engineering system. It scores transactions, groups alerts into cases, investigates each case with deterministic analytics, turns the results into claims that cite stored evidence, verifies each claim independently, and keeps a human in control of every consequential decision.

> **Read this first.** The data is a public *synthetic* AML dataset (IBM HI-Small). Nothing here is evidence about real laundering or real institutions. Live LLM calls are **disabled**; where an LLM step exists, a labelled stub stands in. The FIU-IND report template is a **placeholder**. See [Status and limits](#9-status-and-limits).

---

## Contents
1. [What problem it addresses](#1-what-problem-it-addresses)
2. [How it works](#2-how-it-works)
3. [Architecture](#3-architecture)
4. [Implementation](#4-implementation)
5. [The web interface](#5-the-web-interface)
6. [Research results](#6-research-results)
7. [Quick start](#7-quick-start)
8. [Repository map](#8-repository-map)
9. [Status and limits](#9-status-and-limits)
10. [Further reading](#10-further-reading)

---

## 1. What problem it addresses

Banks flag suspicious accounts with detectors. A human investigator must then decide whether a flag deserves a report to the regulator. That work is slow, and the reasoning behind a decision is rarely recorded in a form anyone can check.

Aegis-FI explores one idea: **an automated investigation is only trustworthy if every statement it makes can be traced to stored evidence, checked independently, and approved by a person.** The system is built around that rule.

## 2. How it works

```
IBM synthetic data -> ingestion (insert-only) -> source tables (immutable)
                                                      |
                              detectors score transactions (B0 rules, B1 XGBoost)
                                                      |
                                        alerts -> case (OPEN)
                                                      |
        analyst: "Run investigation" -> run queued -> worker executes the run
                                                      |
  deterministic capabilities (transaction, graph, behaviour) -> evidence (hashed, with provenance)
                                                      |
                         claims, each citing evidence ids
                                                      |
        verifier sees ONLY the claim and its cited evidence -> verified / partly / unsupported / contradicted
                                                      |
                 report: verified findings only, plus "not findings", contradictions, limitations
                                                      |
  analyst: Start investigating -> Send to review -> Request closure -> a second person approves -> CLOSED
                                                      |
                                every step is written to an append-only audit log
```

Key points:

- **Detect.** A rule baseline (B0) and an XGBoost model (B1) score transactions. Alerts for one account become a *case*. A score ranks cases; it is **not a probability**.
- **Investigate.** Deterministic analytics (transaction patterns, graph structure, behaviour against a baseline) write *evidence*: stored, content-hashed, with provenance.
- **Claim and verify.** Each claim cites evidence ids. A separate verifier sees only the claim and its cited evidence. Claims that fail never appear as findings in the report.
- **Decide.** Closing a case, requesting a filing, exporting feedback for training, merging non-identical entities and activating a model all need a stored decision by a person other than the requester.
- **Time.** Every analysis carries an `as_of` (what the system knew then). Lookback windows end at the case's *event anchor* (the end of its alert window), so cases built on historical data still get evidence.

### Non-negotiable invariants
1. Source financial records are insert-only (enforced in the database). LLMs and agents have no write path to them.
2. Every claim cites evidence ids; evidence carries provenance and a content hash; unsupported claims never appear as report findings.
3. Verification is independent: the verifier sees only the claim and its cited evidence.
4. A score is not a probability. `calibrated_probability` is null unless a registered calibration artefact exists. LLM confidence is ordinal and labelled self-reported.
5. Consequential actions need a stored human approval.
6. A deterministic function is tried before an agent, and an agent before an LLM. No microservices.
7. All LLM calls go through the `llm/` gateway (pseudonymised, budgeted, recorded). The Anthropic SDK is imported nowhere else.
8. `as_of` on every analysis; temporal splits for research results.
9. Nothing is fabricated: unverified means the document says unverified.

## 3. Architecture

A **modular monolith**: one Python package, two processes (API and worker), PostgreSQL as the only datastore, MLflow (SQLite) for model artefacts.

```
            +-------------------+
            | Next.js web UI    |
            +---------+---------+
                      | HTTPS/JSON (same-origin proxy)
+---------------------v---------------------------------------------+
| API process (FastAPI)   api/ : auth, role checks, routes, DTOs     |
+----------+----------------------------------------+---------------+
           | calls services                         | enqueue job
+----------v-------------------------+     +---------v--------------+
| services/  use-cases               |     | Worker process         |
| cases, approvals, feedback, reports|     | runtime/ (LangGraph,   |
+----------+-------------------------+     | orchestration)         |
           |                                +---------+--------------+
+----------v-----------------------------------------v--------------+
| capabilities/  nine agent-interface modules                        |
+---+-------------+---------------+---------------+-----------------+
    |             |               |               |
+---v----+  +-----v-----+  +------v------+  +-----v------+
|analytics| | entities  |  | evidence    |  | llm/ gateway|
|pure fns | | resolution|  | store+prov. |  | (disabled)  |
+---+----+  +-----+-----+  +------+------+  +-------------+
    +-------------+---------------+
                  v
           +-------------+
           | store/  PostgreSQL |  <-- ml/ (training, registry)
           +-------------+
```

### Layering rule
`common -> domain -> store / analytics / llm -> entities / evidence / ml -> capabilities -> runtime -> services -> api`

A module may only import modules in lower layers. `import-linter` enforces five contracts. Capabilities never call each other; only `runtime` composes them.

| Module | Job |
|---|---|
| `common`, `domain` | IDs, time, config, errors; Pydantic models that act as data contracts |
| `store` | SQLAlchemy models, repositories, migrations, database roles, job queue |
| `ingestion` | Dataset adapters, validation, quarantine, hashing, batch manifests |
| `analytics` | Pure functions: transaction metrics, graph metrics, behaviour deviation, typology rules |
| `entities` | Entity resolution: blocking, scoring, link proposals, reversible merges |
| `evidence` | Evidence and claim creation, content hashing, provenance walk |
| `ml` | Features, training, calibration, model registry, graph snapshots, scoring |
| `llm` | The only place a model may be called: pseudonymisation, budgets, record and replay |
| `capabilities` | Orchestrator, transaction, graph, behaviour, intelligence, investigation, verification, contradiction, reporting |
| `runtime` | LangGraph graphs, orchestration policies, tool gateway, checkpointing |
| `services` | Use-cases: open a case, request an approval, record feedback, build a report |
| `api` | FastAPI routes, authentication and role checks |
| `eval` | Metrics, temporal splits, bootstrap, drift measures |

### Database and access control
- Schemas: `source` (immutable), `derived`, `evidence`, `workflow`, `audit`, `ml`.
- Separate logins (`aegis_app`, `aegis_ingest`, `aegis_approver`, `aegis_reviewer`) hold only the rights they need.
- Insert-only tables are protected by triggers, so even application code cannot update or delete them.
- Migrations are hash-approved: each file's SHA-256 must match an entry in `backend/migrations/APPROVALS.json`.
- Closing a case is gated **by the database** on an approved decision made by a different person than the requester.

### Roles
| Role | May |
|---|---|
| analyst | Read cases, change status, submit runs, request closure, give feedback |
| approver | Everything an analyst may, plus decide approvals and second-review feedback |
| auditor | Read, plus the audit log |
| model_risk | Request and perform a validated feedback export (needs an approved decision) |
| admin | Read, audit log, feedback export |

The person recorded everywhere is the token subject; no request body can carry an identity.

## 4. Implementation

| Area | Technology |
|---|---|
| Backend | Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2, psycopg 3, PyJWT |
| Data store | PostgreSQL 16 (Docker), MLflow with SQLite |
| Analytics and ML | NumPy, pandas, scikit-learn, XGBoost, NetworkX; optional PyTorch and PyTorch Geometric (GNN experiment) |
| Agent runtime | LangGraph |
| Quality gates | ruff, mypy (strict), pytest, import-linter, structlog JSON logs |
| Frontend | Next.js, React, TypeScript, Motion, Lucide icons, hand-written SVG charts |

### HTTP API
`GET /health` (public), `GET /queue`, `GET /cases/{id}`, `POST /cases/{id}/transition`, `POST /cases/{id}/runs` (202, asynchronous), `GET /runs/{id}`, `POST /cases/{id}/closure-requests`, `GET /approvals/pending`, `POST /approvals/{id}/decision`, `POST /cases/{id}/close`, `GET /cases/{id}/report?format=json|markdown`, `POST /feedback`, `POST /feedback/{id}/second-review`, `POST /feedback/export-requests`, `POST /feedback/export`, `POST /contradictions/{id}/status`, `POST /er-links/{id}/decision`, `GET /audit`.

The contract is the generated OpenAPI document, guarded by a snapshot test. Details: [`docs/API.md`](docs/API.md).

### Command line
From `backend/`: `uv run python -m aegis.cli dev-token | serve | worker | report`.

## 5. The web interface

Built in `frontend/`. It calls only the existing API through a same-origin proxy and adds no endpoint.

- **Overview:** KPI cards, an interactive activity chart with a time range, status and evidence-quality donuts, a score histogram, quick actions.
- **Cases and Case:** searchable, sortable, paginated tables; a report grouped by finding type; claims with search, filter and expandable details; confirmation dialogs that collect a reason.
- **Approvals:** approve or deny with a reason; your own requests show the actions disabled and say why.
- **Audit log:** search, filters, sortable table, events-per-day chart, CSV export.
- **System:** a live data-flow diagram of the pipeline with real numbers and an API health check, plus an interactive map of the backend layers.
- **Settings:** theme, refresh interval, sidebar.
- **Everywhere:** collapsible sidebar, command palette (`Ctrl+K`), keyboard shortcuts (`?`), light and dark themes, motion that respects reduced-motion settings.

"Live" means polling the existing endpoints (default every 10 seconds), not push. More detail: [`frontend/README.md`](frontend/README.md).

## 6. Research results

All results are model-free (no LLM). Every number is traceable to a stored result file through `docs/research/RESULTS_INDEX.json`, checked by `backend/scripts/check_results_trace.py`.

| Experiment | Result | Reading |
|---|---|---|
| E1 detection baselines | B0 rules PR-AUC 0.00122; B1 XGBoost 0.4901 +/- 0.0102 across seeds; B1 intrinsic features only 0.0535 | The model beats the rules widely; most of its signal is account and pair history |
| E8 short-horizon temporal | B1 mean PR-AUC 0.4090; 0.4435 at horizon 1 and 0.3060 at horizon 7 | **No degradation can be claimed**: horizon is confounded with training size |
| E9 GNN comparison | GraphSAGE 0.4677 +/- 0.0109 against B1 0.4901 +/- 0.0102 | The condition for replacing XGBoost was not met; XGBoost stays |
| E5 entity resolution | Silent propagation 0.0000 in every cell | **Did not support the research claim**; narrowed to a design property |
| E6 contradictions | Perfect on a seeded set | By construction; says nothing about natural data |
| E2 to E4 | Run on a stub and fixtures only | Say nothing about a real model |
| E7 feedback loop | **Not run** | Not authorised |

Caveats: one synthetic dataset, row bootstraps that understate noise (laundering rows cluster), seed variation of about 0.02 PR-AUC, no multiple-comparison correction. Full list: [`docs/research/LIMITATIONS.md`](docs/research/LIMITATIONS.md).

## 7. Quick start

Prerequisites: Docker Desktop (running), [uv](https://docs.astral.sh/uv/), and a current Node.js.

**Backend**
```bash
cd backend
uv sync
uv run python scripts/dev.py up        # start local PostgreSQL
uv run python scripts/dev.py migrate
```
Ingest the IBM data (not included in the repository; see the script's docstring for where to put it):
```bash
uv run python scripts/ingest_ibm.py
```
Start the API. It needs `AEGIS_API_JWT_KEY` (at least 32 bytes), `AEGIS_API_JWT_ISSUER` and the database URLs; the variable names are listed in [`docs/API.md`](docs/API.md).
```bash
uv run python -m aegis.cli serve --port 8000
```
Create a local development token and (optionally) process queued runs with the labelled stub:
```bash
uv run python -m aegis.cli dev-token --user you --roles analyst approver auditor
uv run python -m aegis.cli worker --once --llm stub
```

**Frontend**
```bash
cd frontend
npm install
npm run build
npm run start        # http://127.0.0.1:3000
```
Paste the token on the sign-in page.

**Quality checks**
```bash
cd backend && uv run python scripts/dev.py check   # lint, types, architecture, unit tests
cd frontend && npm run check                       # types, ESLint, unit tests
```

## 8. Repository map

```
backend/src/aegis/   the Python package (section 3)
backend/migrations/  SQL migrations and APPROVALS.json
backend/scripts/     dev.py, ingest_ibm.py, demo_seed.py, repro_check.py, check_results_trace.py
backend/tests/       unit and integration tests
frontend/            the web UI
experiments/         experiment scripts, configs, stored results
docs/                architecture, requirements, API, research, phase exit reviews
decisions.md         every major decision, with status and reason
Project_description.md  long-form description of the whole project
CLAUDE.md            project rules for AI assistants working in the repository
```

## 9. Status and limits

Built in seven phases, each closed by an owner decision and an exit review: platform and data foundation, analytics and detection, evidence layer and agent runtime, API with role checks and human feedback, model-free evaluation, GNN comparison and hardening, then the web UI.

- The data is one synthetic dataset. Do not read any result as a statement about real-world laundering.
- Live Claude calls are disabled. Anything LLM-dependent has only been run against a stub, so measured behaviour describes the harness, not a model.
- The FIU-IND STR report template is a placeholder; the official format was not verified from its source, and every report says so.
- E7 (human feedback improving a model) was not run, and E5's research claim was not supported.
- No deployment, load testing or real identity provider. One API process holds all database logins, there is no rate limiting, `/docs` is unauthenticated, and the dev token lives in `sessionStorage`.
- Security reviews were AI reviewer passes with no human review or penetration test.
- Reproduction from a clean checkout has not been performed beyond one re-run; see [`docs/REPRODUCE.md`](docs/REPRODUCE.md).
- UI: evidence appears as ids only (no endpoint returns its content); automated tests cover the pure logic, not the components; accessibility had a structural scan only.

## 10. Further reading

- [`Project_description.md`](Project_description.md): the whole project in one document, including the QA record
- [`decisions.md`](decisions.md): why each choice was made (D-01 to D-37)
- [`docs/architecture/SYSTEM_ARCHITECTURE.md`](docs/architecture/SYSTEM_ARCHITECTURE.md), `AGENT_ARCHITECTURE.md`, `EVIDENCE_MODEL.md`
- [`docs/API.md`](docs/API.md), [`docs/DEVELOPMENT_STANDARDS.md`](docs/DEVELOPMENT_STANDARDS.md), [`docs/TESTING_STRATEGY.md`](docs/TESTING_STRATEGY.md)
- [`docs/research/`](docs/research): experiment write-ups and limitations

## Licence and data

No licence has been chosen yet; until one is added, all rights are reserved by the author. The IBM synthetic AML dataset is not included and has its own terms, which you should check before redistributing anything derived from it.
