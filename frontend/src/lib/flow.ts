// The investigation pipeline as ten stages, and how audit events map onto them. Pure data and functions.

export type NodeId = "ingest" | "detect" | "cases" | "runs" | "claims" | "verify" | "report" | "approval" | "audit";

export interface FlowNode {
  id: NodeId;
  title: string;
  blurb: string;
  detail: string;
  href?: string;
  live: boolean; // false: no API endpoint exposes a number for this stage
}

export const FLOW: FlowNode[] = [
  { id: "ingest", title: "Ingestion", blurb: "Source tables, insert-only", live: false, detail: "The IBM synthetic data is loaded by the ingest login into the source schema. Triggers refuse UPDATE and DELETE, and each batch has a hashed manifest. No API endpoint reports ingestion, so this stage shows no live number." },
  { id: "detect", title: "Detection", blurb: "B0 rules, B1 XGBoost", href: "/queue", live: true, detail: "Detectors score transactions; alerts for one account are grouped into a case. The number shown is the highest detector score in the queue. A score ranks cases; it is not a probability." },
  { id: "cases", title: "Cases", blurb: "Queue and status", href: "/queue", live: true, detail: "A case moves Open, Investigating, Pending review, Closed. The number is the count of cases that are not closed." },
  { id: "runs", title: "Investigation runs", blurb: "Worker and orchestrator", live: true, detail: "An analyst queues a run; the worker executes the runtime graph. With live Claude calls disabled, LLM steps end DENIED and runs finish PARTIAL. The number counts runs on the sampled cases." },
  { id: "claims", title: "Evidence and claims", blurb: "Hashed, with provenance", live: true, detail: "Deterministic capabilities write evidence; each claim cites evidence ids. The number counts claims on the sampled cases." },
  { id: "verify", title: "Verification", blurb: "Claim plus its evidence only", live: true, detail: "An independent verifier sees only the claim and its cited evidence. The number is the share of sampled claims that are VERIFIED." },
  { id: "report", title: "Report", blurb: "Verified findings only", href: "/queue", live: true, detail: "Reports contain verified findings, with unsupported claims listed as not findings. The number counts finished runs (complete or partial) on the sampled cases." },
  { id: "approval", title: "Human approval", blurb: "A second person decides", href: "/approvals", live: true, detail: "Closing a case or exporting feedback needs a stored decision from someone other than the requester. The number is the count of requests waiting." },
  { id: "audit", title: "Audit log", blurb: "Append-only record", href: "/audit", live: true, detail: "Every consequential action is written to the audit log. The number is the count of events loaded; only the auditor and admin roles can read it." },
];

/** Which pipeline stage an audit event belongs to, from its action prefix. Unknown prefixes land on the audit stage. */
export function nodeForAction(action: string): NodeId {
  const head = action.split(".")[0];
  if (head === "case") return "cases";
  if (head === "run") return "runs";
  if (head === "evidence" || head === "claim") return "claims";
  if (head === "verification") return "verify";
  if (head === "report") return "report";
  if (head === "approval" || head === "feedback") return "approval";
  return "audit";
}

/** Stages to light up for a batch of new events. The audit stage always lights, because every event is recorded there. */
export function stagesFor(actions: string[]): Set<NodeId> {
  const s = new Set<NodeId>();
  for (const a of actions) {
    s.add(nodeForAction(a));
    s.add("audit");
  }
  return s;
}
