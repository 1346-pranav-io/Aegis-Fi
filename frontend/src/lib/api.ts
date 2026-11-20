// Thin client for the Aegis-FI HTTP API (same-origin /api proxy). The token lives in
// sessionStorage only; the server verifies it on every call, so nothing here grants access.

export type Role = "analyst" | "approver" | "model_risk" | "auditor" | "admin";

export interface Session {
  token: string;
  user: string;
  roles: Role[];
  exp: number;
}

const KEY = "aegis.token";
export const UNAUTH_EVENT = "aegis:unauthorised";
export const SESSION_EVENT = "aegis:session"; // fired whenever the stored token changes

function decode(token: string): Session | null {
  try {
    const part = token.split(".")[1];
    const json = atob(part.replace(/-/g, "+").replace(/_/g, "/"));
    const c = JSON.parse(json) as { sub?: string; roles?: Role[]; exp?: number };
    if (!c.sub || !c.exp) return null;
    return { token, user: c.sub, roles: c.roles ?? [], exp: c.exp };
  } catch {
    return null;
  }
}

export function sessionFromToken(t: string | null): Session | null {
  const s = t ? decode(t) : null;
  return s && s.exp * 1000 >= Date.now() ? s : null;
}

export function getStoredToken(): string | null {
  return typeof window === "undefined" ? null : window.sessionStorage.getItem(KEY);
}

export function getSession(): Session | null {
  return sessionFromToken(getStoredToken());
}

export function setToken(token: string): Session | null {
  const s = decode(token.trim());
  if (s) {
    window.sessionStorage.setItem(KEY, token.trim());
    window.dispatchEvent(new Event(SESSION_EVENT));
  }
  return s;
}

export function signOut(): void {
  window.sessionStorage.removeItem(KEY);
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const s = getSession();
  if (!s) throw new ApiError(401, "not signed in");
  const res = await fetch(`/api${path}`, {
    method: init?.method ?? "GET",
    headers: {
      Authorization: `Bearer ${s.token}`,
      ...(init?.body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  if (res.status === 401) {
    // The token expired or was rejected: drop it and let the shell show the sign-in page with a reason.
    signOut();
    window.dispatchEvent(new Event(UNAUTH_EVENT));
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const d = (data as { detail?: unknown } | null)?.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : `request failed (${res.status})`);
  }
  return data as T;
}

// ---------------------------------------------------------------- response shapes (from the API)
export interface QueueItem {
  case_id: string;
  priority_score: number;
  status: string;
}
export interface Claim {
  claim_id: string;
  text: string;
  claim_kind: string;
  self_reported_confidence: string | null;
  verification_status: string | null;
  depends_on_er_links: string[];
}
export interface RunBrief {
  run_id: string;
  status: string;
  policy: string;
}
export interface CaseDetail {
  case_id: string;
  status: string;
  subject_ref: string;
  priority_score: number;
  assigned_to: string | null;
  opened_at: string;
  disposition: string | null;
  alerts: number;
  claims: Claim[];
  contradictions: Array<Record<string, unknown>>;
  runs: RunBrief[];
}
export interface RunStatus {
  run_id: string;
  status: string;
  reason: string | null;
  policy: string;
  steps: Array<{ agent: string; status: string }>;
}
export interface Finding {
  claim_id: string;
  text: string;
  evidence_ids: string[];
}
export interface ReportRow {
  claim_id: string;
  text: string;
  status: string;
  evidence_ids: string[];
}
export interface Report {
  report_id: string;
  version: number;
  run_id: string;
  body_hash: string;
  body: {
    case: { subject_ref: string; as_of: string };
    report_format?: { template?: string; note?: string };
    findings: {
      items: Finding[];
      narrative?: Array<{ text: string; claim_ids: string[]; evidence_ids: string[] }>;
      narrative_source?: string;
    };
    partially_supported: ReportRow[];
    not_findings: ReportRow[];
    open_contradictions: Array<Record<string, unknown>>;
    limitations: string[];
    citation_coverage?: { cited: number; total: number };
  };
}
export interface PendingApproval {
  request_id: string;
  action_type: string;
  subject_ref: string;
  requested_by: string;
  requested_at: string;
  justification: string;
}
export interface AuditEvent {
  event_id: number;
  ts: string;
  actor_id: string;
  action: string;
  object_type: string;
  object_id: string;
}
