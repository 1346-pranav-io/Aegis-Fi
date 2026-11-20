"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowRight, Inbox } from "lucide-react";
import { api, type QueueItem } from "@/lib/api";
import { fmtScore } from "@/lib/format";
import { Dialog } from "./ui/Dialog";
import { Kbd } from "./ui/Tip";

export interface Command {
  id: string;
  label: string;
  group: "Go to" | "Do" | "Cases";
  icon?: ReactNode;
  hint?: string;
  keywords?: string;
  run: () => void;
}

/** Ctrl/Cmd+K. Jump to a page or a case, or run a global action, entirely from the keyboard. */
export function CommandPalette({ open, onClose, commands }: { open: boolean; onClose: () => void; commands: Command[] }) {
  // The dialog only renders its children while open, so <Body> mounts fresh (empty query, new case list) every time.
  return (
    <Dialog open={open} title="Go to or do anything" onClose={onClose}>
      <Body onClose={onClose} commands={commands} />
    </Dialog>
  );
}

function Body({ onClose, commands }: { onClose: () => void; commands: Command[] }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [cases, setCases] = useState<QueueItem[]>([]);
  const [failed, setFailed] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    api<QueueItem[]>("/queue?limit=200").then(
      (r) => live && setCases(r),
      () => live && setFailed(true),
    );
    const t = window.setTimeout(() => input.current?.focus(), 30);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, []);

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const base = commands.filter((c) => !needle || `${c.label} ${c.keywords ?? ""}`.toLowerCase().includes(needle));
    const caseCmds: Command[] = cases
      .filter((c) => (needle ? c.case_id.toLowerCase().includes(needle) : false))
      .slice(0, 8)
      .map((c) => ({
        id: c.case_id, label: c.case_id, group: "Cases" as const, icon: <Inbox size={16} aria-hidden />,
        hint: `score ${fmtScore(c.priority_score)}`, run: () => router.push(`/cases/${c.case_id}`),
      }));
    return [...base, ...caseCmds];
  }, [q, commands, cases, router]);

  function choose(c: Command | undefined) {
    if (!c) return;
    onClose();
    c.run();
  }

  const groups: Array<Command["group"]> = ["Go to", "Do", "Cases"];
  return (
    <>
      <div className="field">
        <input
          ref={input}
          data-autofocus
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={results[active] ? `pal-${results[active].id}` : undefined}
          aria-label="Search pages, cases and actions"
          placeholder="Type a page, an action, or part of a case id"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(results.length - 1, i + 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
            else if (e.key === "Enter") { e.preventDefault(); choose(results[active]); }
          }}
        />
      </div>
      <div id="palette-list" role="listbox" className="palette-list">
        {results.length === 0 && <div className="empty"><b>No match</b>Try a page name, or part of a case id.</div>}
        {groups.map((g) => {
          const rows = results.filter((r) => r.group === g);
          if (!rows.length) return null;
          return (
            <div key={g}>
              <div className="palette-group">{g}</div>
              {rows.map((r) => {
                const i = results.indexOf(r);
                return (
                  <div
                    key={r.id}
                    id={`pal-${r.id}`}
                    role="option"
                    aria-selected={i === active}
                    className={`palette-item ${i === active ? "active" : ""}`}
                    onMouseMove={() => setActive(i)}
                    onClick={() => choose(r)}
                  >
                    {r.icon ?? <ArrowRight size={16} aria-hidden />}
                    <span className={`grow ${r.group === "Cases" ? "mono" : ""}`}>{r.label}</span>
                    {r.hint && <span className="muted small">{r.hint}</span>}
                  </div>
                );
              })}
            </div>
          );
        })}
        {failed && <div className="muted small" style={{ padding: "8px 12px" }}>Case search is unavailable right now. Pages and actions still work.</div>}
      </div>
      <div className="muted small row" style={{ gap: 14 }}>
        <span><Kbd>Up</Kbd> <Kbd>Down</Kbd> move</span><span><Kbd>Enter</Kbd> open</span><span><Kbd>Esc</Kbd> close</span>
      </div>
    </>
  );
}
