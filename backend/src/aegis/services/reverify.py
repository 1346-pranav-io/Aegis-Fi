"""Re-verification after an entity-resolution link changes (reconciliation item C6).

When a link is rejected, reopened or superseded, or a merge is undone, claims that relied on it
may no longer be supported. Nothing is edited: this finds the dependent claims and starts a NEW
run (`reverify` policy) that re-verifies exactly those claims, rescans the case for contradictions
and drafts a new report version. The original claims, verification results and report versions
stay on record.

A claim depends on a link when it names it in `depends_on_er_links` (the link was not confirmed
when the claim was made) or when it cites ER_LINK_EVIDENCE whose content names the link
(`link_id`) or a merge (`merge_ids`).
"""

from collections.abc import Collection, Iterable
from datetime import datetime

from aegis.domain.agents import Budget
from aegis.evidence.er_deps import er_dependencies_of_evidence
from aegis.evidence.store import EvidenceStore
from aegis.runtime.runner import InvestigationRunner


def link_dependencies(store: EvidenceStore, claim_id: str) -> set[str]:
    """Every ER link / merge id the claim relies on."""
    claim = store.get_claim(claim_id)
    return set(claim.draft.depends_on_er_links) | er_dependencies_of_evidence(
        store, (c.evidence_id for c in claim.draft.citations)
    )


def claims_depending_on(
    store: EvidenceStore, case_id: str, changed_ids: Collection[str]
) -> list[str]:
    """Claims of the case that rely on any of `changed_ids`, in claim-id order."""
    changed = set(changed_ids)
    return sorted(
        c.claim_id
        for c in store.list_claims(case_id)
        if link_dependencies(store, c.claim_id) & changed
    )


def start_reverification(
    runner: InvestigationRunner,
    store: EvidenceStore,
    case_ids: Iterable[str],
    changed_ids: Collection[str],
    *,
    as_of: datetime,
    budget: Budget,
) -> dict[str, str]:
    """Queue one `reverify` run per affected case; returns {case_id: run_id}. The runner must
    have been built with the `reverify` policy. Cases without dependent claims are left alone."""
    started: dict[str, str] = {}
    for case_id in sorted(set(case_ids)):
        affected = claims_depending_on(store, case_id, changed_ids)
        if affected:
            started[case_id] = runner.start(
                case_id=case_id, as_of=as_of, budget=budget, reverify_claim_ids=affected
            )
    return started
