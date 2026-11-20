"use client";

import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  CheckCircle, AlertCircle, XCircle, Circle, Clock, Loader2, AlertTriangle, Info, ChevronDown,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { copyText, sentence } from "@/lib/format";
import { HeaderActions } from "./HeaderActions";
import { Tip } from "./ui/Tip";
import { useToast } from "./ui/Toast";

type Tone = "ok" | "warn" | "bad" | "info" | "";

const TONES: Record<string, Tone> = {
  VERIFIED: "ok", COMPLETE: "ok", APPROVED: "ok", CLOSED: "ok", OK: "ok", VALIDATED: "ok",
  REJECTED: "bad", CONTRADICTED: "bad", FAILED: "bad", DENIED: "bad", UNSUPPORTED: "bad", QUARANTINED: "bad",
  PARTIAL: "warn", PARTIALLY_SUPPORTED: "warn", UNVERIFIABLE: "warn", PENDING_REVIEW: "warn",
  RUNNING: "info", QUEUED: "info", INVESTIGATING: "info", OPEN: "",
};

const LABELS: Record<string, string> = {
  PENDING_REVIEW: "Pending review", PARTIALLY_SUPPORTED: "Partly supported",
};

function label(v: string): string {
  return LABELS[v] ?? sentence(v);
}

export function Chip({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">-</span>;
  const tone = TONES[value] ?? "";
  const Icon =
    tone === "ok" ? CheckCircle : tone === "bad" ? XCircle : tone === "warn" ? AlertCircle
    : value === "RUNNING" ? Loader2 : value === "QUEUED" ? Clock : Circle;
  return (
    <span className={`chip ${tone}`}>
      <Icon size={13} aria-hidden />
      {label(value)}
    </span>
  );
}

/** Evidence ids are long; show a count and expand on demand. Each id copies on click. */
export function Evidence({ ids }: { ids: string[] }) {
  const toast = useToast();
  if (!ids.length) return <span className="muted small">no evidence</span>;
  return (
    <details className="ev">
      <summary>{ids.length} {ids.length === 1 ? "source" : "sources"}</summary>
      <div className="ids">
        {ids.map((i) => (
          <Tip key={i} label="Copy id">
            <button
              className="copy-id mono muted"
              onClick={async () => ((await copyText(i)) ? toast.ok("Evidence id copied.") : toast.error("Could not copy. Select the id and copy it by hand."))}
            >
              {i}
            </button>
          </Tip>
        ))}
      </div>
    </details>
  );
}

/** A panel whose body expands and collapses. The header is the toggle, so it works from the keyboard. */
export function Collapsible({ title, meta, defaultOpen = false, children }: { title: string; meta?: ReactNode; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <section className="panel">
      <button className="collapse-head" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <h2>{title}</h2>
        {meta}
        <ChevronDown size={14} aria-hidden />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={id}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.2, 0.7, 0.2, 1] }}
            style={{ overflow: "hidden", borderTop: "1px solid var(--line)" }}
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

export function Notice({ tone = "warn", children }: { tone?: "warn" | "bad" | "info"; children: ReactNode }) {
  const Icon = tone === "bad" ? XCircle : tone === "info" ? Info : AlertTriangle;
  return (
    <div className={`notice ${tone === "warn" ? "" : tone}`} role={tone === "bad" ? "alert" : "status"}>
      <Icon size={18} aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return message ? <Notice tone="bad">{message}</Notice> : null;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <b>{title}</b>
      {children}
    </div>
  );
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="panel-body" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skel" style={{ width: `${92 - i * 11}%`, margin: "10px 0" }} />
      ))}
    </div>
  );
}

/** Page intro line, plus actions that are rendered into the shell's header. The page title itself comes from the route. */
export function PageHead({ children, actions }: { title?: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <>
      {actions && <HeaderActions>{actions}</HeaderActions>}
      {children && <p className="lede">{children}</p>}
    </>
  );
}

/** Loads `path`. Change `refreshKey` to force a reload (for example after a background run finishes). */
export function useLoad<T>(path: string | null, refreshKey = 0) {
  interface Result {
    key: string; // the request this result answers
    data: T | null; // last good data, kept while a refresh is in flight
    error: string;
    status: number | null;
    at: number; // when this result arrived
  }
  const [nonce, setNonce] = useState(0);
  const [res, setRes] = useState<Result | null>(null);
  const key = `${path}|${refreshKey}|${nonce}`;

  useEffect(() => {
    if (!path) return;
    let live = true;
    api<T>(path).then(
      (data) => live && setRes({ key, data, error: "", status: null, at: Date.now() }),
      (e: unknown) => {
        if (!live) return;
        const status = e instanceof ApiError ? e.status : null;
        const error = e instanceof ApiError ? `${e.status}: ${e.message}` : "Could not reach the server. Check that the API is running.";
        setRes((prev) => ({ key, data: prev?.data ?? null, error, status, at: prev?.at ?? 0 }));
      },
    );
    return () => {
      live = false; // a newer request supersedes this one, so a slow old response cannot overwrite it
    };
  }, [path, key]);

  const reload = useCallback(async () => setNonce((n) => n + 1), []);
  return {
    data: path ? (res?.data ?? null) : null,
    error: path ? (res?.error ?? "") : "",
    status: path ? (res?.status ?? null) : null,
    loading: path !== null && res?.key !== key,
    updatedAt: path ? (res?.at ?? 0) : 0,
    reload,
  };
}

/** "n in is 6 (TXN_AGGREGATE)" -> { key: "n in", value: "6", type: "TXN_AGGREGATE" } when it matches. */
export function splitFinding(text: string): { key: string; value: string; type: string } | null {
  const m = /^(.*?) is (.*?) \(([A-Z][A-Z0-9_]+)\)$/.exec(text);
  return m ? { key: m[1], value: m[2], type: m[3] } : null;
}

const WORDS: Record<string, string> = {
  TXN: "transaction", BEHAV: "behaviour", FANIN: "fan-in", FANOUT: "fan-out", ER: "entity link",
};

export function typeTitle(t: string): string {
  const s = t.split("_").map((w) => WORDS[w] ?? w.toLowerCase()).join(" ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
