"use client";

import { motion } from "motion/react";
import { useState } from "react";

export interface Segment {
  tone: string; // maps to a .seg-* colour class
  value: number;
  label: string;
}
export interface BarDatum {
  key: string;
  label: string; // shown in the readout
  axis?: string; // short tick label under the bar
  segments: Segment[];
}

const total = (d: BarDatum) => d.segments.reduce((n, s) => n + s.value, 0);

/**
 * Stacked bar chart built from real buttons, so it works with the mouse, touch and keyboard.
 * Click (or Enter) selects a bar to filter by it; click again to clear. Arrow keys move between bars.
 * A text readout under the chart says what is hovered or focused, so no value depends on colour alone.
 */
export function BarChart({
  data, selected, onSelect, ariaLabel, unit, height = 132,
}: {
  data: BarDatum[];
  selected: string | null;
  onSelect: (key: string | null) => void;
  ariaLabel: string;
  unit: string;
  height?: number;
}) {
  const [hot, setHot] = useState<string | null>(null);
  const max = Math.max(1, ...data.map(total));
  const shown = data.find((d) => d.key === (hot ?? selected));
  const every = Math.max(1, Math.ceil(data.length / 5)); // about five tick labels, so they never collide on a phone

  return (
    <div className="chart">
      <div className="bars" role="group" aria-label={ariaLabel} style={{ height }}>
        {data.map((d, i) => {
          const t = total(d);
          const dim = selected !== null && selected !== d.key;
          return (
            <button
              key={d.key}
              className={`bar ${dim ? "dim" : ""} ${selected === d.key ? "sel" : ""}`}
              aria-pressed={selected === d.key}
              aria-label={`${d.label}: ${t} ${unit}`}
              onClick={() => onSelect(selected === d.key ? null : d.key)}
              onMouseEnter={() => setHot(d.key)}
              onMouseLeave={() => setHot(null)}
              onFocus={() => setHot(d.key)}
              onBlur={() => setHot(null)}
              onKeyDown={(e) => {
                const sib = e.key === "ArrowRight" ? e.currentTarget.nextElementSibling : e.key === "ArrowLeft" ? e.currentTarget.previousElementSibling : null;
                if (sib instanceof HTMLElement) { e.preventDefault(); sib.focus(); }
              }}
            >
              <motion.span
                className="bar-stack"
                initial={{ height: 0 }}
                animate={{ height: t === 0 ? 0 : Math.max(3, (t / max) * (height - 4)) }}
                transition={{ duration: 0.35, delay: Math.min(i * 0.02, 0.3), ease: [0.2, 0.7, 0.2, 1] }}
              >
                {d.segments.filter((s) => s.value > 0).map((s) => (
                  <span key={s.tone} className={`seg-${s.tone}`} style={{ flexGrow: s.value }} />
                ))}
              </motion.span>
            </button>
          );
        })}
      </div>
      <div className="ticks" aria-hidden>
        {data.map((d, i) => <span key={d.key}>{i % every === 0 ? d.axis ?? "" : ""}</span>)}
      </div>
      <p className="readout" aria-live="polite">
        {shown ? (
          <>
            <b>{shown.label}</b>: <span className="num">{total(shown)}</span> {unit}
            {shown.segments.some((s) => s.value > 0) && (
              <span className="muted"> ({shown.segments.filter((s) => s.value > 0).map((s) => `${s.value} ${s.label}`).join(", ")})</span>
            )}
          </>
        ) : (
          <span className="muted">Hover or focus a bar for its values. Click one to filter.</span>
        )}
      </p>
    </div>
  );
}
