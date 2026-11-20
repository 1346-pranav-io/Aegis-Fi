// Pure claim-table logic: filter and sort. No React, no I/O.

export interface ClaimRow {
  claim_id: string;
  text: string;
  claim_kind: string;
  self_reported_confidence: string | null;
  verification_status: string | null;
  depends_on_er_links: string[];
}

export type ClaimSort = "status" | "text" | "confidence";
export const NONE = "NONE"; // a claim with no verification result yet

// Most worrying first, so problems surface at the top of a status sort.
const ORDER: Record<string, number> = { CONTRADICTED: 0, UNSUPPORTED: 1, UNVERIFIABLE: 2, PARTIALLY_SUPPORTED: 3, VERIFIED: 4, [NONE]: 5 };
const CONF: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };

export const statusOf = (c: ClaimRow): string => c.verification_status ?? NONE;

export function applyClaims(claims: ClaimRow[], q: string, status: string, sort: ClaimSort): ClaimRow[] {
  const needle = q.trim().toLowerCase();
  const out = claims.filter(
    (c) => (status === "ALL" || statusOf(c) === status) && (needle === "" || `${c.text} ${c.claim_id}`.toLowerCase().includes(needle)),
  );
  const by: Record<ClaimSort, (a: ClaimRow, b: ClaimRow) => number> = {
    status: (a, b) => (ORDER[statusOf(a)] ?? 9) - (ORDER[statusOf(b)] ?? 9) || a.text.localeCompare(b.text),
    text: (a, b) => a.text.localeCompare(b.text),
    confidence: (a, b) => (CONF[a.self_reported_confidence ?? ""] ?? 9) - (CONF[b.self_reported_confidence ?? ""] ?? 9) || a.text.localeCompare(b.text),
  };
  return out.sort(by[sort]);
}

export function claimStatusCounts(claims: ClaimRow[]): Record<string, number> {
  const out: Record<string, number> = { ALL: claims.length };
  for (const c of claims) out[statusOf(c)] = (out[statusOf(c)] ?? 0) + 1;
  return out;
}
