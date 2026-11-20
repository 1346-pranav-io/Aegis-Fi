"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Database, RefreshCw } from "lucide-react";
import type { AuditEvent, PendingApproval, QueueItem } from "@/lib/api";
import { DB_ROLES, layers, MODULES, SCHEMAS } from "@/lib/architecture";
import { claimMix, runMix, sum, verifiedShare } from "@/lib/aggregate";
import { FLOW, stagesFor, type NodeId } from "@/lib/flow";
import { fmtScore, relTime } from "@/lib/format";
import { newSince } from "@/lib/timeseries";
import { useRefreshMs, useTick } from "@/lib/prefs";
import { useCaseSample } from "@/lib/useCaseSample";
import { ErrorNotice, PageHead, Skeleton, useLoad } from "@/components/Bits";
import { useSession } from "@/components/Shell";
import { Sparkline } from "@/components/ui/Sparkline";
import { Item, Stagger } from "@/components/ui/Reveal";

/** Round-trip time of GET /health through the same-origin proxy, with a short history. */
function useHealth(tick: number) {
  const [hist, setHist] = useState<number[]>([]);
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    const t0 = performance.now();
    fetch("/api/health", { cache: "no-store" }).then(
      (r) => {
        if (!live) return;
        setOk(r.ok);
        setHist((h) => [...h.slice(-19), Math.round(performance.now() - t0)]);
      },
      () => live && setOk(false),
    );
    return () => {
      live = false;
    };
  }, [tick]);
  return { ok, hist, last: hist[hist.length - 1] };
}

