"use client";

import { motion } from "motion/react";
import { useId, useMemo, useState } from "react";
import { formatCompact, nearestIndex, niceTicks, smoothPath } from "@/lib/charts";
import { useElementWidth } from "@/lib/prefs";

export interface Series {
  key: string;
  label: string;
  color: string; // any CSS colour, normally a design token such as var(--blue)
  data: number[]; // one value per x, same length as `xs`
}

const PAD = { l: 38, r: 10, t: 10, b: 24 };

/**
 * Smooth area chart with a crosshair tooltip, a clickable legend and keyboard support (focus the chart, then
 * Left and Right move the crosshair, Escape clears it). Sizes itself to its container. A hidden table carries the
 * same numbers for screen readers.
 */
export function AreaChart({
  xs, series, xTick, xTip, ariaLabel, height = 232, unit = "events",
}: {
  xs: number[];
  series: Series[];
  xTick: (x: number) => string; // short axis label
  xTip: (x: number) => string; // long tooltip heading
  ariaLabel: string;
  height?: number;
  unit?: string;
}) {
  const uid = useId().replace(/:/g, "");
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [hot, setHot] = useState<number | null>(null);

  const visible = series.filter((s) => !hidden.has(s.key));
  const max = Math.max(0, ...visible.flatMap((s) => s.data));
  const { ticks, top } = useMemo(() => niceTicks(max, 4, 0, true), [max]);
  const w = Math.max(width, 120);
  const iw = w - PAD.l - PAD.r;
  const ih = height - PAD.t - PAD.b;
  const x0 = xs[0] ?? 0;
  const span = (xs[xs.length - 1] ?? 1) - x0 || 1;
  const px = (x: number) => PAD.l + (xs.length === 1 ? iw / 2 : ((x - x0) / span) * iw);
  const py = (v: number) => PAD.t + ih - (v / top) * ih;
  const xPos = useMemo(() => xs.map(px), [xs, iw, x0, span]); // eslint-disable-line react-hooks/exhaustive-deps

  const everyTick = Math.max(1, Math.ceil(xs.length / Math.max(2, Math.floor(iw / 78))));
  const toggle = (k: string) =>
    setHidden((h) => {
      const n = new Set(h);
      if (n.has(k)) n.delete(k);
      else if (visible.length > 1) n.add(k); // never hide the last series
      return n;
    });

  function move(e: React.PointerEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    setHot(nearestIndex(xPos, e.clientX - r.left));
  }
  const tipLeft = hot === null ? 0 : Math.min(Math.max(xPos[hot] - 80, 4), w - 164);

  return (
    <div className="areachart">
      <div className="legend-row" role="group" aria-label="Series">
        {series.map((s) => (
          <button key={s.key} className="legend-btn" aria-pressed={!hidden.has(s.key)} onClick={() => toggle(s.key)}>
            <i style={{ background: s.color }} /> {s.label}
          </button>
        ))}
      </div>
      <div ref={ref} className="chart-box" style={{ height }}>
        {width > 0 && (
          <svg
            width={w}
            height={height}
            role="img"
            aria-label={ariaLabel}
            tabIndex={0}
            onPointerMove={move}
            onPointerLeave={() => setHot(null)}
            onBlur={() => setHot(null)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight") { e.preventDefault(); setHot((h) => Math.min(xs.length - 1, (h ?? -1) + 1)); }
              else if (e.key === "ArrowLeft") { e.preventDefault(); setHot((h) => Math.max(0, (h ?? xs.length) - 1)); }
              else if (e.key === "Escape") setHot(null);
            }}
          >
            <defs>
              {series.map((s) => (
                <linearGradient key={s.key} id={`${uid}-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={s.color} stopOpacity="0.28" />
                  <stop offset="100%" stopColor={s.color} stopOpacity="0" />
                </linearGradient>
              ))}
            </defs>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.l} x2={w - PAD.r} y1={py(t)} y2={py(t)} className="gl" />
                <text x={PAD.l - 8} y={py(t) + 4} textAnchor="end" className="axis">{formatCompact(t)}</text>
              </g>
            ))}
            {xs.map((x, i) => i % everyTick === 0 && (
              <text key={x} x={xPos[i]} y={height - 6} textAnchor={i === 0 ? "start" : "middle"} className="axis">{xTick(x)}</text>
            ))}
            {visible.map((s) => {
              const pts = s.data.map((v, i) => ({ x: xPos[i], y: py(v) }));
              const line = smoothPath(pts);
              const area = pts.length > 1 ? `${line}L${pts[pts.length - 1].x},${py(0)}L${pts[0].x},${py(0)}Z` : "";
              return (
                <g key={s.key}>
                  {area && <motion.path d={area} fill={`url(#${uid}-${s.key})`} initial={{ opacity: 0, d: area }} animate={{ opacity: 1, d: area }} transition={{ duration: 0.4 }} />}
                  <motion.path
                    d={line}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={2}
                    strokeLinecap="round"
                    initial={{ pathLength: 0, d: line }}
                    animate={{ pathLength: 1, d: line }}
                    transition={{ duration: 0.5, ease: [0.2, 0.7, 0.2, 1] }}
                  />
                </g>
              );
            })}
            {hot !== null && xPos[hot] !== undefined && (
              <g pointerEvents="none">
                <line x1={xPos[hot]} x2={xPos[hot]} y1={PAD.t} y2={PAD.t + ih} className="cross" />
                {visible.map((s) => <circle key={s.key} cx={xPos[hot]} cy={py(s.data[hot])} r={4} fill="var(--surface)" stroke={s.color} strokeWidth={2} />)}
              </g>
            )}
          </svg>
        )}
        {hot !== null && xs[hot] !== undefined && width > 0 && (
          <div className="chart-tip" style={{ left: tipLeft }} role="status">
            <b>{xTip(xs[hot])}</b>
            {visible.map((s) => (
              <div key={s.key}><i style={{ background: s.color }} />{s.label}<span className="num">{s.data[hot]}</span></div>
            ))}
          </div>
        )}
      </div>
      <table className="sr-only">
        <caption>{ariaLabel}</caption>
        <thead><tr><th>Time</th>{series.map((s) => <th key={s.key}>{s.label} ({unit})</th>)}</tr></thead>
        <tbody>{xs.map((x, i) => <tr key={x}><td>{xTip(x)}</td>{series.map((s) => <td key={s.key}>{s.data[i]}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}
