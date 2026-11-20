"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import type { CaseDetail } from "@/lib/api";
import { claimMix, NONE, runMix } from "@/lib/aggregate";
import { fmtScore, sentence } from "@/lib/format";
import { Chip, Empty, ErrorNotice, Skeleton, useLoad } from "./Bits";
import { Dialog } from "./ui/Dialog";

/** A side panel with the essentials of one case, so a row can be inspected without leaving the list. */
export function CaseDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data: c, error, loading, status } = useLoad<CaseDetail>(id ? `/cases/${id}` : null);
  const mix = c ? claimMix([c]) : {};
  const runs = c ? runMix([c]) : {};
  return (
    <Dialog
      open={id !== null}
      drawer
      title="Case summary"
      onClose={onClose}
      footer={id ? <Link className="btn primary" href={`/cases/${id}`} onClick={onClose}>Open full case <ArrowRight size={14} aria-hidden /></Link> : null}
    >
      {id && <div className="mono small" style={{ wordBreak: "break-all" }}>{id}</div>}
      {loading && !c ? <Skeleton rows={5} /> : status === 404 ? <Empty title="Case not found" /> : error ? <ErrorNotice message={error} /> : c && (
        <>
          <div className="row"><Chip value={c.status} />{c.disposition && <Chip value={c.disposition} />}</div>
          <dl className="facts">
            <dt>Account</dt><dd className="mono">{c.subject_ref}</dd>
            <dt>Score</dt><dd className="num">{fmtScore(c.priority_score)}</dd>
            <dt>Alerts</dt><dd className="num">{c.alerts}</dd>
            <dt>Opened</dt><dd className="num small">{new Date(c.opened_at).toLocaleString()}</dd>
            <dt>Assigned</dt><dd>{c.assigned_to ?? "nobody"}</dd>
          </dl>
          <section>
            <h3 style={{ marginBottom: 6 }}>Claims ({c.claims.length})</h3>
            {c.claims.length === 0 ? <span className="muted small">No claims yet.</span> : (
              <div className="row" style={{ gap: 6 }}>
                {Object.keys(mix).sort().map((k) => <span className="chip" key={k}>{k === NONE ? "Not verified" : sentence(k)} <b className="num">{mix[k]}</b></span>)}
              </div>
            )}
          </section>
          <section>
            <h3 style={{ marginBottom: 6 }}>Runs ({c.runs.length})</h3>
            {c.runs.length === 0 ? <span className="muted small">No runs yet.</span> : (
              <div className="row" style={{ gap: 6 }}>
                {Object.keys(runs).sort().map((k) => <span className="chip" key={k}>{sentence(k)} <b className="num">{runs[k]}</b></span>)}
              </div>
            )}
          </section>
        </>
      )}
    </Dialog>
  );
}
