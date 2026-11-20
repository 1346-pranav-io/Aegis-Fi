"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ExternalLink, Copy, FolderOpen, Link2, PanelRight, RefreshCw, XCircle } from "lucide-react";
import type { QueueItem } from "@/lib/api";
import { copyText, fmtScore, sentence } from "@/lib/format";
import { useRefreshMs, useTick } from "@/lib/prefs";
import { applyQueue, BIN_COUNT, statusCounts, STATUSES, type QueueRow, type SortKey } from "@/lib/queue";
import { useUrlState } from "@/lib/useUrlState";
import { CaseDrawer } from "@/components/CaseDrawer";
import { Chip, Empty, ErrorNotice, PageHead, useLoad } from "@/components/Bits";
import { ScoreChart } from "@/components/ScoreChart";
import { Segmented, SearchField, Toolbar } from "@/components/ui/Controls";
import { DataTable, type Column } from "@/components/ui/DataTable";
import type { MenuItem } from "@/components/ui/Menu";
import { useToast } from "@/components/ui/Toast";

const DEFAULTS = { q: "", status: "ALL", sort: "score_desc", bin: "" };

export default function QueuePage() {
  const [refreshMs] = useRefreshMs();
  const tick = useTick(refreshMs);
  const [manual, setManual] = useState(0);
  const { data, error, loading } = useLoad<QueueItem[]>("/queue?limit=200", tick + manual);
  const [v, set] = useUrlState(DEFAULTS);
  const router = useRouter();
  const toast = useToast();
  const [peek, setPeek] = useState<string | null>(null);

  const all = useMemo(() => data ?? [], [data]);
  const binNum = v.bin === "" || Number.isNaN(Number(v.bin)) ? null : Math.min(BIN_COUNT - 1, Math.max(0, Number(v.bin)));
  const sort = (["score_desc", "score_asc", "case", "status"] as const).find((s) => s === v.sort) ?? "score_desc";
  const rows = useMemo(() => applyQueue(all, { q: v.q, status: v.status, sort: sort as SortKey, bin: binNum }), [all, v.q, v.status, sort, binNum]);
  const counts = useMemo(() => statusCounts(all), [all]);
  const lo = useMemo(() => Math.min(...all.map((c) => c.priority_score)), [all]);
  const hi = useMemo(() => Math.max(...all.map((c) => c.priority_score)), [all]);
  const filtered = v.q !== "" || v.status !== "ALL" || binNum !== null;

  function menuFor(c: QueueRow): MenuItem[] {
    const url = `${window.location.origin}/cases/${c.case_id}`;
    return [
      { label: "Open case", icon: <FolderOpen size={16} aria-hidden />, hint: "Enter", onSelect: () => router.push(`/cases/${c.case_id}`) },
      { label: "Quick view", icon: <PanelRight size={16} aria-hidden />, onSelect: () => setPeek(c.case_id) },
      { label: "Open in new tab", icon: <ExternalLink size={16} aria-hidden />, onSelect: () => window.open(url, "_blank", "noopener") },
      { label: "Copy case id", icon: <Copy size={16} aria-hidden />, onSelect: async () => ((await copyText(c.case_id)) ? toast.ok("Case id copied.") : toast.error("Could not copy the id.")) },
      { label: "Copy link", icon: <Link2 size={16} aria-hidden />, onSelect: async () => ((await copyText(url)) ? toast.ok("Link copied.") : toast.error("Could not copy the link.")) },
    ];
  }

  const columns: Column<QueueRow>[] = [
    { key: "case", header: "Case", sort: () => 0, cell: (c) => <span className="mono">{c.case_id}</span> },
    {
      key: "score", header: "Score", align: "right", sort: () => 0,
      cell: (c) => (
        <>
          <span className="scorebar hide-sm" style={{ width: Math.max(2, ((c.priority_score - lo) / (hi - lo || 1)) * 80 + 2) }} aria-hidden />
          <span className="num">{fmtScore(c.priority_score)}</span>
        </>
      ),
    },
    { key: "status", header: "Status", sort: () => 0, cell: (c) => <Chip value={c.status} /> },
  ];

  // The table shows the sort; `applyQueue` does it (so the order matches the URL and the chart filter).
  const sortKey = sort === "case" ? "case" : sort === "status" ? "status" : "score";
  const sortDir = sort === "score_desc" ? "desc" : "asc";
  function onSort(key: string, dir: "asc" | "desc") {
    set({ sort: key === "score" ? (dir === "desc" ? "score_desc" : "score_asc") : key });
  }

  return (
    <>
      <PageHead
        actions={<button className="btn sm" aria-label="Refresh" onClick={() => setManual((n) => n + 1)} disabled={loading}><RefreshCw size={14} aria-hidden className={loading ? "spin" : ""} /> <span className="btn-label">Refresh</span></button>}
      >
        Highest detector score first. A score ranks cases for review; it is not the probability that a case is laundering.
      </PageHead>
      <ErrorNotice message={error} />

      {all.length > 0 && (
        <section className="panel" style={{ marginBottom: 14 }}>
          <div className="card-head">
            <div className="grow"><h2>Score distribution</h2><p>Pick a bar to show only that range.</p></div>
            {binNum !== null && <button className="btn sm" onClick={() => set({ bin: "" })}><XCircle size={14} aria-hidden /> Clear range</button>}
          </div>
          <ScoreChart items={all} bin={binNum} onBin={(i) => set({ bin: i === null ? "" : String(i) })} />
        </section>
      )}

      <Toolbar>
        <SearchField value={v.q} onChange={(q) => set({ q })} placeholder="Search by case id" label="Search cases by id" />
        <Segmented
          label="Filter by status"
          value={v.status}
          onChange={(status) => set({ status })}
          options={[{ value: "ALL", label: "All", count: counts.ALL ?? 0 }, ...STATUSES.filter((s) => counts[s]).map((s) => ({ value: s, label: sentence(s), count: counts[s] }))]}
        />
      </Toolbar>

      <section className="panel">
        <DataTable
          label="Cases"
          rows={rows}
          rowKey={(c) => c.case_id}
          columns={columns}
          loading={loading && !data}
          presorted
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={onSort}
          resetKey={`${v.q}|${v.status}|${v.bin}`}
          onOpen={(c) => router.push(`/cases/${c.case_id}`)}
          menu={menuFor}
          defaultPageSize={10}
          empty={
            <Empty title={all.length ? "No case matches" : "No cases yet"}>
              {all.length ? (
                <div style={{ marginTop: 8 }}>{filtered && <button className="btn sm" onClick={() => set({ q: "", status: "ALL", bin: "" })}>Clear filters</button>}</div>
              ) : "Cases appear when a detector records alerts for an account."}
            </Empty>
          }
        />
      </section>
      <p className="muted small" style={{ marginTop: 10 }}>Arrow keys or j and k move, Enter opens, right-click or the ... button for more. Press ? for all shortcuts.</p>
      <CaseDrawer id={peek} onClose={() => setPeek(null)} />
    </>
  );
}
