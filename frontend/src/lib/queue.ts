// Pure queue logic: filtering, sorting, binning. No React, no I/O, so it can be unit-tested with node --test.

export interface QueueRow {
  case_id: string;
  priority_score: number;
  status: string;
}

export const STATUSES = ["OPEN", "INVESTIGATING", "PENDING_REVIEW", "CLOSED"] as const;
export type SortKey = "score_desc" | "score_asc" | "case" | "status";

export interface QueueView {
  q: string;
  status: string; // "ALL" or one of STATUSES
  sort: SortKey;
  bin: number | null; // index of a score-histogram bin picked on the chart, or null
}

export const BIN_COUNT = 12;

function range(items: QueueRow[]): { min: number; max: number } {
  let min = Infinity;
  let max = -Infinity;
  for (const c of items) {
    if (c.priority_score < min) min = c.priority_score;
    if (c.priority_score > max) max = c.priority_score;
  }
  return { min, max };
}

/** Which histogram bin a score belongs to. The same rule is used to draw the chart and to filter by a bar. */
export function binIndex(score: number, min: number, max: number, n: number): number {
  if (max === min) return 0;
  return Math.min(n - 1, Math.floor(((score - min) / (max - min)) * n));
}

const STATUS_ORDER: Record<string, number> = { OPEN: 0, INVESTIGATING: 1, PENDING_REVIEW: 2, CLOSED: 3 };

export function applyQueue(items: QueueRow[], v: QueueView): QueueRow[] {
  const q = v.q.trim().toLowerCase();
  const { min, max } = range(items);
  const out = items.filter(
    (c) =>
      (v.status === "ALL" || c.status === v.status) &&
      (q === "" || c.case_id.toLowerCase().includes(q)) &&
      (v.bin === null || binIndex(c.priority_score, min, max, BIN_COUNT) === v.bin),
  );
  const by: Record<SortKey, (a: QueueRow, b: QueueRow) => number> = {
    score_desc: (a, b) => b.priority_score - a.priority_score || a.case_id.localeCompare(b.case_id),
    score_asc: (a, b) => a.priority_score - b.priority_score || a.case_id.localeCompare(b.case_id),
    case: (a, b) => a.case_id.localeCompare(b.case_id),
    status: (a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || b.priority_score - a.priority_score,
  };
  return out.sort(by[v.sort]);
}

export function statusCounts(items: QueueRow[]): Record<string, number> {
  const out: Record<string, number> = { ALL: items.length };
  for (const c of items) out[c.status] = (out[c.status] ?? 0) + 1;
  return out;
}

export interface Bin {
  lo: number;
  hi: number;
  total: number;
  counts: Record<string, number>;
}

/** Equal-width bins over the observed score range. Every item lands in exactly one bin; the top edge goes to the last bin. */
export function histogram(items: QueueRow[], n = BIN_COUNT): Bin[] {
  if (!items.length || n < 1) return [];
  const { min, max } = range(items);
  if (max === min) {
    const counts: Record<string, number> = {};
    for (const c of items) counts[c.status] = (counts[c.status] ?? 0) + 1;
    return [{ lo: min, hi: max, total: items.length, counts }];
  }
  const w = (max - min) / n;
  const bins: Bin[] = Array.from({ length: n }, (_, i) => ({ lo: min + i * w, hi: i === n - 1 ? max : min + (i + 1) * w, total: 0, counts: {} }));
  for (const c of items) {
    const i = binIndex(c.priority_score, min, max, n);
    bins[i].total += 1;
    bins[i].counts[c.status] = (bins[i].counts[c.status] ?? 0) + 1;
  }
  return bins;
}

/** Decimals needed so that neighbouring bin edges print differently (scores can cluster near 1.0). */
export function scoreDecimals(min: number, max: number, n = BIN_COUNT): number {
  const w = (max - min) / n;
  if (!(w > 0)) return 2;
  return Math.min(6, Math.max(2, Math.ceil(-Math.log10(w))));
}
