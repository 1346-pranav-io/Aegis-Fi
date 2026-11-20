"use client";

import { motion } from "motion/react";
import { useState } from "react";
import { arcPath } from "@/lib/charts";

export interface Slice {
  key: string;
  label: string;
  value: number;
  color: string;
}

/**
 * Donut with a live centre readout, an interactive legend (click to hide a slice) and optional click-through.
 * The legend is a list of real buttons, so the chart is fully usable without a pointer.
 */
export function Donut({
  slices, ariaLabel, unit, centreLabel = "Total", onPick, size = 168,
}: {
  slices: Slice[];
  ariaLabel: string;
  unit: string;
  centreLabel?: string;
  onPick?: (key: string) => void;
  size?: number;
}) {
  const [off, setOff] = useState<Set<string>>(new Set());
  const [hot, setHot] = useState<string | null>(null);
  const live = slices.filter((s) => !off.has(s.key) && s.value > 0);
  const total = live.reduce((n, s) => n + s.value, 0);
  const all = slices.reduce((n, s) => n + s.value, 0);
  const c = size / 2;
  const r1 = c - 4;
  const r0 = c - 24;
  const arcs = live.map((s, i) => {
    const before = live.slice(0, i).reduce((n, x) => n + x.value, 0);
    const a0 = (before / total) * Math.PI * 2;
    return { s, d: arcPath(c, c, r0, hot === s.key ? r1 + 3 : r1, a0, a0 + (s.value / total) * Math.PI * 2) };
  });
  const focus = slices.find((s) => s.key === hot);

  return (
    <div className="donut">
      <div className="donut-svg" style={{ width: size, height: size }}>
        <svg width={size} height={size} role="img" aria-label={ariaLabel} viewBox={`0 0 ${size} ${size}`}>
          <circle cx={c} cy={c} r={(r0 + r1) / 2} fill="none" stroke="var(--surface-2)" strokeWidth={r1 - r0} />
          {arcs.map(({ s, d }, i) => (
            <motion.path
              key={s.key}
              d={d}
              fill={s.color}
              stroke="var(--surface)"
              strokeWidth={2}
              initial={{ opacity: 0 }}
              animate={{ opacity: hot && hot !== s.key ? 0.45 : 1 }}
              transition={{ duration: 0.25, delay: hot ? 0 : i * 0.05 }}
              style={{ cursor: onPick ? "pointer" : "default" }}
              onPointerEnter={() => setHot(s.key)}
              onPointerLeave={() => setHot(null)}
              onClick={() => onPick?.(s.key)}
            />
          ))}
        </svg>
        <div className="donut-centre" aria-hidden>
          <b className="num">{focus ? focus.value : total}</b>
          <span>{focus ? focus.label : all === 0 ? "No data" : centreLabel}</span>
        </div>
      </div>
      <ul className="donut-legend">
        {slices.map((s) => (
          <li key={s.key} onPointerEnter={() => setHot(s.key)} onPointerLeave={() => setHot(null)}>
            <button
              aria-pressed={!off.has(s.key)}
              aria-label={`${s.label}: ${s.value} ${unit}${all ? `, ${Math.round((s.value / all) * 100)} percent` : ""}. Click to show or hide.`}
              onClick={() => setOff((o) => { const n = new Set(o); if (n.has(s.key)) n.delete(s.key); else n.add(s.key); return n; })}
              onFocus={() => setHot(s.key)}
              onBlur={() => setHot(null)}
            >
              <i style={{ background: s.color }} />
              <span className="grow">{s.label}</span>
              <span className="num">{s.value}</span>
              <span className="num muted pct">{all ? `${Math.round((s.value / all) * 100)}%` : "-"}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
