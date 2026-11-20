"""Database side of entity resolution (runs as the aegis_app login; migration 0020).

All state is append-only history. The database enforces the rules (exact-only auto-confirm,
human-only decisions, ENTITY_MERGE approval, no merge cycles, audit rows); this module is the
typed way to call it and maps database refusals to typed errors. The `decided_by` / `actor` ids
are caller-asserted human ids until the API phase supplies verified identity.
"""

import json
from dataclasses import dataclass
from typing import Any

import psycopg

from aegis.common.errors import ConflictError, NotFound
from aegis.common.ids import new_id
from aegis.domain.entities import ERLinkView, LinkMethod, LinkStatus
from aegis.entities.pipeline import (
    EntityIndex,
    EntityRecord,
    LinkProposal,
    MentionRecord,
    propose_links,
)
from aegis.entities.similarity import NORMALISER_VERSION, normalise_name


def new_merge_id() -> str:
    return new_id("erm")


def _wrap(exc: psycopg.errors.RaiseException) -> ConflictError:
    return ConflictError(str(exc).splitlines()[0])


# ----------------------------------------------------------------------------- mentions, entities
def add_mention(
    conn: psycopg.Connection,
    *,
    source_table: str,
    source_id: str,
    raw_name: str,
    mention_type: str = "ORGANISATION",
    identifiers: dict[str, str] | None = None,
    attributes: dict[str, Any] | None = None,
    extractor_version: str = NORMALISER_VERSION,
) -> str:
    """Idempotent per (source_table, source_id, extractor_version): returns the existing id."""
    mention_id = new_id("men")
    row = conn.execute(
        "INSERT INTO derived.entity_mention (mention_id, source_table, source_id, mention_type, "
        "raw_name, normalised_name, identifiers, attributes, extractor_version) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT ON CONSTRAINT mention_once_per_source DO NOTHING RETURNING mention_id",
        (
            mention_id, source_table, source_id, mention_type, raw_name, normalise_name(raw_name),
            json.dumps(identifiers or {}), json.dumps(attributes or {}), extractor_version,
        ),
    ).fetchone()  # fmt: skip
    if row:
        return str(row[0])
    existing = conn.execute(
        "SELECT mention_id FROM derived.entity_mention WHERE source_table=%s AND source_id=%s "
        "AND extractor_version=%s",
        (source_table, source_id, extractor_version),
    ).fetchone()
    if existing is None:  # pragma: no cover - the conflict above proves it exists
        raise NotFound(f"mention {source_table}/{source_id}")
    return str(existing[0])


def create_entity(
    conn: psycopg.Connection,
    *,
    canonical_name: str,
    created_by: str,
    entity_type: str = "ORGANISATION",
    identifiers: dict[str, str] | None = None,
    founding_mention_id: str | None = None,
) -> str:
    entity_id = new_id("ent")
    conn.execute(
        "INSERT INTO derived.resolved_entity (entity_id, entity_type, canonical_name, "
        "normalised_name, identifiers, founding_mention_id, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (
            entity_id, entity_type, canonical_name, normalise_name(canonical_name),
            json.dumps(identifiers or {}), founding_mention_id, created_by,
        ),
    )  # fmt: skip
    return entity_id


def load_entity_index(conn: psycopg.Connection) -> EntityIndex:
    rows = conn.execute(
        "SELECT entity_id, normalised_name, identifiers FROM derived.resolved_entity "
        "ORDER BY entity_id"
    ).fetchall()
    return EntityIndex([EntityRecord(str(r[0]), str(r[1]), dict(r[2])) for r in rows])


def load_mention(conn: psycopg.Connection, mention_id: str) -> MentionRecord:
    r = conn.execute(
        "SELECT mention_id, normalised_name, identifiers FROM derived.entity_mention "
        "WHERE mention_id=%s",
        (mention_id,),
    ).fetchone()
    if r is None:
        raise NotFound(f"mention {mention_id}")
    return MentionRecord(str(r[0]), str(r[1]), dict(r[2]))


