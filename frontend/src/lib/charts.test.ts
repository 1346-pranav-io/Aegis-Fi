import { test } from "node:test";
import assert from "node:assert/strict";
import { arcPath, formatCompact, nearestIndex, niceTicks, smoothPath } from "./charts.ts";
import { bucketEvents, bucketWidth, categoryOf, newSince, periodDelta, rangeBounds } from "./timeseries.ts";
import { compareText, paginate, sortBy } from "./table.ts";

test("niceTicks: round numbers, covers the max, starts at 0", () => {
  const { ticks, top } = niceTicks(37, 4);
  assert.equal(ticks[0], 0);
  assert.ok(top >= 37);
  assert.ok(ticks.every((t) => Number.isInteger(t)));
  assert.deepEqual(niceTicks(0).ticks.length >= 2, true);
});
test("niceTicks: integer mode never produces fractional ticks", () => {
  assert.ok(niceTicks(1, 4, 0, true).ticks.every(Number.isInteger));
  assert.ok(niceTicks(0, 4, 0, true).ticks.every(Number.isInteger));
  assert.ok(niceTicks(7, 4, 0, true).top >= 7);
});
test("formatCompact", () => {
  assert.equal(formatCompact(950), "950");
  assert.equal(formatCompact(1200), "1.2k");
  assert.equal(formatCompact(25000), "25k");
  assert.equal(formatCompact(2_500_000), "2.5M");
});
test("smoothPath: handles 0, 1, 2 points, and stays between neighbouring values", () => {
  assert.equal(smoothPath([]), "");
  assert.equal(smoothPath([{ x: 0, y: 5 }]), "M0,5");
  assert.equal(smoothPath([{ x: 0, y: 5 }, { x: 10, y: 7 }]), "M0,5L10,7");
  // Monotone: for flat-then-rise data the control points must not go below the flat value (y grows downward in SVG, so
  // "below zero" would be a y larger than the baseline).
  const d = smoothPath([{ x: 0, y: 100 }, { x: 10, y: 100 }, { x: 20, y: 40 }]);
  const nums = [...d.matchAll(/-?\d+(?:\.\d+)?/g)].map((m) => Number(m[0]));
  const ys = nums.filter((_, i) => i % 2 === 1); // numbers come in x,y pairs
  assert.ok(ys.every((y) => y <= 100.0001 && y >= 39.9999));
});
test("nearestIndex", () => {
  assert.equal(nearestIndex([], 3), -1);
  assert.equal(nearestIndex([0, 10, 20], 4), 0);
  assert.equal(nearestIndex([0, 10, 20], 6), 1);
  assert.equal(nearestIndex([0, 10, 20], 99), 2);
});
test("arcPath: full circle does not collapse", () => {
  assert.ok(arcPath(50, 50, 30, 40, 0, Math.PI * 2).includes("A"));
});

const T0 = Date.parse("2026-11-13T12:00:00Z");
const ev = (offsetH: number, action = "case.closed") => ({ ts: new Date(T0 - offsetH * 3600_000).toISOString(), action });

