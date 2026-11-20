"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import {
  Play, Search, Send, Lock, Download, ThumbsUp, ThumbsDown, Copy,
  ChevronRight, MoreHorizontal, Loader2,
} from "lucide-react";
import { api, ApiError, getSession, type CaseDetail, type Finding, type Report, type RunStatus } from "@/lib/api";
import { applyClaims, claimStatusCounts, NONE, statusOf, type ClaimSort } from "@/lib/claims";
import { copyText, downloadBlob, sentence } from "@/lib/format";
import { useUrlState } from "@/lib/useUrlState";
import {
  Chip, Collapsible, Empty, ErrorNotice, Evidence, Notice, Skeleton, splitFinding, typeTitle, useLoad,
} from "@/components/Bits";
import { HeaderActions } from "@/components/HeaderActions";
import { useSession } from "@/components/Shell";
import { Segmented, SearchField, SelectField, Toolbar } from "@/components/ui/Controls";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Menu, useMenu, type MenuItem } from "@/components/ui/Menu";
import { Item, Stagger } from "@/components/ui/Reveal";
import { Tip } from "@/components/ui/Tip";
import { useToast } from "@/components/ui/Toast";
import { AnimatePresence, motion } from "motion/react";

type Pending =
  | { kind: "investigate" }
  | { kind: "review" }
  | { kind: "closure" }
  | { kind: "agree" | "disagree"; claimId: string; text: string };

const COPY: Record<Pending["kind"], { title: string; confirm: string; label: string; need: boolean; body: string }> = {
  investigate: { title: "Start investigating", confirm: "Start investigating", label: "Reason for the change", need: true, body: "The case moves to Investigating and is recorded in the audit log." },
  review: { title: "Send to review", confirm: "Send to review", label: "What the reviewer should know", need: true, body: "The case moves to Pending review. Closing it afterwards needs a decision from a different person." },
  closure: { title: "Request closure", confirm: "Request closure", label: "Justification for closing", need: true, body: "This only creates a request. A different person must approve it before the case can be closed." },
  agree: { title: "Agree with this claim", confirm: "Record agreement", label: "Note (optional)", need: false, body: "Feedback records your view. It never edits the claim or its evidence." },
  disagree: { title: "Disagree with this claim", confirm: "Record disagreement", label: "Why do you disagree?", need: true, body: "Feedback records your view. It never edits the claim or its evidence." },
};

function FindingGroups({ items }: { items: Finding[] }) {
  const groups = useMemo(() => {
    const g = new Map<string, Array<{ key: string; value: string; f: Finding }>>();
    const loose: Finding[] = [];
    for (const f of items) {
      const s = splitFinding(f.text);
      if (!s) loose.push(f);
      else g.set(s.type, [...(g.get(s.type) ?? []), { key: s.key, value: s.value, f }]);
    }
    return { grouped: [...g.entries()], loose };
  }, [items]);
  return (
    <Stagger>
      {groups.grouped.map(([type, rows]) => (
        <Item key={type} className="group">
          <div className="group-head">
            <h3>{typeTitle(type)}</h3>
            <Evidence ids={[...new Set(rows.flatMap((r) => r.f.evidence_ids))]} />
          </div>
          <dl className="kv">
            {rows.map((r) => (
              <KeyValue key={r.f.claim_id} k={r.key} v={r.value} />
            ))}
          </dl>
        </Item>
      ))}
      {groups.loose.map((f) => (
        <Item key={f.claim_id} className="group">
          <div className="group-head"><h3>{f.text}</h3><Evidence ids={f.evidence_ids} /></div>
        </Item>
      ))}
    </Stagger>
  );
}

function KeyValue({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt>{k}</dt>
      <dd className="num">{v}</dd>
    </>
  );
}

const DEFAULTS = { cq: "", cs: "ALL", co: "status" };