# --------------------------------------------------------------------------------------- links
def record_proposal(conn: psycopg.Connection, p: LinkProposal) -> str:
    """Store one proposal as the first row of a new link. Returns the link id. The database
    refuses a CONFIRMED start unless the identifiers really are equal and allow-listed."""
    link_id = new_id("erl")
    try:
        _insert_link_row(
            conn, link_id, p.mention_id, p.entity_id, p.method, p.match_score, p.features,
            p.status, "SYSTEM", None, p.scorer_version, None,
        )  # fmt: skip
    except psycopg.errors.RaiseException as exc:
        raise _wrap(exc) from exc
    return link_id


def resolve_mention(
    conn: psycopg.Connection,
    mention_id: str,
    *,
    propose_threshold: float | None = None,
    top_k: int | None = None,
) -> list[str]:
    """Run the pure pipeline against the stored entities and store each proposal."""
    kwargs: dict[str, Any] = {}
    if propose_threshold is not None:
        kwargs["propose_threshold"] = propose_threshold
    if top_k is not None:
        kwargs["top_k"] = top_k
    mention = load_mention(conn, mention_id)
    proposals = propose_links(mention, load_entity_index(conn), **kwargs)
    return [record_proposal(conn, p) for p in proposals]


def record_manual_link(
    conn: psycopg.Connection, mention_id: str, entity_id: str, decided_by: str, reason: str
) -> str:
    """A human asserts a link: allowed to start CONFIRMED (method MANUAL, score 1)."""
    link_id = new_id("erl")
    try:
        _insert_link_row(
            conn, link_id, mention_id, entity_id, LinkMethod.MANUAL, 1.0, {}, LinkStatus.CONFIRMED,
            "HUMAN", decided_by, "manual", reason,
        )  # fmt: skip
    except psycopg.errors.RaiseException as exc:
        raise _wrap(exc) from exc
    return link_id


def _insert_link_row(
    conn: psycopg.Connection, link_id: str, mention_id: str, entity_id: str, method: LinkMethod,
    score: float, features: dict[str, object], status: LinkStatus, actor_kind: str,
    decided_by: str | None, scorer_version: str, reason: str | None,
) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO derived.er_link (link_id, mention_id, entity_id, method, match_score, "
        "evidence_features, status, actor_kind, decided_by, scorer_version, reason) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            link_id, mention_id, entity_id, method.value, score, json.dumps(features), status.value,
            actor_kind, decided_by, scorer_version, reason,
        ),
    )  # fmt: skip


def _change_status(
    conn: psycopg.Connection, link_id: str, to: LinkStatus, actor_kind: str,
    decided_by: str | None, reason: str,
) -> None:  # fmt: skip
    cur = conn.execute(
        "SELECT mention_id, entity_id, method, match_score, evidence_features, scorer_version "
        "FROM derived.er_link_current WHERE link_id=%s",
        (link_id,),
    ).fetchone()
    if cur is None:
        raise NotFound(f"link {link_id}")
    try:
        _insert_link_row(
            conn, link_id, str(cur[0]), str(cur[1]), LinkMethod(cur[2]), float(cur[3]),
            dict(cur[4]), to, actor_kind, decided_by, str(cur[5]), reason,
        )  # fmt: skip
    except psycopg.errors.RaiseException as exc:
        raise _wrap(exc) from exc


def confirm_link(conn: psycopg.Connection, link_id: str, decided_by: str, reason: str) -> None:
    _change_status(conn, link_id, LinkStatus.CONFIRMED, "HUMAN", decided_by, reason)


def reject_link(conn: psycopg.Connection, link_id: str, decided_by: str, reason: str) -> None:
    _change_status(conn, link_id, LinkStatus.REJECTED, "HUMAN", decided_by, reason)


def reopen_link(conn: psycopg.Connection, link_id: str, decided_by: str, reason: str) -> None:
    _change_status(conn, link_id, LinkStatus.PROPOSED, "HUMAN", decided_by, reason)


def supersede_link(conn: psycopg.Connection, link_id: str, reason: str) -> None:
    _change_status(conn, link_id, LinkStatus.SUPERSEDED, "SYSTEM", None, reason)