test("categoryOf", () => {
  assert.equal(categoryOf("case.closed"), "case");
  assert.equal(categoryOf("approval.requested"), "approval");
  assert.equal(categoryOf("weird"), "other");
});
test("bucketWidth stays within the max bucket count", () => {
  for (const spanH of [1, 24, 24 * 7, 24 * 30, 24 * 400]) {
    const w = bucketWidth(0, spanH * 3600_000, 30);
    assert.ok(Math.ceil((spanH * 3600_000) / w) <= 30 || w === 7 * 24 * 3600_000);
  }
});
test("bucketEvents: counts every in-range event once and keeps empty buckets", () => {
  const events = [ev(1), ev(2, "run.queued"), ev(30), ev(50)];
  const { from, to } = rangeBounds("7d", T0, events);
  const b = bucketEvents(events, from, to);
  assert.equal(b.reduce((n, x) => n + x.total, 0), 4);
  assert.ok(b.some((x) => x.total === 0));
  assert.deepEqual(bucketEvents([ev(500)], T0 - 24 * 3600_000, T0).reduce((n, x) => n + x.total, 0), 0);
});
test("periodDelta compares with the previous equal window", () => {
  const events = [ev(1), ev(2), ev(3), ev(25), ev(26)];
  const d = periodDelta(events, T0 - 24 * 3600_000, T0);
  assert.equal(d.cur, 3);
  assert.equal(d.prev, 2);
  assert.equal(Math.round(d.pct ?? 0), 50);
  assert.equal(periodDelta([ev(1)], T0 - 24 * 3600_000, T0).pct, null);
});
test("rangeBounds all: starts at the first event, at least an hour back", () => {
  const { from, to } = rangeBounds("all", T0, [ev(100)]);
  assert.equal(to, T0);
  assert.equal(from, T0 - 100 * 3600_000);
  assert.equal(rangeBounds("all", T0, []).from, T0 - 3600_000);
});
test("newSince returns newer events in order", () => {
  const rows = [{ event_id: 5 }, { event_id: 3 }, { event_id: 9 }];
  assert.deepEqual(newSince(rows, 4).map((r) => r.event_id), [5, 9]);
});
test("table: stable sort, direction, no mutation", () => {
  const rows = [{ n: 2, k: "a" }, { n: 1, k: "b" }, { n: 2, k: "c" }];
  const copy = JSON.stringify(rows);
  assert.deepEqual(sortBy(rows, (a, b) => a.n - b.n, "asc").map((r) => r.k), ["b", "a", "c"]);
  assert.deepEqual(sortBy(rows, (a, b) => a.n - b.n, "desc").map((r) => r.k), ["a", "c", "b"]);
  assert.equal(JSON.stringify(rows), copy);
  assert.equal(compareText("case_2", "case_10") < 0, true);
});
test("paginate: clamps, reports ranges, handles empty", () => {
  const rows = Array.from({ length: 23 }, (_, i) => i);
  assert.deepEqual(paginate(rows, 1, 10).rows, rows.slice(0, 10));
  const last = paginate(rows, 99, 10);
  assert.equal(last.page, 3);
  assert.equal(last.rows.length, 3);
  assert.equal(last.from, 21);
  assert.equal(last.to, 23);
  const e = paginate([], 1, 10);
  assert.deepEqual([e.pages, e.from, e.to, e.page], [1, 0, 0, 1]);
});

import { dependencyViolations, layers, MODULES } from "./architecture.ts";
import { FLOW, nodeForAction, stagesFor } from "./flow.ts";
import { claimMix, runMix, verifiedShare } from "./aggregate.ts";
import { initials, routeMeta, sessionLeft } from "./nav.ts";

test("architecture: documented dependency rule holds for every module", () => {
  assert.deepEqual(dependencyViolations(), []);
  assert.ok(layers().length >= 6);
  assert.ok(MODULES.some((m) => m.id === "llm"));
  // The checker must actually catch a violation.
  assert.equal(dependencyViolations([{ id: "a", rank: 1, job: "", mayUse: ["b"], mustNot: "" }, { id: "b", rank: 2, job: "", mayUse: [], mustNot: "" }]).length, 1);
});
test("flow: events map to stages and always light the audit stage", () => {
  assert.equal(nodeForAction("case.closed"), "cases");
  assert.equal(nodeForAction("approval.requested"), "approval");
  assert.equal(nodeForAction("feedback.recorded"), "approval");
  assert.equal(nodeForAction("something.else"), "audit");
  assert.deepEqual([...stagesFor(["run.queued"])].sort(), ["audit", "runs"]);
  assert.equal(new Set(FLOW.map((n) => n.id)).size, FLOW.length);
});
test("aggregate: claim and run mixes, verified share", () => {
  const cases = [
    { status: "OPEN", claims: [{ verification_status: "VERIFIED" }, { verification_status: null }], runs: [{ status: "PARTIAL" }] },
    { status: "OPEN", claims: [{ verification_status: "VERIFIED" }, { verification_status: "UNSUPPORTED" }], runs: [{ status: "PARTIAL" }, { status: "COMPLETE" }] },
  ];
  assert.deepEqual(claimMix(cases), { VERIFIED: 2, NONE: 1, UNSUPPORTED: 1 });
  assert.deepEqual(runMix(cases), { PARTIAL: 2, COMPLETE: 1 });
  assert.equal(verifiedShare(claimMix(cases)), 50);
  assert.equal(verifiedShare({}), null);
});
test("nav: route metadata, session time left, initials", () => {
  assert.equal(routeMeta("/").title, "Overview");
  assert.deepEqual(routeMeta("/cases/case_1").crumbs, [{ label: "Cases", href: "/queue" }, { label: "case_1" }]);
  assert.equal(routeMeta("/nope").section, "other");
  assert.equal(sessionLeft(1000 + 1800, 1000 * 1000), "30 min");
  assert.equal(sessionLeft(10, 1000 * 1000), "expired");
  assert.equal(initials("alice.smith"), "AS");
  assert.equal(initials("you"), "Y");
});
