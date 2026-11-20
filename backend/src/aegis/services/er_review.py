"""Human entity-link decisions that trigger re-verification (reconciliation item C6).

A reviewer rejects or re-opens a link, supersedes it, or undoes a merge. Claims that relied on it
are then no longer on a confirmed footing, so this queues a targeted `reverify` run for each
affected case (services.reverify). The decision itself is written on the REVIEWER connection
(migration 0023); the lookup of affected claims and the queuing use the application connection.

Confirming a link does not trigger anything: it can only strengthen what claims rely on.
"""

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum

import psycopg

from aegis.domain.agents import Budget
from aegis.entities import repo
from aegis.evidence.store import EvidenceStore
from aegis.runtime.runner import InvestigationRunner
from aegis.services.reverify import start_reverification


class LinkChange(StrEnum):
    REJECT = "REJECT"
    REOPEN = "REOPEN"
    SUPERSEDE = "SUPERSEDE"
    UNMERGE = "UNMERGE"


def affected_cases(app_conn: psycopg.Connection, changed_ids: set[str]) -> list[str]:
    """Cases with a claim that now depends on an unconfirmed id among `changed_ids`."""
    flagged = repo.PgERLinkReader(app_conn).flagged_claims()
    return sorted({f.case_id for f in flagged if set(f.unconfirmed) & changed_ids})


def decide_and_reverify(
    *,
    change: LinkChange,
    target_id: str,
    actor: str,
    reason: str,
    reviewer_conn: psycopg.Connection,
    app_conn: psycopg.Connection,
    make_runner: Callable[[], InvestigationRunner],
    store: EvidenceStore,
    as_of: datetime,
    budget: Budget,
) -> dict[str, str]:
    """Apply the human decision, then queue one re-verification run per affected case.
    Returns {case_id: run_id}. If the decision is refused (not allowed, wrong state) nothing is
    queued and the error propagates."""
    if change is LinkChange.REJECT:
        repo.reject_link(reviewer_conn, target_id, actor, reason)
    elif change is LinkChange.REOPEN:
        repo.reopen_link(reviewer_conn, target_id, actor, reason)
    elif change is LinkChange.SUPERSEDE:
        repo.supersede_link(reviewer_conn, target_id, reason)
    else:
        repo.unmerge(reviewer_conn, target_id, actor, reason)
    reviewer_conn.commit()
    app_conn.commit()  # see the decision just written
    cases = affected_cases(app_conn, {target_id})
    if not cases:
        return {}
    return start_reverification(
        make_runner(), store, cases, {target_id}, as_of=as_of, budget=budget
    )
