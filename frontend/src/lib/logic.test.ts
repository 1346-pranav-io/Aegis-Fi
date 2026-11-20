import { test } from "node:test";
import assert from "node:assert/strict";
import { applyQueue, histogram, scoreDecimals, statusCounts, type QueueRow, type QueueView } from "./queue.ts";
import { applyAudit, byDay, dayKey, toCsv, type AuditRow, type AuditView } from "./audit.ts";
import { relTime } from "./format.ts";

const rows: QueueRow[] = [
  { case_id: "case_B", priority_score: 0.5, status: "OPEN" },
  { case_id: "case_A", priority_score: 0.9, status: "INVESTIGATING" },
  { case_id: "case_C", priority_score: 0.5, status: "CLOSED" },
  { case_id: "case_D", priority_score: 0.1, status: "OPEN" },
];
const base: QueueView = { q: "", status: "ALL", sort: "score_desc", bin: null };

test("queue: sorts by score desc with a stable id tie-break", () => {
  assert.deepEqual(applyQueue(rows, base).map((r) => r.case_id), ["case_A", "case_B", "case_C", "case_D"]);
});
test("queue: filters by status, text and score range together", () => {
  assert.deepEqual(applyQueue(rows, { ...base, status: "OPEN" }).map((r) => r.case_id), ["case_B", "case_D"]);
  assert.deepEqual(applyQueue(rows, { ...base, q: " _a" }).map((r) => r.case_id), ["case_A"]);
  // bin 6 of 12 over 0.1..0.9 covers 0.5..0.5667, which holds the two 0.5 scores
  assert.deepEqual(applyQueue(rows, { ...base, bin: 6 }).map((r) => r.case_id), ["case_B", "case_C"]);
});
test("queue: sort by status uses workflow order, not alphabet", () => {
  const s = applyQueue(rows, { ...base, sort: "status" }).map((r) => r.status);
  assert.deepEqual(s, ["OPEN", "OPEN", "INVESTIGATING", "CLOSED"]);
});
test("queue: does not mutate its input", () => {
  const copy = JSON.stringify(rows);
  applyQueue(rows, { ...base, sort: "case" });
  assert.equal(JSON.stringify(rows), copy);
});
test("queue: status counts", () => {
  assert.deepEqual(statusCounts(rows), { ALL: 4, OPEN: 2, INVESTIGATING: 1, CLOSED: 1 });
});
test("histogram: every item lands in exactly one bin, including the maximum", () => {
  const bins = histogram(rows, 4);
  assert.equal(bins.length, 4);
  assert.equal(bins.reduce((n, b) => n + b.total, 0), rows.length);
  assert.equal(bins[3].total, 1); // 0.9 is in the top bin
});
test("histogram and bin filter agree for every bin", () => {
  const bins = histogram(rows);
  bins.forEach((b, i) => assert.equal(applyQueue(rows, { ...base, bin: i }).length, b.total));
});
test("histogram: empty input, and all-equal scores, are handled", () => {
  assert.deepEqual(histogram([], 5), []);
  const one = histogram([{ case_id: "x", priority_score: 0.3, status: "OPEN" }, { case_id: "y", priority_score: 0.3, status: "OPEN" }], 5);
  assert.equal(one.length, 1);
  assert.equal(one[0].total, 2);
});

const audit: AuditRow[] = [
  { event_id: 3, ts: "2026-11-13T10:00:00", actor_id: "bob", action: "CASE_CLOSED", object_type: "case", object_id: "c1" },
  { event_id: 2, ts: "2026-11-11T10:00:00", actor_id: "alice", action: "RUN_QUEUED", object_type: "run", object_id: "r1" },
  { event_id: 1, ts: "2026-11-11T09:00:00", actor_id: "alice", action: "CASE_OPENED", object_type: "case", object_id: "c1" },
];
const av: AuditView = { q: "", actor: "ALL", action: "ALL", day: "", sort: "id", dir: "desc" };

test("audit: filters and sorts", () => {
  assert.deepEqual(applyAudit(audit, av).map((r) => r.event_id), [3, 2, 1]);
  assert.deepEqual(applyAudit(audit, { ...av, actor: "alice", dir: "asc" }).map((r) => r.event_id), [1, 2]);
  assert.deepEqual(applyAudit(audit, { ...av, q: "c1" }).map((r) => r.event_id), [3, 1]);
  assert.deepEqual(applyAudit(audit, { ...av, day: dayKey("2026-11-11T10:00:00") }).map((r) => r.event_id), [2, 1]);
});
test("audit: day buckets include empty days between events", () => {
  const b = byDay(audit);
  assert.deepEqual(b.map((x) => x.total), [2, 0, 1]);
  assert.deepEqual(byDay([]), []);
});
test("csv: quotes, doubles quotes and neutralises formulas", () => {
  const out = toCsv(["a", "b"], [["x,y", 'say "hi"'], ["=SUM(A1)", "+1"]]);
  assert.equal(out, 'a,b\r\n"x,y","say ""hi"""\r\n\'=SUM(A1),\'+1\r\n');
});
test("relTime: coarse and safe on bad input", () => {
  const now = Date.parse("2026-11-13T12:00:00Z");
  assert.equal(relTime("2026-11-13T11:30:00Z", now), "30 min ago");
  assert.equal(relTime("2026-11-13T09:00:00Z", now), "3 h ago");
  assert.equal(relTime("not a date", now), "");
});
test("scoreDecimals: tight clusters near 1.0 get enough digits to tell bins apart", () => {
  assert.equal(scoreDecimals(0, 1), 2);
  assert.ok(scoreDecimals(0.9971, 1.0) >= 4);
  assert.equal(scoreDecimals(0.3, 0.3), 2);
});
