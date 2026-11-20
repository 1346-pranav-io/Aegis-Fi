"use client";

import { AnimatePresence, motion } from "motion/react";
import { useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, ChevronUp, MoreHorizontal } from "lucide-react";
import { paginate, sortBy, type Dir } from "@/lib/table";
import { Menu, useMenu, type MenuItem } from "./Menu";
import { Tip } from "./Tip";

export interface Column<T> {
  key: string;
  header: string;
  cell: (row: T) => ReactNode;
  sort?: (a: T, b: T) => number; // omit for a column that cannot be sorted
  align?: "right";
  hide?: "sm" | "md"; // drop this column on narrow screens
  width?: string;
}

/**
 * Data table: sortable headers, pagination, expandable rows, row menu (right-click or button), keyboard navigation
 * (Arrow keys or j/k move, Enter opens, Space expands) and skeleton / empty states. Filtering and search belong to the
 * page, which passes the filtered rows in; change `resetKey` when filters change to return to page 1.
 */
export function DataTable<T>({
  rows, rowKey, columns, label, loading, empty, defaultSort, sortKey, sortDir, onSort, presorted,
  pageSizes = [10, 25, 50], defaultPageSize = 10, resetKey = "", onOpen, expand, menu, selectedKey,
}: {
  rows: T[];
  rowKey: (r: T) => string;
  columns: Column<T>[];
  label: string;
  loading?: boolean;
  empty?: ReactNode;
  defaultSort?: { key: string; dir: Dir };
  sortKey?: string; // controlled sorting (also set onSort)
  sortDir?: Dir;
  onSort?: (key: string, dir: Dir) => void;
  presorted?: boolean; // rows already arrive in the right order; the header only shows and reports the sort
  pageSizes?: number[];
  defaultPageSize?: number;
  resetKey?: string;
  onOpen?: (r: T) => void;
  expand?: (r: T) => ReactNode;
  menu?: (r: T) => MenuItem[];
  selectedKey?: string | null;
}) {
  const [own, setOwn] = useState(defaultSort ?? { key: "", dir: "asc" as Dir });
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(defaultPageSize);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [cursor, setCursor] = useState(0);
  const [seenReset, setSeenReset] = useState(resetKey);
  const mn = useMenu();

  // Filters changed: back to the first page. (Derived-state reset, done during render rather than in an effect.)
  if (seenReset !== resetKey) {
    setSeenReset(resetKey);
    setPage(1);
    setCursor(0);
  }

  const key = sortKey ?? own.key;
  const dir = sortDir ?? own.dir;
  const col = columns.find((c) => c.key === key);
  const sorted = useMemo(() => (presorted ? rows : sortBy(rows, col?.sort, dir)), [rows, col, dir, presorted]);
  const pg = paginate(sorted, page, size);
  const cur = Math.min(cursor, Math.max(0, pg.rows.length - 1));

  function sortOn(c: Column<T>) {
    const next: Dir = key === c.key && dir === "asc" ? "desc" : "asc";
    if (onSort) onSort(c.key, next);
    else setOwn({ key: c.key, dir: next });
  }
  const toggle = (k: string) => setOpen((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const focusRow = (i: number) => {
    setCursor(i);
    // Rows already exist, so focus now; waiting for a frame would lag (or never run) in a throttled tab.
    document.querySelector<HTMLElement>(`[data-table="${label}"] [data-i="${i}"]`)?.focus();
  };

  function onKey(e: React.KeyboardEvent, r: T, i: number) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if ((e.target as HTMLElement).closest("input, select, textarea")) return;
    if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); focusRow(Math.min(pg.rows.length - 1, i + 1)); }
    else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); focusRow(Math.max(0, i - 1)); }
    else if (e.key === "Home") { e.preventDefault(); focusRow(0); }
    else if (e.key === "End") { e.preventDefault(); focusRow(pg.rows.length - 1); }
    else if (e.key === "Enter" && onOpen && e.target === e.currentTarget) { e.preventDefault(); onOpen(r); }
    else if (e.key === " " && expand && e.target === e.currentTarget) { e.preventDefault(); toggle(rowKey(r)); }
    else if (menu && (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey))) {
      e.preventDefault();
      const el = e.currentTarget as HTMLElement;
      mn.openFor(el, menu(r));
    }
  }

  const span = columns.length + (expand ? 1 : 0) + (menu ? 1 : 0);

  return (
    <div className="dt" data-table={label}>
      <div className="tbl-wrap">
        <table className="tbl dt-tbl" aria-label={label} aria-busy={loading || undefined}>
          <thead>
            <tr>
              {expand && <th style={{ width: 36 }}><span className="sr-only">Expand</span></th>}
              {columns.map((c) => (
                <th
                  key={c.key}
                  className={`${c.align === "right" ? "r" : ""} ${c.hide ? `col-hide-${c.hide}` : ""}`}
                  style={c.width ? { width: c.width } : undefined}
                  aria-sort={c.sort && key === c.key ? (dir === "asc" ? "ascending" : "descending") : c.sort ? "none" : undefined}
                >
                  {c.sort ? (
                    <button className="sort" onClick={() => sortOn(c)}>
                      {c.header}
                      {key === c.key ? (dir === "asc" ? <ChevronUp size={12} aria-hidden /> : <ChevronDown size={12} aria-hidden />) : <span className="sort-ph" aria-hidden />}
                    </button>
                  ) : c.header}
                </th>
              ))}
              {menu && <th style={{ width: 44 }}><span className="sr-only">Actions</span></th>}
            </tr>
          </thead>
          <tbody>
            {loading && !rows.length
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i} aria-hidden>
                    {Array.from({ length: span }).map((__, j) => <td key={j}><div className="skel" style={{ width: `${55 + ((i * 7 + j * 13) % 40)}%` }} /></td>)}
                  </tr>
                ))
              : pg.rows.map((r, i) => {
                  const k = rowKey(r);
                  const isOpen = open.has(k);
                  return (
                    <RowGroup key={k}>
                      <tr
                        data-i={i}
                        tabIndex={i === cur ? 0 : -1}
                        className={`${onOpen ? "link" : ""} ${selectedKey === k ? "selected" : ""}`}
                        aria-expanded={expand ? isOpen : undefined}
                        onKeyDown={(e) => onKey(e, r, i)}
                        onFocus={() => setCursor(i)}
                        onClick={(e) => {
                          if (onOpen && !(e.target as HTMLElement).closest("a, button, input, select")) onOpen(r);
                        }}
                        onContextMenu={menu ? (e) => { e.preventDefault(); setCursor(i); mn.openAt(e, menu(r)); } : undefined}
                      >
                        {expand && (
                          <td>
                            <button className="expander" aria-expanded={isOpen} aria-label={isOpen ? "Hide details" : "Show details"} onClick={() => toggle(k)}>
                              <ChevronRight size={14} aria-hidden />
                            </button>
                          </td>
                        )}
                        {columns.map((c) => (
                          <td key={c.key} className={`${c.align === "right" ? "r" : ""} ${c.hide ? `col-hide-${c.hide}` : ""}`}>{c.cell(r)}</td>
                        ))}
                        {menu && (
                          <td>
                            <Tip label="Row actions">
                              <button className="btn sm icon more" aria-label="Row actions" aria-haspopup="menu" onClick={(e) => mn.openFor(e.currentTarget, menu(r))}>
                                <MoreHorizontal size={16} aria-hidden />
                              </button>
                            </Tip>
                          </td>
                        )}
                      </tr>
                      <AnimatePresence initial={false}>
                        {expand && isOpen && (
                          <tr>
                            <td colSpan={span} style={{ padding: 0, borderBottom: 0 }}>
                              <motion.div className="dt-more" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.18 }} style={{ overflow: "hidden" }}>
                                {expand(r)}
                              </motion.div>
                            </td>
                          </tr>
                        )}
                      </AnimatePresence>
                    </RowGroup>
                  );
                })}
          </tbody>
        </table>
      </div>
      {!loading && rows.length === 0 && (empty ?? <div className="empty"><b>Nothing to show</b></div>)}
      {rows.length > 0 && (
        <div className="dt-foot">
          <label className="selectf">
            <span className="muted small">Rows</span>
            <select value={size} onChange={(e) => { setSize(Number(e.target.value)); setPage(1); }}>
              {pageSizes.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <span className="muted small num grow" aria-live="polite">{pg.from}-{pg.to} of {pg.total}</span>
          <div className="row" style={{ gap: 4 }} role="group" aria-label="Pagination">
            <button className="btn sm icon" aria-label="First page" disabled={pg.page === 1} onClick={() => setPage(1)}><ChevronsLeft size={14} aria-hidden /></button>
            <button className="btn sm icon" aria-label="Previous page" disabled={pg.page === 1} onClick={() => setPage(pg.page - 1)}><ChevronLeft size={14} aria-hidden /></button>
            <span className="small num" style={{ padding: "0 6px" }}>{pg.page} / {pg.pages}</span>
            <button className="btn sm icon" aria-label="Next page" disabled={pg.page === pg.pages} onClick={() => setPage(pg.page + 1)}><ChevronRight size={14} aria-hidden /></button>
            <button className="btn sm icon" aria-label="Last page" disabled={pg.page === pg.pages} onClick={() => setPage(pg.pages)}><ChevronsRight size={14} aria-hidden /></button>
          </div>
        </div>
      )}
      <Menu state={mn.state} onClose={mn.close} />
    </div>
  );
}

function RowGroup({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
