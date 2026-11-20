// Static description of the backend architecture, copied from docs/architecture/SYSTEM_ARCHITECTURE.md (section 2) and
// the database roles in section 4. Pure data plus two helpers, so it can be tested against the documented
// dependency rule: common -> domain -> store/analytics/llm -> entities/evidence/ml -> capabilities -> runtime -> services -> api.

export interface Mod {
  id: string;
  rank: number; // lower layers may not import higher ones
  job: string;
  mayUse: string[];
  mustNot: string;
}

export const MODULES: Mod[] = [
  { id: "common", rank: 0, job: "IDs, time, configuration, logging, errors, base Pydantic types.", mayUse: [], mustNot: "import any other aegis module" },
  { id: "domain", rank: 1, job: "Pydantic domain models (transactions, entities, cases, evidence, claims). They are the data contracts.", mayUse: ["common"], mustNot: "do any I/O" },
  { id: "store", rank: 2, job: "SQLAlchemy models, repositories, migrations, database roles, job queue.", mayUse: ["domain", "common"], mustNot: "contain business rules" },
  { id: "analytics", rank: 2, job: "Pure deterministic functions: transaction metrics, graph metrics, behaviour deviation, typology rules.", mayUse: ["domain", "common"], mustNot: "touch the database or the network (inputs are passed in)" },
  { id: "llm", rank: 2, job: "The only gateway to a language model: pseudonymisation, budgets, retries, schema-checked output, record and replay. Live calls are disabled (D-29).", mayUse: ["common", "domain"], mustNot: "be imported by anything except the capabilities that need an LLM" },
  { id: "ingestion", rank: 3, job: "Dataset adapters, validation, quarantine, hashing, batch manifests.", mayUse: ["store", "domain"], mustNot: "call an LLM, or modify an existing row" },
  { id: "entities", rank: 3, job: "Entity resolution: blocking, scoring, link proposals, reversible merges.", mayUse: ["domain", "store"], mustNot: "auto-merge on a fuzzy match" },
  { id: "evidence", rank: 3, job: "Evidence and claim creation, content hashing, provenance walk.", mayUse: ["domain", "store"], mustNot: "modify stored evidence" },
  { id: "ml", rank: 3, job: "Features, training, calibration, the versioned model registry, graph snapshots, scoring.", mayUse: ["analytics", "domain", "store"], mustNot: "silently load an unversioned model artefact" },
  { id: "capabilities", rank: 4, job: "The nine agent-interface implementations: orchestrator, transaction, graph, behaviour, intelligence, investigation, verification, contradiction, reporting.", mayUse: ["analytics", "entities", "evidence", "ml", "llm", "store"], mustNot: "write to the source schema, or call each other directly" },
  { id: "runtime", rank: 5, job: "LangGraph graphs, orchestration policies, tool gateway, checkpointing. The single owner of control flow.", mayUse: ["capabilities", "store", "evidence"], mustNot: "contain domain logic" },
  { id: "services", rank: 6, job: "Use-cases: open a case, request an approval, record feedback, build a report.", mayUse: ["runtime", "store", "evidence", "entities"], mustNot: "be imported by capabilities" },
  { id: "api", rank: 7, job: "FastAPI routes, authentication and role checks, request and response models.", mayUse: ["services"], mustNot: "contain business logic" },
];

export interface DbRole {
  id: string;
  rights: string;
}

export const DB_ROLES: DbRole[] = [
  { id: "aegis_migrator", rights: "DDL only; used by migrations." },
  { id: "aegis_ingest", rights: "INSERT and SELECT on the source schema; used only by ingestion." },
  { id: "aegis_app", rights: "SELECT on source; INSERT and SELECT elsewhere, with controlled UPDATE on state tables such as case and job. No UPDATE or DELETE on source, evidence or audit." },
  { id: "aegis_approver", rights: "A separate login the API uses for approver-only writes such as approval decisions, so the application login cannot approve (docs/API.md)." },
  { id: "aegis_reviewer", rights: "A separate login the API uses for reviewer writes such as second reviews of feedback (docs/API.md)." },
  { id: "aegis_readonly", rights: "SELECT for analytics and evaluation." },
];

export const SCHEMAS = ["source (immutable)", "derived", "evidence", "workflow", "audit", "ml"];

/** Violations of the documented rule that a module may only use modules in lower layers. Empty means consistent. */
export function dependencyViolations(mods: Mod[] = MODULES): string[] {
  const rank = new Map(mods.map((m) => [m.id, m.rank]));
  const out: string[] = [];
  for (const m of mods) {
    for (const d of m.mayUse) {
      const r = rank.get(d);
      if (r === undefined) out.push(`${m.id} lists unknown module ${d}`);
      else if (r >= m.rank) out.push(`${m.id} (layer ${m.rank}) may not use ${d} (layer ${r})`);
    }
  }
  return out;
}

export function layers(mods: Mod[] = MODULES): Mod[][] {
  const ranks = [...new Set(mods.map((m) => m.rank))].sort((a, b) => b - a); // top layer first
  return ranks.map((r) => mods.filter((m) => m.rank === r));
}