export default function SystemPage() {
  const [session] = useSession();
  const can = (...r: string[]) => !!session && session.roles.some((x) => r.includes(x));
  const [refreshMs] = useRefreshMs();
  const tick = useTick(refreshMs);
  const [manual, setManual] = useState(0);
  const key = tick + manual;

  const health = useHealth(key);
  const queue = useLoad<QueueItem[]>("/queue?limit=200", key);
  const approvals = useLoad<PendingApproval[]>(session && can("approver", "analyst") ? "/approvals/pending" : null, key);
  const audit = useLoad<AuditEvent[]>(session && can("auditor", "admin") ? "/audit?limit=100" : null, key);
  const all = useMemo(() => queue.data ?? [], [queue.data]);
  const top = useMemo(() => [...all].sort((a, b) => b.priority_score - a.priority_score), [all]);
  const sample = useCaseSample(useMemo(() => top.slice(0, 12).map((c) => c.case_id), [top]), key);
  const cm = useMemo(() => claimMix(sample.cases), [sample.cases]);
  const rm = useMemo(() => runMix(sample.cases), [sample.cases]);
  const events = useMemo(() => audit.data ?? [], [audit.data]);

  // Event-driven pulses: the first audit load sets a high-water mark; anything newer afterwards lights its stage.
  const maxId = events.reduce((m, e) => Math.max(m, e.event_id), 0);
  const [mark, setMark] = useState<number | null>(null);
  const [pulses, setPulses] = useState<Partial<Record<NodeId, number>>>({});
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  if (audit.data && mark !== maxId) {
    if (mark !== null) {
      const added = newSince(events, mark);
      if (added.length) {
        const stages = stagesFor(added.map((e) => e.action));
        setPulses((p) => {
          const n = { ...p };
          stages.forEach((s) => (n[s] = (n[s] ?? 0) + 1));
          return n;
        });
        setFresh((f) => new Set([...f, ...added.map((e) => e.event_id)]));
      }
    }
    setMark(maxId);
  }

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of all) c[q.status] = (c[q.status] ?? 0) + 1;
    return c;
  }, [all]);
  const open = all.length - (counts.CLOSED ?? 0);
  const share = verifiedShare(cm);
  const finished = (rm.COMPLETE ?? 0) + (rm.PARTIAL ?? 0);
  const val: Record<NodeId, { v: string; s: string }> = {
    ingest: { v: "n/a", s: "no endpoint exposes it" },
    detect: { v: top[0] ? fmtScore(top[0].priority_score) : "-", s: "highest score in queue" },
    cases: { v: String(open), s: `${counts.INVESTIGATING ?? 0} investigating, ${counts.PENDING_REVIEW ?? 0} in review` },
    runs: { v: String(sum(rm)), s: sum(rm) ? Object.entries(rm).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(", ") : "none yet" },
    claims: { v: String(sum(cm)), s: `top ${sample.cases.length} cases` },
    verify: { v: share === null ? "-" : `${share}%`, s: "of sampled claims verified" },
    report: { v: String(finished), s: "finished runs, sampled" },
    approval: { v: approvals.data ? String(approvals.data.length) : "n/a", s: can("approver", "analyst") ? "requests waiting" : "needs approver or analyst" },
    audit: { v: audit.data ? String(events.length) : "n/a", s: audit.data ? `latest ${events[0] ? relTime(events[0].ts) : "none"}` : "needs auditor or admin" },
  };
  // Stages also light up when their live number changes between refreshes (for example a run is queued, which writes no
  // audit event). Skipped while a refresh is in flight, so a half-loaded sample never looks like a change.
  const sig = Object.fromEntries(FLOW.map((n) => [n.id, `${val[n.id].v}|${val[n.id].s}`])) as Record<NodeId, string>;
  const settled = !!queue.data && !sample.loading;
  const [prevSig, setPrevSig] = useState<Record<NodeId, string> | null>(null);
  if (settled && JSON.stringify(prevSig) !== JSON.stringify(sig)) {
    if (prevSig) {
      const changed = FLOW.filter((n) => prevSig[n.id] !== sig[n.id]).map((n) => n.id);
      if (changed.length) setPulses((p) => { const n = { ...p }; changed.forEach((id) => (n[id] = (n[id] ?? 0) + 1)); return n; });
    }
    setPrevSig(sig);
  }
  const [sel, setSel] = useState<NodeId>("cases");
  const selected = FLOW.find((n) => n.id === sel) ?? FLOW[0];
  const [mod, setMod] = useState("runtime");
  const m = MODULES.find((x) => x.id === mod) ?? MODULES[0];
  const [role, setRole] = useState<string | null>(null);
  const busy = queue.loading || sample.loading;

  return (
    <>
      <PageHead actions={<button className="btn sm" aria-label="Refresh" onClick={() => setManual((n) => n + 1)} disabled={busy}><RefreshCw size={14} aria-hidden className={busy ? "spin" : ""} /> <span className="btn-label">Refresh</span></button>}>
        How a case moves from data to a decision, with the numbers behind each stage, and how the code is layered. The flow refreshes by itself; a stage lights up when a new audit event lands on it.
      </PageHead>
      <ErrorNotice message={queue.error} />

      <section className="panel" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <div className="grow"><h2>Live data flow</h2><p>Click a stage for what it does. Dashed arrows move left to right; a ring marks fresh activity.</p></div>
          <span className={`health ${health.ok === false ? "bad" : ""}`} role="status">
            <i />
            <span>{health.ok === null ? "Checking the API" : health.ok ? "API healthy" : "API unreachable"}{health.last !== undefined && health.ok ? <span className="muted num"> {health.last} ms</span> : null}</span>
            <Sparkline data={health.hist} width={64} height={22} color={health.ok === false ? "var(--bad)" : "var(--accent)"} />
          </span>
        </div>
        <div className="card-body">
          {queue.loading && !queue.data ? <Skeleton rows={5} /> : (
            <Stagger className="flow">
              {FLOW.map((n, i) => {
                const lit = (pulses[n.id] ?? 0) > 0;
                const last = i === FLOW.length - 1;
                return (
                  <Item key={n.id}>
                    <button className={`node ${last ? "last" : ""} ${(i + 1) % 5 === 0 ? "row-end" : ""} ${lit ? "live-edge" : ""}`} aria-pressed={sel === n.id} onClick={() => setSel(n.id)}>
                      {lit && <span key={pulses[n.id]} className="ring" aria-hidden />}
                      <span className="nh"><span className="num muted">{i + 1}</span> {n.title}</span>
                      <span className="nv num">{val[n.id].v}</span>
                      <span className="ns">{val[n.id].s}</span>
                      <span className="arrow" aria-hidden />
                    </button>
                  </Item>
                );
              })}
            </Stagger>
          )}
          <div className="panel" style={{ marginTop: 14, boxShadow: "none" }}>
            <div className="panel-body stack" aria-live="polite">
              <div className="row"><b>{selected.title}</b><span className="muted small">{selected.blurb}</span>{selected.href && <Link className="link-more" style={{ marginLeft: "auto" }} href={selected.href}>Open <ArrowRight size={14} aria-hidden /></Link>}</div>
              <p style={{ margin: 0 }}>{selected.detail}</p>
              {!selected.live && <p className="muted small" style={{ margin: 0 }}>Not measured live: the API has no endpoint for this stage.</p>}
            </div>
          </div>
        </div>
      </section>

      <div className="grid g-main">
        <section className="panel">
          <div className="card-head"><div className="grow"><h2>Recent events</h2><p>Newest first. New arrivals are highlighted for a moment.</p></div></div>
          {!can("auditor", "admin") ? (
            <div className="empty"><b>Auditor or admin role needed</b>The audit log feeds this list and the stage rings.</div>
          ) : audit.loading && !audit.data ? <Skeleton rows={4} /> : audit.error ? <div className="card-body"><ErrorNotice message={audit.error} /></div> : !events.length ? (
            <div className="empty"><b>No events yet</b></div>
          ) : (
            <div className="ticker" style={{ marginTop: 10 }}>
              {events.slice(0, 20).map((e) => (
                <div key={e.event_id} className={`list-row ${fresh.has(e.event_id) ? "new" : ""}`}>
                  <span className="mono grow">{e.action}</span>
                  <span className="muted small">{e.object_type}</span>
                  <span className="muted small">{relTime(e.ts)}</span>
                </div>
              ))}
            </div>
          )}
        </section>
        <section className="panel">
          <div className="card-head"><div className="grow"><h2>Data stores</h2><p>PostgreSQL is the only datastore. Click a login to see its rights.</p></div><Database size={18} aria-hidden className="muted" /></div>
          <div className="card-body stack">
            <div className="row" style={{ gap: 6 }}>{SCHEMAS.map((s) => <span key={s} className="chip">{s}</span>)}</div>
            <div className="row" style={{ gap: 6 }}>
              {DB_ROLES.map((r) => <button key={r.id} className="mod" aria-pressed={role === r.id} onClick={() => setRole(role === r.id ? null : r.id)}><span className="mono">{r.id}</span></button>)}
            </div>
            <p className="muted small" style={{ margin: 0 }} aria-live="polite">{role ? DB_ROLES.find((r) => r.id === role)?.rights : "Insert-only tables are protected by triggers, so even application code cannot update or delete them."}</p>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="card-head"><div className="grow"><h2>Architecture</h2><p>A modular monolith. A module may only use modules in layers below it, enforced by import-linter. Click a module.</p></div></div>
        <div className="card-body">
          <div className="arch">
            {layers().map((row) => (
              <div className="arch-row" key={row[0].id}>
                <span className="arch-tag">Layer {row[0].rank}</span>
                {row.map((x) => <button key={x.id} className="mod" aria-pressed={mod === x.id} onClick={() => setMod(x.id)}><span className="mono">{x.id}</span></button>)}
              </div>
            ))}
          </div>
          <div className="panel" style={{ marginTop: 14, boxShadow: "none" }}>
            <div className="panel-body stack" aria-live="polite">
              <div className="row"><b className="mono">{m.id}</b><span className="muted small">layer {m.rank}</span></div>
              <p style={{ margin: 0 }}>{m.job}</p>
              <dl className="facts">
                <dt>May use</dt><dd style={{ textAlign: "left" }}>{m.mayUse.length ? m.mayUse.map((d) => <button key={d} className="chip" style={{ marginRight: 4, border: 0, cursor: "pointer" }} onClick={() => setMod(d)}>{d}</button>) : "nothing else"}</dd>
                <dt>Must not</dt><dd style={{ textAlign: "left" }}>{m.mustNot}</dd>
              </dl>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
