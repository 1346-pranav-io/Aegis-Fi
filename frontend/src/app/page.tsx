"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Activity, ArrowRight, Calendar, BadgeCheck, Command, Inbox, Network, RefreshCw, ShieldCheck, ClipboardCheck, ScrollText } from "lucide-react";
import type { AuditEvent, PendingApproval, QueueItem } from "@/lib/api";
import { claimMix, NONE, runMix, sum, verifiedShare } from "@/lib/aggregate";
import { fmtScore, relTime, sentence } from "@/lib/format";
import { useRefreshMs, useTick } from "@/lib/prefs";
import { statusCounts, STATUSES } from "@/lib/queue";
import { bucketEvents, categoryOf, periodDelta, rangeBounds, RANGES, type RangeKey } from "@/lib/timeseries";
import { useCaseSample } from "@/lib/useCaseSample";
import { useUrlState } from "@/lib/useUrlState";
import { Chip, Empty, ErrorNotice, PageHead, Skeleton, useLoad } from "@/components/Bits";
import { ScoreChart } from "@/components/ScoreChart";
import { useSession } from "@/components/Shell";
import { AreaChart, type Series } from "@/components/ui/AreaChart";
import { Donut } from "@/components/ui/Donut";
import { Kpi } from "@/components/ui/Kpi";
import { Item, Stagger } from "@/components/ui/Reveal";

const DEFAULTS = { range: "7d" };
const STATUS_COLOR: Record<string, string> = { OPEN: "var(--c-open)", INVESTIGATING: "var(--c-inv)", PENDING_REVIEW: "var(--c-pend)", CLOSED: "var(--c-closed)" };
const VERIFY_COLOR: Record<string, string> = {
  VERIFIED: "var(--ok)", PARTIALLY_SUPPORTED: "var(--amber)", UNVERIFIABLE: "var(--c-open)", UNSUPPORTED: "var(--bad)", CONTRADICTED: "var(--rose)", [NONE]: "var(--line-strong)",
};
const CAT_COLOR: Record<string, string> = { case: "var(--accent)", run: "var(--blue)", approval: "var(--amber)", feedback: "var(--violet)", evidence: "var(--rose)", report: "var(--c-open)", other: "var(--muted)" };

function Card({ title, sub, action, children, pad = true }: { title: string; sub?: string; action?: React.ReactNode; children: React.ReactNode; pad?: boolean }) {
  return (
    <section className="panel">
      <div className="card-head">
        <div className="grow"><h2>{title}</h2>{sub && <p>{sub}</p>}</div>
        {action}
      </div>
      {pad ? <div className="card-body">{children}</div> : <div style={{ paddingTop: 10 }}>{children}</div>}
    </section>
  );
}