def link_history(conn: psycopg.Connection, link_id: str) -> list[tuple[str, str, str | None]]:
    """(status, actor_kind, decided_by) per row, oldest first."""
    rows = conn.execute(
        "SELECT status, actor_kind, decided_by FROM derived.er_link WHERE link_id=%s "
        "ORDER BY row_id",
        (link_id,),
    ).fetchall()
    return [(str(r[0]), str(r[1]), None if r[2] is None else str(r[2])) for r in rows]


# ------------------------------------------------------------------------------ merge / unmerge
def merge_subject_ref(from_entity: str, into_entity: str) -> str:
    return f"{from_entity}->{into_entity}"


def request_merge(
    conn: psycopg.Connection, from_entity: str, into_entity: str, requested_by: str,
    justification: str,
) -> str:  # fmt: skip
    """Create the ENTITY_MERGE approval request (D-09d). The merge itself waits for an approver."""
    request_id = new_id("apr")
    conn.execute(
        "INSERT INTO workflow.approval_request (request_id, action_type, subject_ref, "
        "requested_by, justification) VALUES (%s,'ENTITY_MERGE',%s,%s,%s)",
        (request_id, merge_subject_ref(from_entity, into_entity), requested_by, justification),
    )
    return request_id


def merge_entities(
    conn: psycopg.Connection, from_entity: str, into_entity: str, approval_id: str, actor: str,
    reason: str,
) -> str:  # fmt: skip
    """Merge `from_entity` into `into_entity`. Refused unless `approval_id` is an APPROVED
    ENTITY_MERGE decision for exactly this pair. Returns the merge id (a dependency id: see
    PgERLinkReader.unconfirmed_dependencies). Nothing is overwritten."""
    merge_id = new_merge_id()
    try:
        conn.execute(
            "INSERT INTO derived.entity_merge (merge_id, from_entity_id, into_entity_id, action, "
            "approval_id, actor, reason) VALUES (%s,%s,%s,'MERGE',%s,%s,%s)",
            (merge_id, from_entity, into_entity, approval_id, actor, reason),
        )
    except psycopg.errors.RaiseException as exc:
        raise _wrap(exc) from exc
    return merge_id


def unmerge(conn: psycopg.Connection, merge_id: str, actor: str, reason: str) -> None:
    """Reverse an active merge by appending an UNMERGE row; the entities return to their prior
    state and anything that depended on the merge is no longer confirmed."""
    cur = conn.execute(
        "SELECT from_entity_id, into_entity_id FROM derived.entity_merge_current WHERE merge_id=%s",
        (merge_id,),
    ).fetchone()
    if cur is None:
        raise NotFound(f"merge {merge_id}")
    try:
        conn.execute(
            "INSERT INTO derived.entity_merge (merge_id, from_entity_id, into_entity_id, action, "
            "actor, reason) VALUES (%s,%s,%s,'UNMERGE',%s,%s)",
            (merge_id, str(cur[0]), str(cur[1]), actor, reason),
        )
    except psycopg.errors.RaiseException as exc:
        raise _wrap(exc) from exc


