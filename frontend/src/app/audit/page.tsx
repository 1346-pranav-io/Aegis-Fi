"use client";

import { useMemo, useState } from "react";
import { Copy, Download, Filter, RefreshCw, User, XCircle } from "lucide-react";
import type { AuditEvent } from "@/lib/api";
import { applyAudit, byDay, toCsv, type AuditSort } from "@/lib/audit";
import { copyText, downloadBlob, relTime } from "@/lib/format";
import { useRefreshMs, useTick } from "@/lib/prefs";
import { useUrlState } from "@/lib/useUrlState";
import { Empty, ErrorNotice, PageHead, useLoad } from "@/components/Bits";
import { BarChart } from "@/components/ui/BarChart";
import { SearchField, SelectField, Toolbar } from "@/components/ui/Controls";
import { DataTable, type Column } from "@/components/ui/DataTable";
import type { MenuItem } from "@/components/ui/Menu";
import { Tip } from "@/components/ui/Tip";
import { useToast } from "@/components/ui/Toast";

const DEFAULTS = { q: "", actor: "ALL", action: "ALL", day: "", sort: "id", dir: "desc", n: "200" };

export default function AuditPage() {
  const [v, set] = useUrlState(DEFAULTS);
  const limit = ["100", "200", "500"].includes(v.n) ? v.n : "200";
  const [refreshMs] = useRefreshMs();
  const tick = useTick(refreshMs);
  const [manual, setManual] = useState(0);
  const { data, error, loading, status } = useLoad<AuditEvent[]>(`/audit?limit=${limit}`, tick + manual);
  const toast = useToast();

  const all = useMemo(() => data ?? [], [data]);
  const actors = useMemo(() => [...new Set(all.map((e) => e.actor_id))].sort(), [all]);
  const actions = useMemo(() => [...new Set(all.map((e) => e.action))].sort(), [all]);
  const days = useMemo(() => byDay(all), [all]);
  const sort = (["id", "ts", "actor", "action"] as const).find((s) => s === v.sort) ?? "id";
  const dir = v.dir === "asc" ? "asc" : "desc";
  const rows = useMemo(() => applyAudit(all, { q: v.q, actor: v.actor, action: v.action, day: v.day, sort: sort as AuditSort, dir }), [all, v.q, v.actor, v.action, v.day, sort, dir]);
  const filtered = v.q !== "" || v.actor !== "ALL" || v.action !== "ALL" || v.day !== "";

  function exportCsv() {
    const csv = toCsv(["event_id", "time", "actor", "action", "object_type", "object_id"], rows.map((e) => [String(e.event_id), e.ts, e.actor_id, e.action, e.object_type, e.object_id]));
    downloadBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), "audit-log.csv");
    toast.ok(`Exported ${rows.length} ${rows.length === 1 ? "event" : "events"}.`);
  }

  const menu = (e: AuditEvent): MenuItem[] => [
    { label: `Only actor ${e.actor_id}`, icon: <User size={16} aria-hidden />, onSelect: () => set({ actor: e.actor_id }) },
    { label: `Only action ${e.action}`, icon: <Filter size={16} aria-hidden />, onSelect: () => set({ action: e.action }) },
    { label: "Copy object id", icon: <Copy size={16} aria-hidden />, onSelect: async () => ((await copyText(e.object_id)) ? toast.ok("Object id copied.") : toast.error("Could not copy the id.")) },
  ];

  const columns: Column<AuditEvent>[] = [
    { key: "id", header: "#", align: "right", width: "80px", sort: (a, b) => a.event_id - b.event_id, cell: (e) => <span className="num muted">{e.event_id}</span> },
    { key: "ts", header: "Time", sort: (a, b) => Date.parse(a.ts) - Date.parse(b.ts), cell: (e) => <Tip label={new Date(e.ts).toLocaleString()}><span tabIndex={0} className="num small">{relTime(e.ts)}</span></Tip> },
    { key: "actor", header: "Actor", hide: "sm", sort: (a, b) => a.actor_id.localeCompare(b.actor_id), cell: (e) => e.actor_id },
    { key: "action", header: "Action", sort: (a, b) => a.action.localeCompare(b.action), cell: (e) => <span className="mono">{e.action}</span> },
    { key: "obj", header: "Object", hide: "md", cell: (e) => <><span className="muted">{e.object_type}</span> <span className="mono">{e.object_id}</span></> },
  ];

  return (
    <>
      <PageHead
        actions={
          <>
            <Tip label="Download the filtered rows as CSV"><button className="btn sm" aria-label="Download CSV" onClick={exportCsv} disabled={!rows.length}><Download size={14} aria-hidden /> <span className="btn-label">CSV</span></button></Tip>
            <button className="btn sm" aria-label="Refresh" onClick={() => setManual((n) => n + 1)} disabled={loading}><RefreshCw size={14} aria-hidden className={loading ? "spin" : ""} /> <span className="btn-label">Refresh</span></button>
          </>
        }
      >
        Every consequential action, append-only, visible to the auditor and admin roles. The actor column is the database login that wrote the row.
      </PageHead>
      <ErrorNotice message={status === 403 ? "Your roles cannot read the audit log." : error} />

      {days.length > 1 && (
        <section className="panel" style={{ marginBottom: 14 }}>
          <div className="card-head">
            <div className="grow"><h2>Events per day</h2><p>Click a day to filter the table.</p></div>
            {v.day && <button className="btn sm" onClick={() => set({ day: "" })}><XCircle size={14} aria-hidden /> Clear day</button>}
          </div>
          <BarChart
            data={days.map((d) => ({ key: d.day, label: new Date(`${d.day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }), axis: d.day.slice(5), segments: [{ tone: "default", value: d.total, label: "events" }] }))}
            selected={v.day || null}
            onSelect={(k) => set({ day: k ?? "" })}
            ariaLabel="Audit events per day"
            unit="events"
            height={110}
          />
        </section>
      )}

      <Toolbar>
        <SearchField value={v.q} onChange={(q) => set({ q })} placeholder="Search actor, action, object" label="Search the audit log" />
        <SelectField label="Actor" value={v.actor} onChange={(actor) => set({ actor })}>
          <option value="ALL">Everyone</option>
          {actors.map((a) => <option key={a} value={a}>{a}</option>)}
        </SelectField>
        <SelectField label="Action" value={v.action} onChange={(action) => set({ action })}>
          <option value="ALL">All actions</option>
          {actions.map((a) => <option key={a} value={a}>{a}</option>)}
        </SelectField>
        <SelectField label="Load" value={limit} onChange={(n) => set({ n })}>
          <option value="100">Last 100</option>
          <option value="200">Last 200</option>
          <option value="500">Last 500</option>
        </SelectField>
        {filtered && <button className="btn sm" onClick={() => set({ q: "", actor: "ALL", action: "ALL", day: "" })}><XCircle size={14} aria-hidden /> Clear filters</button>}
      </Toolbar>

      <section className="panel">
        <DataTable
          label="Audit log"
          rows={rows}
          rowKey={(e) => String(e.event_id)}
          columns={columns}
          loading={loading && !data}
          presorted
          sortKey={sort}
          sortDir={dir}
          onSort={(key, d) => set({ sort: key, dir: d })}
          resetKey={`${v.q}|${v.actor}|${v.action}|${v.day}|${limit}`}
          menu={menu}
          expand={(e) => (
            <dl className="facts" style={{ maxWidth: 640 }}>
              <dt>Event</dt><dd className="num" style={{ textAlign: "left" }}>{e.event_id}</dd>
              <dt>Time</dt><dd className="num small" style={{ textAlign: "left" }}>{e.ts}</dd>
              <dt>Actor</dt><dd style={{ textAlign: "left" }}>{e.actor_id}</dd>
              <dt>Action</dt><dd className="mono" style={{ textAlign: "left" }}>{e.action}</dd>
              <dt>Object</dt><dd className="mono" style={{ textAlign: "left" }}>{e.object_type} {e.object_id}</dd>
            </dl>
          )}
          defaultPageSize={25}
          empty={<Empty title={all.length ? "No event matches" : "No events yet"}>{all.length ? "" : "Actions appear here as people use the system."}</Empty>}
        />
      </section>
    </>
  );
}
