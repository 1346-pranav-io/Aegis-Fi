"use client";

import { useMemo, useState } from "react";
import { Check, Copy, RefreshCw, X } from "lucide-react";
import { api, ApiError, type PendingApproval } from "@/lib/api";
import { copyText, relTime, sentence } from "@/lib/format";
import { useRefreshMs, useTick } from "@/lib/prefs";
import { compareText } from "@/lib/table";
import { useUrlState } from "@/lib/useUrlState";
import { useSession } from "@/components/Shell";
import { Empty, ErrorNotice, Notice, PageHead, useLoad } from "@/components/Bits";
import { SearchField, SelectField, Segmented, Toolbar } from "@/components/ui/Controls";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { ConfirmDialog } from "@/components/ui/Dialog";
import type { MenuItem } from "@/components/ui/Menu";
import { Tip } from "@/components/ui/Tip";
import { useToast } from "@/components/ui/Toast";

const DEFAULTS = { q: "", type: "ALL", who: "ALL" };

export default function ApprovalsPage() {
  const [session] = useSession();
  const toast = useToast();
  const [refreshMs] = useRefreshMs();
  const tick = useTick(refreshMs);
  const [manual, setManual] = useState(0);
  const { data, error, loading } = useLoad<PendingApproval[]>("/approvals/pending", tick + manual);
  const [v, set] = useUrlState(DEFAULTS);
  const [deciding, setDeciding] = useState<{ a: PendingApproval; approve: boolean } | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const isApprover = session?.roles.includes("approver") ?? false;
  const me = session?.user ?? "";

  const all = useMemo(() => data ?? [], [data]);
  const types = useMemo(() => [...new Set(all.map((a) => a.action_type))].sort(), [all]);
  const rows = useMemo(() => {
    const q = v.q.trim().toLowerCase();
    return all.filter(
      (a) =>
        (v.type === "ALL" || a.action_type === v.type) &&
        (v.who === "ALL" || (v.who === "MINE" ? a.requested_by === me : a.requested_by !== me)) &&
        (!q || `${a.subject_ref} ${a.requested_by} ${a.justification} ${a.request_id}`.toLowerCase().includes(q)),
    );
  }, [all, v.q, v.type, v.who, me]);
  const mineCount = all.filter((a) => a.requested_by === me).length;

  function begin(a: PendingApproval, approve: boolean) {
    setReason("");
    setDialogError("");
    setDeciding({ a, approve });
  }
  const whyNot = (a: PendingApproval) => (!isApprover ? "Deciding needs the approver role" : a.requested_by === me ? "You made this request, so someone else must decide it" : "");

  async function decide() {
    if (!deciding) return;
    setBusy(true);
    setDialogError("");
    try {
      await api(`/approvals/${deciding.a.request_id}/decision`, { method: "POST", body: { approve: deciding.approve, reason } });
      toast.ok(deciding.approve ? "Approved. The decision is stored with your identity." : "Denied. The decision is stored with your identity.");
      setDeciding(null);
      setManual((n) => n + 1);
    } catch (e) {
      setDialogError(e instanceof ApiError ? `${e.status}: ${e.message}` : "Something went wrong. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  const menu = (a: PendingApproval): MenuItem[] => {
    const why = whyNot(a);
    return [
      { label: why ? `Approve (${why.toLowerCase()})` : "Approve", icon: <Check size={16} aria-hidden />, disabled: !!why, onSelect: () => begin(a, true) },
      { label: "Deny", icon: <X size={16} aria-hidden />, tone: "danger", disabled: !!why, onSelect: () => begin(a, false) },
      { label: "Copy request id", icon: <Copy size={16} aria-hidden />, onSelect: async () => ((await copyText(a.request_id)) ? toast.ok("Request id copied.") : toast.error("Could not copy the id.")) },
    ];
  };

  const columns: Column<PendingApproval>[] = [
    { key: "type", header: "Request", sort: (a, b) => compareText(a.action_type, b.action_type), cell: (a) => <b style={{ fontWeight: 600 }}>{sentence(a.action_type)}</b> },
    { key: "subject", header: "Subject", hide: "md", sort: (a, b) => compareText(a.subject_ref, b.subject_ref), cell: (a) => <span className="mono">{a.subject_ref}</span> },
    { key: "by", header: "Requested by", sort: (a, b) => compareText(a.requested_by, b.requested_by), cell: (a) => <>{a.requested_by}{a.requested_by === me && <span className="muted"> (you)</span>}</> },
    { key: "at", header: "When", hide: "sm", sort: (a, b) => Date.parse(a.requested_at) - Date.parse(b.requested_at), cell: (a) => <Tip label={new Date(a.requested_at).toLocaleString()}><span tabIndex={0} className="muted small">{relTime(a.requested_at)}</span></Tip> },
    {
      key: "act", header: "Decision", cell: (a) => {
        const why = whyNot(a);
        return (
          <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
            <Tip label={why || "Approve"}><button className="btn sm primary" disabled={!!why} onClick={() => begin(a, true)}><Check size={14} aria-hidden /> Approve</button></Tip>
            <Tip label={why || "Deny"}><button className="btn sm danger" disabled={!!why} onClick={() => begin(a, false)}><X size={14} aria-hidden /> Deny</button></Tip>
          </div>
        );
      },
    },
  ];

  return (
    <>
      <PageHead actions={<button className="btn sm" aria-label="Refresh" onClick={() => setManual((n) => n + 1)} disabled={loading}><RefreshCw size={14} aria-hidden className={loading ? "spin" : ""} /> <span className="btn-label">Refresh</span></button>}>
        Consequential actions wait here for a second person. Your decision is stored with your verified identity, and the person who made the request cannot decide it.
      </PageHead>
      {!isApprover && session && <Notice tone="info">Your roles can read this list but not decide. Deciding needs the approver role.</Notice>}
      <ErrorNotice message={error} />

      <Toolbar>
        <SearchField value={v.q} onChange={(q) => set({ q })} placeholder="Search requests" label="Search approval requests" />
        <Segmented label="Whose requests" value={v.who} onChange={(who) => set({ who })} options={[{ value: "ALL", label: "All", count: all.length }, { value: "OTHERS", label: "From others", count: all.length - mineCount }, { value: "MINE", label: "Mine", count: mineCount }]} />
        <SelectField label="Type" value={v.type} onChange={(type) => set({ type })}>
          <option value="ALL">All types</option>
          {types.map((t) => <option key={t} value={t}>{sentence(t)}</option>)}
        </SelectField>
      </Toolbar>

      <section className="panel">
        <DataTable
          label="Pending approvals"
          rows={rows}
          rowKey={(a) => a.request_id}
          columns={columns}
          loading={loading && !data}
          defaultSort={{ key: "at", dir: "desc" }}
          resetKey={`${v.q}|${v.type}|${v.who}`}
          menu={menu}
          expand={(a) => (
            <dl className="facts" style={{ maxWidth: 640 }}>
              <dt>Justification</dt><dd style={{ textAlign: "left" }}>{a.justification}</dd>
              <dt>Subject</dt><dd className="mono" style={{ textAlign: "left" }}>{a.subject_ref}</dd>
              <dt>Request id</dt><dd className="mono muted" style={{ textAlign: "left" }}>{a.request_id}</dd>
              <dt>Requested</dt><dd className="num small" style={{ textAlign: "left" }}>{new Date(a.requested_at).toLocaleString()}</dd>
            </dl>
          )}
          empty={
            <Empty title={all.length ? "No request matches" : "Nothing is waiting"}>
              {all.length ? <button className="btn sm" style={{ marginTop: 8 }} onClick={() => set({ q: "", type: "ALL", who: "ALL" })}>Clear filters</button> : "Requests for case closure or feedback export show up here."}
            </Empty>
          }
        />
      </section>

      <ConfirmDialog
        open={deciding !== null}
        title={deciding ? `${deciding.approve ? "Approve" : "Deny"}: ${sentence(deciding.a.action_type).toLowerCase()}` : ""}
        confirmLabel={deciding?.approve ? "Approve" : "Deny"}
        tone={deciding?.approve ? undefined : "danger"}
        busy={busy}
        error={dialogError}
        reason={{ label: "Reason for your decision", value: reason }}
        onReason={setReason}
        onConfirm={() => void decide()}
        onCancel={() => setDeciding(null)}
      >
        {deciding && (
          <dl className="facts">
            <dt>Subject</dt><dd className="mono">{deciding.a.subject_ref}</dd>
            <dt>Requested by</dt><dd>{deciding.a.requested_by}</dd>
            <dt>Justification</dt><dd>{deciding.a.justification}</dd>
          </dl>
        )}
        <p className="muted small" style={{ margin: 0 }}>This decision is final and is written to the audit log with your identity.</p>
      </ConfirmDialog>
    </>
  );
}
