// Pure aggregation over case details for the dashboard and system views. No React, no I/O.

export interface CaseLike {
  status: string;
  claims: Array<{ verification_status: string | null }>;
  runs: Array<{ status: string }>;
}

export const NONE = "NONE";

/** Count of claims by verification status across the given cases (a claim with no result counts as NONE). */
export function claimMix(cases: CaseLike[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const c of cases) for (const cl of c.claims) out[cl.verification_status ?? NONE] = (out[cl.verification_status ?? NONE] ?? 0) + 1;
  return out;
}

export function runMix(cases: CaseLike[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const c of cases) for (const r of c.runs) out[r.status] = (out[r.status] ?? 0) + 1;
  return out;
}

/** Share of claims that are VERIFIED, as a whole percent, or null when there are no claims. */
export function verifiedShare(mix: Record<string, number>): number | null {
  const total = Object.values(mix).reduce((n, v) => n + v, 0);
  return total === 0 ? null : Math.round(((mix.VERIFIED ?? 0) / total) * 100);
}

export function sum(mix: Record<string, number>): number {
  return Object.values(mix).reduce((n, v) => n + v, 0);
}