def root_entity(conn: psycopg.Connection, entity_id: str) -> str:
    """The entity an entity currently resolves to (follows active merges; chains are acyclic)."""
    row = conn.execute(
        "WITH RECURSIVE chain(e, depth) AS ("
        "  SELECT %s::text, 0 "
        "  UNION ALL "
        "  SELECT c.into_entity_id, chain.depth + 1 FROM chain "
        "  JOIN derived.entity_merge_current c ON c.from_entity_id = chain.e AND c.action='MERGE' "
        "  WHERE chain.depth < 100) "
        "SELECT e FROM chain ORDER BY depth DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    if row is None:  # pragma: no cover - the seed row always exists
        raise NotFound(f"entity {entity_id}")
    return str(row[0])


# ------------------------------------------------------------------------------------ reader
@dataclass(frozen=True)
class FlaggedClaim:
    claim_id: str
    case_id: str
    unconfirmed: tuple[str, ...]


class PgERLinkReader:
    """Implements `aegis.domain.entities.ERLinkReader` over the database.

    `unconfirmed_dependencies` accepts link ids (`erl_...`) AND merge ids (`erm_...`): a claim
    that relied on a merge depends on it exactly as it depends on a link. An id counts as
    confirmed only when its latest state is CONFIRMED (link) or MERGE (merge). Unknown ids are
    reported as unconfirmed - the reader fails closed.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def latest_links(self, link_ids: list[str]) -> list[ERLinkView]:
        if not link_ids:
            return []
        rows = self._conn.execute(
            "SELECT link_id, mention_id, entity_id, method, match_score, status, scorer_version "
            "FROM derived.er_link_current WHERE link_id = ANY(%s) ORDER BY link_id",
            (list(link_ids),),
        ).fetchall()
        return [
            ERLinkView(
                link_id=str(r[0]), mention_id=str(r[1]), entity_id=str(r[2]),
                method=LinkMethod(r[3]), match_score=float(r[4]), status=LinkStatus(r[5]),
                scorer_version=str(r[6]),
            )
            for r in rows
        ]  # fmt: skip

    def entity_roots(self, entity_ids: list[str]) -> dict[str, str]:
        """entity id -> root entity after live merges (identity when not merged)."""
        return {e: root_entity(self._conn, e) for e in entity_ids}

    def unconfirmed_dependencies(self, link_ids: list[str]) -> list[str]:
        wanted = list(dict.fromkeys(link_ids))
        if not wanted:
            return []
        confirmed = {
            str(r[0])
            for r in self._conn.execute(
                "SELECT link_id FROM derived.er_link_current WHERE link_id = ANY(%s) "
                "AND status='CONFIRMED' UNION ALL "
                "SELECT merge_id FROM derived.entity_merge_current WHERE merge_id = ANY(%s) "
                "AND action='MERGE'",
                (wanted, wanted),
            ).fetchall()
        }
        return [i for i in wanted if i not in confirmed]

    def flagged_claims(self, case_id: str | None = None) -> list[FlaggedClaim]:
        """Claims that rely on a link or merge that is NOT confirmed *now* (FR-ER-03, T-INV-17).

        A claim relies on an id if the id is in its `depends_on_er_links` (unconfirmed when the
        claim was made) OR it cites ER_LINK_EVIDENCE whose content names the id (`link_id`, and
        `merge_ids` for merges). The second route catches claims made on a confirmed link that
        was rejected later. Claims are never modified; Phase 3 queues re-verification (C6).
        """
        rows = self._conn.execute(
            "SELECT c.claim_id, c.case_id, "
            "  c.depends_on_er_links || coalesce(array_agg(DISTINCT x.dep) FILTER "
            "  (WHERE x.dep IS NOT NULL), '{}') "
            "FROM evidence.claim c "
            "LEFT JOIN evidence.claim_evidence_link l ON l.claim_id = c.claim_id "
            "LEFT JOIN evidence.evidence_item e ON e.evidence_id = l.evidence_id "
            "  AND e.evidence_type = 'ER_LINK_EVIDENCE' "
            "LEFT JOIN LATERAL ("
            "  SELECT e.content->>'link_id' AS dep WHERE e.content ? 'link_id' "
            "  UNION ALL "
            "  SELECT jsonb_array_elements_text(e.content->'merge_ids') "
            "  WHERE jsonb_typeof(e.content->'merge_ids') = 'array') x ON true "
            "WHERE (%s::text IS NULL OR c.case_id = %s) "
            "GROUP BY c.claim_id, c.case_id, c.depends_on_er_links ORDER BY c.claim_id",
            (case_id, case_id),
        ).fetchall()
        out: list[FlaggedClaim] = []
        for claim_id, cid, deps in rows:
            if not deps:
                continue
            bad = self.unconfirmed_dependencies(list(deps))
            if bad:
                out.append(FlaggedClaim(str(claim_id), str(cid), tuple(bad)))
        return out
