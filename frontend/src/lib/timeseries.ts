// Pure time-series logic for the dashboard: ranges, buckets, period comparison. No React, no I/O.

export interface Ev {
  ts: string;
  action: string;
}

export type RangeKey = "24h" | "7d" | "30d" | "all";
export const RANGES: Array<{ key: RangeKey; label: string }> = [
  { key: "24h", label: "24 hours" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "all", label: "All" },
];

const H = 3600_000;
const D = 24 * H;

export function rangeBounds(range: RangeKey, now: number, events: Ev[]): { from: number; to: number } {
  if (range === "24h") return { from: now - D, to: now };
  if (range === "7d") return { from: now - 7 * D, to: now };
  if (range === "30d") return { from: now - 30 * D, to: now };
  let first = now;
  for (const e of events) first = Math.min(first, Date.parse(e.ts));
  return { from: Math.min(first, now - H), to: now };
}

/** Event category: the part of the action before the first dot ("case.closed" is "case"). */
export function categoryOf(action: string): string {
  const head = action.split(".")[0];
  return ["case", "run", "approval", "feedback", "evidence", "report"].includes(head) ? head : "other";
}

const STEPS = [H, 3 * H, 6 * H, 12 * H, D, 2 * D, 7 * D];

/** Smallest bucket width from a fixed list that keeps the range at or below `max` buckets. */
export function bucketWidth(from: number, to: number, max = 30): number {
  const span = Math.max(to - from, H);
  return STEPS.find((s) => Math.ceil(span / s) <= max) ?? STEPS[STEPS.length - 1];
}

export interface Bucket {
  start: number; // ms
  end: number;
  total: number;
  by: Record<string, number>;
}

/** Equal-width buckets covering [from, to]; events outside the range are ignored. Empty buckets are kept. */
export function bucketEvents(events: Ev[], from: number, to: number, max = 30): Bucket[] {
  const w = bucketWidth(from, to, max);
  const first = Math.floor(from / w) * w;
  const out: Bucket[] = [];
  for (let s = first; s <= to; s += w) out.push({ start: s, end: s + w, total: 0, by: {} });
  for (const e of events) {
    const t = Date.parse(e.ts);
    if (!(t >= from && t <= to)) continue;
    const b = out[Math.min(out.length - 1, Math.floor((t - first) / w))];
    const c = categoryOf(e.action);
    b.total += 1;
    b.by[c] = (b.by[c] ?? 0) + 1;
  }
  return out;
}

export interface Delta {
  cur: number;
  prev: number;
  pct: number | null; // null when there is no previous period to compare with
}

/** Events in [from, to] against the equal-length window before it. */
export function periodDelta(events: Ev[], from: number, to: number): Delta {
  const span = to - from;
  let cur = 0;
  let prev = 0;
  for (const e of events) {
    const t = Date.parse(e.ts);
    if (t >= from && t <= to) cur += 1;
    else if (t >= from - span && t < from) prev += 1;
  }
  return { cur, prev, pct: prev === 0 ? null : ((cur - prev) / prev) * 100 };
}

/** Live-feed pulses: which categories have events newer than `since` (an event_id high-water mark). */
export function newSince<T extends { event_id: number }>(rows: T[], since: number): T[] {
  return rows.filter((r) => r.event_id > since).sort((a, b) => a.event_id - b.event_id);
}
