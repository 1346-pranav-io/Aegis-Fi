// Pure audit-log logic: filter, sort, day buckets, CSV. No React, no I/O.

export interface AuditRow {
  event_id: number;
  ts: string;
  actor_id: string;
  action: string;
  object_type: string;
  object_id: string;
}

export type AuditSort = "id" | "ts" | "actor" | "action";
export interface AuditView {
  q: string;
  actor: string; // "ALL" or an actor id
  action: string; // "ALL" or an action
  day: string; // "" or YYYY-MM-DD (local time)
  sort: AuditSort;
  dir: "asc" | "desc";
}

export function dayKey(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function applyAudit(rows: AuditRow[], v: AuditView): AuditRow[] {
  const q = v.q.trim().toLowerCase();
  const out = rows.filter(
    (r) =>
      (v.actor === "ALL" || r.actor_id === v.actor) &&
      (v.action === "ALL" || r.action === v.action) &&
      (v.day === "" || dayKey(r.ts) === v.day) &&
      (q === "" || `${r.actor_id} ${r.action} ${r.object_type} ${r.object_id}`.toLowerCase().includes(q)),
  );
  const sign = v.dir === "asc" ? 1 : -1;
  const key: Record<AuditSort, (a: AuditRow, b: AuditRow) => number> = {
    id: (a, b) => a.event_id - b.event_id,
    ts: (a, b) => Date.parse(a.ts) - Date.parse(b.ts) || a.event_id - b.event_id,
    actor: (a, b) => a.actor_id.localeCompare(b.actor_id) || a.event_id - b.event_id,
    action: (a, b) => a.action.localeCompare(b.action) || a.event_id - b.event_id,
  };
  return out.sort((a, b) => sign * key[v.sort](a, b));
}

export interface DayBucket {
  day: string;
  total: number;
}

/** One bucket per calendar day between the first and last event, so gaps show as empty days. */
export function byDay(rows: AuditRow[]): DayBucket[] {
  if (!rows.length) return [];
  const counts = new Map<string, number>();
  let min = Infinity;
  let max = -Infinity;
  for (const r of rows) {
    const t = Date.parse(r.ts);
    if (t < min) min = t;
    if (t > max) max = t;
    const k = dayKey(r.ts);
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  const out: DayBucket[] = [];
  const d = new Date(min);
  d.setHours(12, 0, 0, 0); // noon avoids DST edge cases when stepping days
  const end = new Date(max);
  end.setHours(12, 0, 0, 0);
  while (d <= end && out.length < 400) {
    const k = dayKey(d.toISOString());
    out.push({ day: k, total: counts.get(k) ?? 0 });
    d.setDate(d.getDate() + 1);
  }
  return out;
}

/** CSV with quoting, and a leading apostrophe on cells a spreadsheet would read as a formula. */
export function toCsv(header: string[], rows: string[][]): string {
  const cell = (s: string) => {
    const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
    return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  return [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
}
