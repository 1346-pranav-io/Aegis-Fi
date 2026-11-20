"use client";

import { useMemo } from "react";
import { histogram, scoreDecimals, STATUSES, type QueueRow } from "@/lib/queue";
import { sentence } from "@/lib/format";
import { BarChart, type BarDatum } from "./ui/BarChart";

const TONE: Record<string, string> = { OPEN: "open", INVESTIGATING: "investigating", PENDING_REVIEW: "pending_review", CLOSED: "closed" };

/** Distribution of detector scores, stacked by case status. A bar is a score range; picking one filters by it. */
export function ScoreChart({ items, bin, onBin }: { items: QueueRow[]; bin: number | null; onBin: (i: number | null) => void }) {
  const data = useMemo<BarDatum[]>(
    () => {
      const bins = histogram(items);
      const d = bins.length ? scoreDecimals(bins[0].lo, bins[bins.length - 1].hi) : 2;
      return bins.map((b, i) => ({
        key: String(i),
        label: `Score ${b.lo.toFixed(d)} to ${b.hi.toFixed(d)}`,
        axis: b.lo.toFixed(d),
        segments: STATUSES.map((s) => ({ tone: TONE[s], value: b.counts[s] ?? 0, label: sentence(s).toLowerCase() })),
      }));
    },
    [items],
  );
  const present = STATUSES.filter((s) => items.some((c) => c.status === s));
  return (
    <>
      <BarChart data={data} selected={bin === null ? null : String(bin)} onSelect={(k) => onBin(k === null ? null : Number(k))} ariaLabel="Cases by detector score range" unit="cases" />
      <div className="legend" style={{ padding: "0 16px 14px" }} aria-hidden>
        {present.map((s) => (
          <span key={s}><i className={`seg-${TONE[s]}`} />{sentence(s)}</span>
        ))}
      </div>
    </>
  );
}
