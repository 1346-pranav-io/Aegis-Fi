"use client";

import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { Sparkline } from "./Sparkline";

export interface KpiDelta {
  text: string;
  tone: "up" | "down" | "flat"; // direction of change, not good or bad (a rise in alerts is not good news)
}

/** A metric card: label, icon tile, big number, optional change and trend line. Links to the view it summarises. */
export function Kpi({
  label, value, icon: Icon, delta, hint, spark, sparkColor, href, loading,
}: {
  label: string;
  value: string;
  icon: LucideIcon;
  delta?: KpiDelta;
  hint?: string;
  spark?: number[];
  sparkColor?: string;
  href?: string;
  loading?: boolean;
}) {
  const body = (
    <>
      <div className="kpi-top">
        <span className="kpi-label">{label}</span>
        <span className="kpi-icon"><Icon size={16} aria-hidden /></span>
      </div>
      {loading ? (
        <div className="skel" style={{ width: "50%", height: 28, margin: "6px 0 4px" }} aria-label="Loading" />
      ) : (
        <div className="kpi-main">
          <span className="kpi-value num">{value}</span>
          {spark && <Sparkline data={spark} color={sparkColor} />}
        </div>
      )}
      <div className="kpi-foot">
        {delta && !loading && (
          <span className={`delta ${delta.tone}`}>
            {delta.tone === "up" ? <ArrowUpRight size={14} aria-hidden /> : delta.tone === "down" ? <ArrowDownRight size={14} aria-hidden /> : <Minus size={14} aria-hidden />}
            {delta.text}
          </span>
        )}
        {hint && <span className="muted small">{hint}</span>}
      </div>
    </>
  );
  return href ? <Link href={href} className="kpi">{body}</Link> : <div className="kpi">{body}</div>;
}