export default function OverviewPage() {
  const [session] = useSession();
  const router = useRouter();
  const can = (...r: string[]) => !!session && session.roles.some((x) => r.includes(x));
  const canAudit = can("auditor", "admin");
  const canApprovals = can("approver", "analyst");
  const [refreshMs] = useRefreshMs();
  const tick = useTick(refreshMs);
  const [v, set] = useUrlState(DEFAULTS);
  const range: RangeKey = (["24h", "7d", "30d", "all"] as const).find((r) => r === v.range) ?? "7d";
  const [manual, setManual] = useState(0);
  const key = tick + manual;

  const queue = useLoad<QueueItem[]>("/queue?limit=200", key);
  const approvals = useLoad<PendingApproval[]>(session && canApprovals ? "/approvals/pending" : null, key);
  const audit = useLoad<AuditEvent[]>(session && canAudit ? "/audit?limit=500" : null, key);

  const all = useMemo(() => queue.data ?? [], [queue.data]);
  const counts = useMemo(() => statusCounts(all), [all]);
  const top = useMemo(() => [...all].sort((a, b) => b.priority_score - a.priority_score), [all]);
  const sampleIds = useMemo(() => top.slice(0, 12).map((c) => c.case_id), [top]);
  const sample = useCaseSample(sampleIds, key);
  const cm = useMemo(() => claimMix(sample.cases), [sample.cases]);
  const rm = useMemo(() => runMix(sample.cases), [sample.cases]);
  const share = verifiedShare(cm);

  // Activity over time (audit log), bucketed to the chosen range.
  const events = useMemo(() => audit.data ?? [], [audit.data]);
  const now = audit.updatedAt || queue.updatedAt; // when the data arrived; keeps render free of clock reads
  const { from, to } = useMemo(() => rangeBounds(range, now, events), [range, events]); // eslint-disable-line react-hooks/exhaustive-deps
  const buckets = useMemo(() => bucketEvents(events, from, to), [events, from, to]);
  const delta = useMemo(() => periodDelta(events, from, to), [events, from, to]);
  const cats = useMemo(() => [...new Set(events.filter((e) => Date.parse(e.ts) >= from).map((e) => categoryOf(e.action)))], [events, from]);
  const series: Series[] = cats.map((c) => ({ key: c, label: sentence(c), color: CAT_COLOR[c] ?? "var(--muted)", data: buckets.map((b) => b.by[c] ?? 0) }));
  const wide = to - from > 36 * 3600_000;
  const fmt = (x: number, long: boolean) => {
    const d = new Date(x);
    return wide ? d.toLocaleDateString(undefined, long ? { weekday: "short", day: "numeric", month: "short" } : { day: "numeric", month: "short" }) : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  };

  const waiting = (approvals.data ?? []).filter((a) => a.requested_by !== session?.user);
  const open = all.length - (counts.CLOSED ?? 0);
  const updated = Math.max(queue.updatedAt, audit.updatedAt, approvals.updatedAt);
  const busy = queue.loading || audit.loading || approvals.loading;
  const loadingFirst = queue.loading && !queue.data;

  return (
    <>
      <PageHead
        actions={
          <>
            {canAudit && (
              <label className="rangechip">
                <Calendar size={14} aria-hidden />
                <span className="sr-only">Time range</span>
                <select value={range} onChange={(e) => set({ range: e.target.value })}>
                  {RANGES.map((r) => <option key={r.key} value={r.key}>{r.key === "all" ? "All time" : `Last ${r.label}`}</option>)}
                </select>
              </label>
            )}
            <span className={`live ${refreshMs ? "" : "off"}`} title={updated ? `Updated ${new Date(updated).toLocaleTimeString()}` : undefined}><i />{refreshMs ? "Live" : "Paused"}</span>
            <button className="btn sm" aria-label="Refresh" onClick={() => setManual((n) => n + 1)} disabled={busy}><RefreshCw size={14} aria-hidden className={busy ? "spin" : ""} /> <span className="btn-label">Refresh</span></button>
          </>
        }
      >
        Where the work is, what is waiting for a second person, and how well the findings are supported. Figures refresh by themselves; change the interval in Settings.
      </PageHead>
      <ErrorNotice message={queue.error} />

      <Stagger className="kpis">
        <Item><Kpi label="Open cases" value={String(open)} icon={Inbox} href="/queue" hint={`of ${all.length} in the queue`} loading={loadingFirst} /></Item>
        <Item><Kpi label="Waiting for review" value={String(counts.PENDING_REVIEW ?? 0)} icon={ClipboardCheck} href="/queue?status=PENDING_REVIEW" hint="needs a closure decision" loading={loadingFirst} /></Item>
        {canApprovals && (
          <Item><Kpi label="Approvals waiting" value={approvals.data ? String(approvals.data.length) : "-"} icon={BadgeCheck} href="/approvals" hint={can("approver") ? `${waiting.length} need your decision` : "approvers decide"} loading={approvals.loading && !approvals.data} /></Item>
        )}
        <Item>
          <Kpi
            label="Claims verified"
            value={share === null ? "-" : `${share}%`}
            icon={ShieldCheck}
            href="/system"
            hint={share === null ? "no claims yet" : `${sum(cm)} claims, top ${sample.cases.length} cases${sample.failed ? `, ${sample.failed} unreadable` : ""}`}
            loading={sample.loading && !sample.cases.length}
          />
        </Item>
        {canAudit && (
          <Item>
            <Kpi
              label={`Activity, ${RANGES.find((r) => r.key === range)?.label.toLowerCase()}`}
              value={String(delta.cur)}
              icon={Activity}
              href="/audit"
              spark={buckets.map((b) => b.total)}
              delta={delta.pct === null ? undefined : { text: `${delta.pct >= 0 ? "+" : ""}${Math.round(delta.pct)}%`, tone: delta.pct > 0 ? "up" : delta.pct < 0 ? "down" : "flat" }}
              hint={delta.pct === null ? "no earlier period to compare" : "vs the period before"}
              loading={audit.loading && !audit.data}
            />
          </Item>
        )}
      </Stagger>

      <div className="grid g-main">
        {canAudit ? (
          <Card title="Activity" sub="Recorded actions over time, by kind. Click a name to hide it; hover for values.">
            {audit.loading && !audit.data ? <Skeleton rows={5} /> : audit.error ? <ErrorNotice message={audit.error} /> : !events.length ? (
              <Empty title="No activity yet">Actions appear here as people use the system.</Empty>
            ) : (
              <AreaChart xs={buckets.map((b) => b.start)} series={series} xTick={(x) => fmt(x, false)} xTip={(x) => fmt(x, true)} ariaLabel={`Audit events over the last ${RANGES.find((r) => r.key === range)?.label.toLowerCase()}`} />
            )}
          </Card>
        ) : (
          <Card title="Detector scores" sub="Cases by score range, stacked by status. Click a bar to open that range in the queue.">
            {loadingFirst ? <Skeleton rows={5} /> : <ScoreChart items={all} bin={null} onBin={(i) => { if (i !== null) router.push(`/queue?bin=${i}`); }} />}
          </Card>
        )}
        <Card title="Cases by status" sub="Click a slice to open it in the queue.">
          {loadingFirst ? <Skeleton rows={4} /> : (
            <Donut
              ariaLabel="Cases by status"
              unit="cases"
              centreLabel="cases"
              slices={STATUSES.map((s) => ({ key: s, label: sentence(s), value: counts[s] ?? 0, color: STATUS_COLOR[s] }))}
              onPick={(s) => router.push(`/queue?status=${s}`)}
            />
          )}
        </Card>
      </div>

      <div className="grid g-even">
        {canAudit && (
          <Card title="Detector scores" sub="Cases by score range, stacked by status. Pick a bar to filter the queue.">
            {loadingFirst ? <Skeleton rows={5} /> : <ScoreChart items={all} bin={null} onBin={(i) => { if (i !== null) router.push(`/queue?bin=${i}`); }} />}
          </Card>
        )}
        <Card title="Evidence quality" sub={`Claim verification across the ${sample.cases.length || "top"} highest-scored cases.`}>
          {sample.loading && !sample.cases.length ? <Skeleton rows={4} /> : sum(cm) === 0 ? (
            <Empty title="No claims yet">Run an investigation on a case to produce claims.</Empty>
          ) : (
            <Donut
              ariaLabel="Claims by verification status"
              unit="claims"
              centreLabel="claims"
              slices={[...Object.keys(cm)].sort().map((k) => ({ key: k, label: k === NONE ? "Not verified" : sentence(k), value: cm[k], color: VERIFY_COLOR[k] ?? "var(--muted)" }))}
            />
          )}
        </Card>
        {!canAudit && (
          <Card title="Investigation runs" sub="Run outcomes across the sampled cases.">
            {sample.loading && !sample.cases.length ? <Skeleton rows={3} /> : sum(rm) === 0 ? <Empty title="No runs yet" /> : (
              <div className="dist">
                {Object.keys(rm).sort().map((k) => (
                  <div className="dist-row" key={k}>
                    <div className="top"><span>{sentence(k)}</span><span className="num">{rm[k]}</span></div>
                    <div className="bar-track"><div className="bar-fill" style={{ width: `${(rm[k] / sum(rm)) * 100}%`, background: k === "COMPLETE" ? "var(--ok)" : k === "PARTIAL" ? "var(--amber)" : k === "FAILED" ? "var(--bad)" : "var(--blue)" }} /></div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        )}
      </div>

      <div className="grid g-main">
        <Card title="Top of the queue" sub="Highest detector score first. A score ranks cases; it is not a probability." pad={false} action={<Link className="link-more" href="/queue">View all <ArrowRight size={14} aria-hidden /></Link>}>
          {loadingFirst ? <Skeleton rows={5} /> : !top.length ? <Empty title="No cases yet" /> : top.slice(0, 6).map((c) => (
            <Link key={c.case_id} href={`/cases/${c.case_id}`} className="list-row">
              <span className="tile-ico"><Inbox size={15} aria-hidden /></span>
              <span className="mono grow" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{c.case_id}</span>
              <span className="num">{fmtScore(c.priority_score)}</span>
              <Chip value={c.status} />
            </Link>
          ))}
        </Card>
        <div className="stack">
          <Card title="Quick actions">
            <div className="qa">
              {top[0] && <Link href={`/cases/${top[0].case_id}`}><Inbox size={16} aria-hidden /> Open highest-scored case</Link>}
              {canApprovals && <Link href="/approvals"><BadgeCheck size={16} aria-hidden /> Review approvals</Link>}
              {canAudit && <Link href="/audit"><ScrollText size={16} aria-hidden /> Open the audit log</Link>}
              <Link href="/system"><Network size={16} aria-hidden /> See the live data flow</Link>
              <button onClick={() => window.dispatchEvent(new Event("aegis:palette"))}><Command size={16} aria-hidden /> Search everything</button>
            </div>
          </Card>
          {canAudit ? (
            <Card title="Latest activity" pad={false} action={<Link className="link-more" href="/audit">Audit log <ArrowRight size={14} aria-hidden /></Link>}>
              {audit.loading && !audit.data ? <Skeleton rows={3} /> : !events.length ? <Empty title="No events yet" /> : events.slice(0, 6).map((e) => (
                <div key={e.event_id} className="list-row">
                  <span className="grow"><span className="mono">{e.action}</span><br /><span className="muted small">{e.object_type} {e.object_id.slice(0, 18)}</span></span>
                  <span className="muted small">{relTime(e.ts)}</span>
                </div>
              ))}
            </Card>
          ) : canApprovals ? (
            <Card title="Waiting for a decision" pad={false} action={<Link className="link-more" href="/approvals">Open <ArrowRight size={14} aria-hidden /></Link>}>
              {approvals.loading && !approvals.data ? <Skeleton rows={2} /> : !approvals.data?.length ? <Empty title="Nothing is waiting" /> : approvals.data.slice(0, 5).map((a) => (
                <Link key={a.request_id} href="/approvals" className="list-row">
                  <span className="grow">{sentence(a.action_type)}<span className="muted small"> by {a.requested_by}</span></span>
                  <span className="muted small">{relTime(a.requested_at)}</span>
                </Link>
              ))}
            </Card>
          ) : null}
        </div>
      </div>
    </>
  );
}