export default function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [session] = useSession();
  const toast = useToast();
  const menu = useMenu();
  const detail = useLoad<CaseDetail>(`/cases/${id}`);
  const [tick, setTick] = useState(0);
  const report = useLoad<Report>(`/cases/${id}/report?format=json`, tick);
  const [activeRun, setActiveRun] = useState<string | null>(null);
  const [runInfo, setRunInfo] = useState<RunStatus | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [v, set] = useUrlState(DEFAULTS);
  const reloadDetail = detail.reload;

  const canWrite = !!session && session.roles.some((r) => r === "analyst" || r === "approver");

  // Follow a queued run until it finishes, then refresh the case and the report.
  useEffect(() => {
    if (!activeRun) return;
    let stop = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const r = await api<RunStatus>(`/runs/${activeRun}`);
        if (stop) return;
        setRunInfo(r);
        if (r.status === "QUEUED" || r.status === "RUNNING") {
          timer = window.setTimeout(poll, 2000);
        } else {
          setTick((n) => n + 1);
          void reloadDetail();
          setActiveRun(null);
          if (r.status === "FAILED") toast.error(`The run failed${r.reason ? `: ${r.reason}` : "."}`);
          else toast.ok(`The run finished: ${sentence(r.status).toLowerCase()}.`);
        }
      } catch (e) {
        if (!stop) toast.error(e instanceof ApiError ? `Could not read the run: ${e.message}` : "Could not read the run.");
      }
    };
    void poll();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [activeRun, reloadDetail, toast]);

  function begin(p: Pending) {
    setReason("");
    setDialogError("");
    setPending(p);
  }

  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setDialogError("");
    try {
      const k = pending.kind;
      if (k === "investigate") await api(`/cases/${id}/transition`, { method: "POST", body: { to: "INVESTIGATING", reason } });
      else if (k === "review") await api(`/cases/${id}/transition`, { method: "POST", body: { to: "PENDING_REVIEW", reason } });
      else if (k === "closure") await api(`/cases/${id}/closure-requests`, { method: "POST", body: { justification: reason } });
      else
        await api("/feedback", {
          method: "POST",
          body: { case_id: id, target_type: "CLAIM", target_id: pending.claimId, action: k === "agree" ? "ACCEPT" : "REJECT", reason: reason.trim() || null },
        });
      toast.ok(
        k === "investigate" ? "Case is now being investigated." : k === "review" ? "Sent to review." :
        k === "closure" ? "Closure requested. A different person must approve it." : "Feedback recorded.",
      );
      setPending(null);
      await reloadDetail();
    } catch (e) {
      setDialogError(e instanceof ApiError ? `${e.status}: ${e.message}` : "Something went wrong. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  async function startRun() {
    try {
      const r = await api<{ run_id: string }>(`/cases/${id}/runs`, { method: "POST", body: { policy: "rule_adaptive" } });
      toast.info("Run queued. This page follows it.");
      setActiveRun(r.run_id);
    } catch (e) {
      toast.error(e instanceof ApiError ? `${e.status}: ${e.message}` : "Could not queue the run.");
    }
  }

  async function downloadMarkdown() {
    const s = getSession();
    if (!s) return;
    setExporting(true);
    try {
      const res = await fetch(`/api/cases/${id}/report?format=markdown`, { headers: { Authorization: `Bearer ${s.token}` } });
      if (!res.ok) throw new Error(String(res.status));
      downloadBlob(await res.blob(), `${id}-report.md`);
      toast.ok("Report downloaded.");
    } catch {
      toast.error("The export failed. Try again in a moment.");
    } finally {
      setExporting(false);
    }
  }

  const copy = useCallback(
    async (text: string, what: string) => ((await copyText(text)) ? toast.ok(`${what} copied.`) : toast.error(`Could not copy the ${what.toLowerCase()}.`)),
    [toast],
  );

  const c = detail.data;
  const rep = report.data;
  const noReport = report.status === 404;
  const closed = c?.status === "CLOSED";
  const notFindings = rep ? [...rep.body.partially_supported, ...rep.body.not_findings] : [];
  const counts = useMemo(() => claimStatusCounts(c?.claims ?? []), [c]);
  const claims = useMemo(() => applyClaims(c?.claims ?? [], v.cq, v.cs, v.co as ClaimSort), [c, v.cq, v.cs, v.co]);
  const toggle = (cid: string) => setOpen((s) => { const n = new Set(s); if (n.has(cid)) n.delete(cid); else n.add(cid); return n; });
  const spec = pending ? COPY[pending.kind] : null;

  function claimMenu(cl: CaseDetail["claims"][number]): MenuItem[] {
    return [
      { label: "Copy claim id", icon: <Copy size={16} aria-hidden />, onSelect: () => void copy(cl.claim_id, "Claim id") },
      { label: "Copy claim text", icon: <Copy size={16} aria-hidden />, onSelect: () => void copy(cl.text, "Claim text") },
      { label: "Agree", icon: <ThumbsUp size={16} aria-hidden />, disabled: !canWrite, onSelect: () => begin({ kind: "agree", claimId: cl.claim_id, text: cl.text }) },
      { label: "Disagree", icon: <ThumbsDown size={16} aria-hidden />, disabled: !canWrite, onSelect: () => begin({ kind: "disagree", claimId: cl.claim_id, text: cl.text }) },
    ];
  }

  return (
    <>
      <HeaderActions>
        {c && <Chip value={c.status} />}
        {c?.disposition && <Chip value={c.disposition} />}
        <Tip label="Copy case id"><button className="btn sm" onClick={() => void copy(id, "Case id")}><Copy size={14} aria-hidden /> Copy id</button></Tip>
      </HeaderActions>
      {c && <p className="lede">Account <span className="mono">{c.subject_ref}</span>. Opened {new Date(c.opened_at).toLocaleString()}.</p>}
      <ErrorNotice message={detail.status === 404 ? "" : detail.error} />
      {detail.status === 404 && (
        <div className="panel">
          <Empty title="Case not found">This case does not exist, or you cannot see it. <Link href="/queue">Back to the queue</Link></Empty>
        </div>
      )}

      {detail.status !== 404 && (
        <div className="case-grid" style={{ marginTop: 12 }}>
          <div className="stack">
            {/* ---------------------------------------------------------------- report */}
            <section className="panel">
              <div className="panel-head">
                <h2>Report</h2>
                {rep && (
                  <button className="btn sm" onClick={() => void downloadMarkdown()} disabled={exporting}>
                    {exporting ? <Loader2 size={14} className="spin" aria-hidden /> : <Download size={14} aria-hidden />} Markdown
                  </button>
                )}
              </div>
              {report.loading && !rep && !noReport ? (
                <Skeleton rows={5} />
              ) : noReport ? (
                <Empty title="No report yet">Run an investigation, then process the queue with the worker.</Empty>
              ) : report.error && !rep ? (
                <div className="panel-body stack">
                  <ErrorNotice message={report.error} />
                  <div><button className="btn sm" onClick={() => void report.reload()}>Try again</button></div>
                </div>
              ) : rep ? (
                <>
                  <div className="panel-body stack" style={{ paddingBottom: 8 }}>
                    <Notice>
                      <b>Placeholder template.</b> {(rep.body.report_format?.note ?? "").replace(/^PLACEHOLDER structure\.\s*/i, "")}
                    </Notice>
                    <div className="muted small">
                      Version <span className="num">{rep.version}</span>, data as of{" "}
                      <span className="num">{new Date(rep.body.case.as_of).toLocaleString()}</span>, run{" "}
                      <span className="mono">{rep.run_id}</span>, hash <span className="mono">{rep.body_hash.slice(0, 12)}</span>
                      {rep.body.citation_coverage && (
                        <>, <span className="num">{rep.body.citation_coverage.cited}</span> of{" "}
                        <span className="num">{rep.body.citation_coverage.total}</span> findings cited</>
                      )}
                    </div>
                  </div>
                  {rep.body.findings.items.length === 0 ? (
                    <Empty title="No verified findings">No claim passed verification, so the report states none.</Empty>
                  ) : (
                    <FindingGroups items={rep.body.findings.items} />
                  )}
                </>
              ) : null}
            </section>

            {rep && (notFindings.length > 0 || rep.body.open_contradictions.length > 0) && (
              <Collapsible
                title="Not findings and open contradictions"
                meta={<span className="chip">{notFindings.length + rep.body.open_contradictions.length}</span>}
              >
                {notFindings.length > 0 && (
                  <div className="tbl-wrap">
                    <table className="tbl">
                      <tbody>
                        {notFindings.map((r) => (
                          <tr key={r.claim_id}>
                            <td style={{ width: 150 }}><Chip value={r.status} /></td>
                            <td>{r.text}</td>
                            <td style={{ width: 110 }}><Evidence ids={r.evidence_ids} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {rep.body.open_contradictions.length > 0 && (
                  <div className="panel-body"><pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(rep.body.open_contradictions, null, 2)}</pre></div>
                )}
              </Collapsible>
            )}

            {rep && (
              <Collapsible title="Limitations" meta={<span className="chip">{rep.body.limitations.length}</span>}>
                <ul className="panel-body" style={{ margin: 0, paddingLeft: 34, display: "flex", flexDirection: "column", gap: 6 }}>
                  {rep.body.limitations.map((l, i) => <li key={i}>{l}</li>)}
                </ul>
              </Collapsible>
            )}

            {/* ---------------------------------------------------------------- claims */}
            <section className="panel">
              <div className="panel-head">
                <h2>Claims</h2>
                <span className="muted small hide-sm">Feedback records your view. It never edits a claim or its evidence.</span>
              </div>
              {detail.loading && !c ? (
                <Skeleton rows={4} />
              ) : !c?.claims.length ? (
                <Empty title="No claims yet">Claims appear after an investigation runs.</Empty>
              ) : (
                <>
                  <div className="panel-body" style={{ paddingBottom: 0 }}>
                    <Toolbar>
                      <SearchField value={v.cq} onChange={(cq) => set({ cq })} placeholder="Search claims" label="Search claims" />
                      <Segmented
                        label="Filter claims by verification"
                        value={v.cs}
                        onChange={(cs) => set({ cs })}
                        options={[
                          { value: "ALL", label: "All", count: counts.ALL },
                          ...Object.keys(counts).filter((k) => k !== "ALL").map((k) => ({ value: k, label: k === NONE ? "Not verified" : sentence(k), count: counts[k] })),
                        ]}
                      />
                      <SelectField label="Sort" value={v.co} onChange={(co) => set({ co })}>
                        <option value="status">Needs attention first</option>
                        <option value="text">Text</option>
                        <option value="confidence">Confidence</option>
                      </SelectField>
                    </Toolbar>
                  </div>
                  {claims.length === 0 ? (
                    <Empty title="No claim matches"><button className="btn sm" style={{ marginTop: 8 }} onClick={() => set({ cq: "", cs: "ALL" })}>Clear filters</button></Empty>
                  ) : (
                    <div className="tbl-wrap">
                      <table className="tbl">
                        <thead><tr><th style={{ width: 34 }}><span className="sr-only">Details</span></th><th>Claim</th><th>Verification</th><th className="hide-sm">Confidence, self-reported</th><th><span className="sr-only">Actions</span></th></tr></thead>
                        <tbody>
                          {claims.map((cl) => {
                            const isOpen = open.has(cl.claim_id);
                            return (
                              <ClaimRow
                                key={cl.claim_id}
                                cl={cl}
                                isOpen={isOpen}
                                canWrite={canWrite}
                                onToggle={() => toggle(cl.claim_id)}
                                onAgree={() => begin({ kind: "agree", claimId: cl.claim_id, text: cl.text })}
                                onDisagree={() => begin({ kind: "disagree", claimId: cl.claim_id, text: cl.text })}
                                onMenu={(e) => menu.openAt(e, claimMenu(cl))}
                                onMore={(el) => menu.openFor(el, claimMenu(cl))}
                                onCopy={(t, w) => void copy(t, w)}
                              />
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </section>
          </div>

          {/* ------------------------------------------------------------------ rail */}
          <aside className="rail">
            <section className="panel">
              <div className="panel-head"><h2>Case</h2></div>
              <div className="panel-body">
                {!c ? <Skeleton rows={4} /> : (
                  <dl className="facts">
                    <dt>Score</dt><dd className="num">{c.priority_score.toFixed(4)}</dd>
                    <dt>Alerts</dt><dd className="num">{c.alerts}</dd>
                    <dt>Opened</dt><dd className="num small">{new Date(c.opened_at).toLocaleString()}</dd>
                    <dt>Assigned</dt><dd>{c.assigned_to ?? "nobody"}</dd>
                  </dl>
                )}
              </div>
            </section>

            <section className="panel">
              <div className="panel-head"><h2>Actions</h2></div>
              <div className="panel-body stack">
                {session && !canWrite && <Notice tone="info">Your roles can read this case but not change it.</Notice>}
                <button className="btn primary" disabled={closed || !canWrite || !!activeRun} onClick={() => void startRun()}>
                  {activeRun ? <Loader2 size={14} className="spin" aria-hidden /> : <Play size={14} aria-hidden />}
                  {activeRun ? "Run in progress" : "Run investigation"}
                </button>
                <button className="btn" disabled={!canWrite || closed || c?.status === "INVESTIGATING" || !c} onClick={() => begin({ kind: "investigate" })}>
                  <Search size={14} aria-hidden /> Start investigating
                </button>
                <button className="btn" disabled={!canWrite || c?.status !== "INVESTIGATING"} onClick={() => begin({ kind: "review" })}>
                  <Send size={14} aria-hidden /> Send to review
                </button>
                <button className="btn" disabled={!canWrite || c?.status !== "PENDING_REVIEW"} onClick={() => begin({ kind: "closure" })}>
                  <Lock size={14} aria-hidden /> Request closure
                </button>
                <span className="help muted small">
                  Some actions are only available in some states. A queued run is processed by the worker (<code>aegis worker</code>).
                </span>
              </div>
            </section>

            {(runInfo || c?.runs.length) && (
              <section className="panel">
                <div className="panel-head"><h2>Runs</h2></div>
                <div className="panel-body stack">
                  <AnimatePresence initial={false}>
                    {runInfo && (
                      <motion.div key="live" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} style={{ overflow: "hidden" }}>
                        <div className="row"><span className="mono small">{runInfo.run_id.slice(0, 16)}</span><Chip value={runInfo.status} /></div>
                        {runInfo.reason && <div className="muted small" style={{ marginTop: 4 }}>{runInfo.reason}</div>}
                        <div className="row" style={{ marginTop: 6, gap: 6 }}>
                          {runInfo.steps.map((s, i) => <Tip key={i} label={sentence(s.status)}><span className="chip" tabIndex={0}>{s.agent.toLowerCase()}</span></Tip>)}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                  {c?.runs.map((r) => (
                    <div key={r.run_id} className="row">
                      <span className="mono small grow">{r.run_id.slice(0, 16)}</span><Chip value={r.status} />
                    </div>
                  ))}
                </div>
              </section>
            )}
          </aside>
        </div>
      )}

      <ConfirmDialog
        open={pending !== null}
        title={spec?.title ?? ""}
        confirmLabel={spec?.confirm ?? "Confirm"}
        busy={busy}
        error={dialogError}
        reason={spec ? { label: spec.label, value: reason, required: spec.need } : undefined}
        onReason={setReason}
        onConfirm={() => void confirm()}
        onCancel={() => setPending(null)}
      >
        <p style={{ margin: 0 }}>{spec?.body}</p>
        {pending && (pending.kind === "agree" || pending.kind === "disagree") && (
          <blockquote className="quote">{pending.text}</blockquote>
        )}
      </ConfirmDialog>
      <Menu state={menu.state} onClose={menu.close} />
    </>
  );
}

function ClaimRow({
  cl, isOpen, canWrite, onToggle, onAgree, onDisagree, onMenu, onMore, onCopy,
}: {
  cl: CaseDetail["claims"][number];
  isOpen: boolean;
  canWrite: boolean;
  onToggle: () => void;
  onAgree: () => void;
  onDisagree: () => void;
  onMenu: (e: React.MouseEvent) => void;
  onMore: (el: HTMLElement) => void;
  onCopy: (text: string, what: string) => void;
}) {
  return (
    <>
      <tr onContextMenu={(e) => { e.preventDefault(); onMenu(e); }}>
        <td>
          <button className="expander" aria-expanded={isOpen} aria-label={isOpen ? "Hide claim details" : "Show claim details"} onClick={onToggle}>
            <ChevronRight size={13} aria-hidden />
          </button>
        </td>
        <td>{cl.text}</td>
        <td><Chip value={statusOf(cl) === NONE ? null : cl.verification_status} /></td>
        <td className="small hide-sm">{cl.self_reported_confidence ? cl.self_reported_confidence.toLowerCase() : "none"}</td>
        <td>
          <div className="row" style={{ justifyContent: "flex-end", flexWrap: "nowrap", gap: 6 }}>
            <Tip label={canWrite ? "Agree" : "Your roles cannot give feedback"}>
              <button className="btn sm icon" aria-label="Agree with this claim" disabled={!canWrite} onClick={onAgree}><ThumbsUp size={14} aria-hidden /></button>
            </Tip>
            <Tip label={canWrite ? "Disagree" : "Your roles cannot give feedback"}>
              <button className="btn sm icon" aria-label="Disagree with this claim" disabled={!canWrite} onClick={onDisagree}><ThumbsDown size={14} aria-hidden /></button>
            </Tip>
            <button className="btn sm icon" aria-label="More actions" aria-haspopup="menu" onClick={(e) => onMore(e.currentTarget)}><MoreHorizontal size={16} aria-hidden /></button>
          </div>
        </td>
      </tr>
      <AnimatePresence initial={false}>
        {isOpen && (
          <tr>
            <td colSpan={5} style={{ padding: 0, borderBottom: 0 }}>
              <motion.div className="claim-more" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.18 }} style={{ overflow: "hidden" }}>
                <dl className="facts" style={{ paddingTop: 10 }}>
                  <dt>Claim id</dt><dd><button className="copy-id mono" onClick={() => onCopy(cl.claim_id, "Claim id")}>{cl.claim_id}</button></dd>
                  <dt>Kind</dt><dd>{sentence(cl.claim_kind)}</dd>
                  <dt>Confidence</dt><dd>{cl.self_reported_confidence ? `${cl.self_reported_confidence.toLowerCase()} (self-reported, ordinal)` : "none given"}</dd>
                  <dt>Entity links relied on</dt>
                  <dd className="mono">{cl.depends_on_er_links.length ? cl.depends_on_er_links.join(", ") : "none"}</dd>
                </dl>
              </motion.div>
            </td>
          </tr>
        )}
      </AnimatePresence>
    </>
  );
}
