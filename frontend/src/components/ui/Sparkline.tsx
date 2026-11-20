"use client";

import { useId } from "react";
import { smoothPath } from "@/lib/charts";

/** A tiny trend line for KPI cards. Decorative: the number beside it carries the meaning, so it is hidden from assistive tech. */
export function Sparkline({ data, color = "var(--accent)", width = 84, height = 28 }: { data: number[]; color?: string; width?: number; height?: number }) {
  const id = useId().replace(/:/g, "");
  if (data.length < 2 || data.every((v) => v === data[0])) {
    return <svg width={width} height={height} aria-hidden><line x1="0" x2={width} y1={height - 3} y2={height - 3} stroke="var(--line-strong)" strokeWidth="1.5" strokeDasharray="3 3" /></svg>;
  }
  const max = Math.max(...data);
  const min = Math.min(...data);
  const pts = data.map((v, i) => ({ x: (i / (data.length - 1)) * (width - 2) + 1, y: height - 3 - ((v - min) / (max - min || 1)) * (height - 7) }));
  const line = smoothPath(pts);
  return (
    <svg width={width} height={height} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity="0.25" /><stop offset="100%" stopColor={color} stopOpacity="0" /></linearGradient>
      </defs>
      <path d={`${line}L${width - 1},${height}L1,${height}Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.75" strokeLinecap="round" />
    </svg>
  );
}
