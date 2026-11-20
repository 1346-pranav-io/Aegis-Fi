// Pure chart maths. No React, no DOM, so it can be unit-tested with node --test.

export interface Pt {
  x: number;
  y: number;
}

/** "Nice" axis ticks from 0 (or min) to a rounded-up maximum. Always returns at least two ticks. */
export function niceTicks(max: number, count = 4, min = 0, integer = false): { ticks: number[]; top: number } {
  const span = max > min ? max - min : 1; // an empty or flat series still gets a usable axis
  const raw = span / Math.max(count, 1);
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / pow;
  const nice = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
  const step = integer ? Math.max(1, Math.ceil(nice)) : nice; // counts never get fractional gridlines
  const top = Math.max(Math.ceil((max - min) / step - 1e-9) * step + min, min + step);
  const ticks: number[] = [];
  for (let v = min; v <= top + step / 2; v += step) ticks.push(Number(v.toPrecision(12)));
  return { ticks, top };
}

export function formatCompact(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e6) return `${(n / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "")}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "")}k`;
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/**
 * Smooth path through the points that never overshoots between them (monotone cubic interpolation, the same idea
 * as d3's curveMonotoneX). A count chart must not dip below zero between two zero points.
 */
export function smoothPath(p: Pt[]): string {
  const n = p.length;
  if (n === 0) return "";
  if (n === 1) return `M${p[0].x},${p[0].y}`;
  if (n === 2) return `M${p[0].x},${p[0].y}L${p[1].x},${p[1].y}`;
  const dx: number[] = [];
  const m: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(p[i + 1].x - p[i].x);
    m.push(dx[i] === 0 ? 0 : (p[i + 1].y - p[i].y) / dx[i]);
  }
  const t: number[] = new Array(n);
  t[0] = m[0];
  t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) {
      t[i] = 0;
      t[i + 1] = 0;
      continue;
    }
    const a = t[i] / m[i];
    const b = t[i + 1] / m[i];
    const s = a * a + b * b;
    if (s > 9) {
      const k = 3 / Math.sqrt(s);
      t[i] = k * a * m[i];
      t[i + 1] = k * b * m[i];
    }
  }
  let d = `M${p[0].x},${p[0].y}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += `C${p[i].x + h},${p[i].y + t[i] * h},${p[i + 1].x - h},${p[i + 1].y - t[i + 1] * h},${p[i + 1].x},${p[i + 1].y}`;
  }
  return d;
}

/** Index of the entry in a sorted array of x positions that is closest to x. */
export function nearestIndex(xs: number[], x: number): number {
  if (!xs.length) return -1;
  let lo = 0;
  let hi = xs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] < x) lo = mid + 1;
    else hi = mid;
  }
  return lo > 0 && Math.abs(xs[lo - 1] - x) <= Math.abs(xs[lo] - x) ? lo - 1 : lo;
}

/** Donut arc as an SVG path (angles in radians, 0 at 12 o'clock, clockwise). */
export function arcPath(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  const pt = (r: number, a: number) => `${(cx + r * Math.sin(a)).toFixed(3)},${(cy - r * Math.cos(a)).toFixed(3)}`;
  const big = a1 - a0 > Math.PI ? 1 : 0;
  // A full circle cannot be drawn as one arc; stop a hair short of 2*PI.
  const end = a1 - a0 >= Math.PI * 2 - 1e-6 ? a0 + Math.PI * 2 - 1e-4 : a1;
  return `M${pt(r1, a0)}A${r1},${r1} 0 ${big} 1 ${pt(r1, end)}L${pt(r0, end)}A${r0},${r0} 0 ${big} 0 ${pt(r0, a0)}Z`;
}
