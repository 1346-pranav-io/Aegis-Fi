// Pure table logic: generic sort and pagination. No React, no I/O.

export type Dir = "asc" | "desc";

/** Stable sort by a comparator; `dir` flips it. Does not mutate the input. */
export function sortBy<T>(rows: T[], cmp: ((a: T, b: T) => number) | undefined, dir: Dir): T[] {
  if (!cmp) return rows.slice();
  const sign = dir === "asc" ? 1 : -1;
  return rows
    .map((r, i) => ({ r, i }))
    .sort((a, b) => sign * cmp(a.r, b.r) || a.i - b.i)
    .map((x) => x.r);
}

export interface Page<T> {
  rows: T[];
  page: number; // 1-based, clamped into range
  pages: number;
  total: number;
  from: number; // 1-based index of the first row on the page, 0 when empty
  to: number;
}

export function paginate<T>(rows: T[], page: number, size: number): Page<T> {
  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / Math.max(size, 1)));
  const p = Math.min(Math.max(1, Math.floor(page) || 1), pages);
  const start = (p - 1) * size;
  const slice = rows.slice(start, start + size);
  return { rows: slice, page: p, pages, total, from: total === 0 ? 0 : start + 1, to: start + slice.length };
}

export function compareText(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
}
