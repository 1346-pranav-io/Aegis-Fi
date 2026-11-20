-- Phase 4: entity resolution (DATA_SCHEMA derived.*, FR-ER-01..04, EVIDENCE_MODEL 7, D-09d).
-- PROPOSED by the Phase 4 stream; NOT approved. Needs an owner entry in APPROVALS.json.
--
-- Model: a mention (what a source record says) is separate from a resolved entity. A link
-- mention -> entity is a *history*: every status change appends a row, nothing is overwritten.
-- Rules enforced here, not only in Python:
--   * a link starts PROPOSED; it may start CONFIRMED only by exact verified identifier equality
--     (method EXACT_ID, identifier type on the allow-list, values equal) or as a human MANUAL link
--   * confirming a non-exact link, rejecting, and re-opening need a HUMAN actor (asserted by the
--     caller until the API phase supplies identity; the DB login is recorded by trigger)
--   * mention, entity, method, score and features of a link never change; only its status
--   * merging one entity into another needs an APPROVED ENTITY_MERGE decision for exactly
--     'from->into' (D-09d); unmerge appends a row and restores the previous state; merge chains
--     cannot form cycles (the target must be a root)
--   * every link status change and merge/unmerge is written to the audit log
--   * match_score is a similarity in [0,1], never a probability (D-06); there is no probability column
CREATE SCHEMA IF NOT EXISTS derived;

-- Identifier types that count as "verified" for automatic confirmation. Changing this list is a
-- migration (and therefore an owner approval), not a runtime action.
CREATE TABLE derived.er_exact_identifier_type (identifier_type text PRIMARY KEY);
INSERT INTO derived.er_exact_identifier_type VALUES ('account_number'), ('entity_id');

CREATE TABLE derived.entity_mention (
  mention_id text PRIMARY KEY,
  source_table text NOT NULL,
  source_id text NOT NULL,
  mention_type text NOT NULL CHECK (mention_type IN ('ORGANISATION', 'PERSON', 'UNKNOWN')),
  raw_name text NOT NULL,
  normalised_name text NOT NULL,
  identifiers jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(identifiers) = 'object'),
  attributes jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(attributes) = 'object'),
  extractor_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT mention_once_per_source UNIQUE (source_table, source_id, extractor_version));

CREATE TABLE derived.resolved_entity (
  entity_id text PRIMARY KEY,
  entity_type text NOT NULL CHECK (entity_type IN ('ORGANISATION', 'PERSON', 'UNKNOWN')),
  canonical_name text NOT NULL,
  normalised_name text NOT NULL,
  identifiers jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(identifiers) = 'object'),
  founding_mention_id text REFERENCES derived.entity_mention,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE derived.er_link (
  row_id bigserial PRIMARY KEY,
  link_id text NOT NULL,
  mention_id text NOT NULL REFERENCES derived.entity_mention,
  entity_id text NOT NULL REFERENCES derived.resolved_entity,
  method text NOT NULL CHECK (method IN ('EXACT_ID', 'FUZZY_NAME', 'ATTRIBUTE_MATCH', 'MANUAL')),
  match_score double precision NOT NULL CHECK (match_score >= 0 AND match_score <= 1),
  evidence_features jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(evidence_features) = 'object'),
  status text NOT NULL CHECK (status IN ('PROPOSED', 'CONFIRMED', 'REJECTED', 'SUPERSEDED')),
  actor_kind text NOT NULL CHECK (actor_kind IN ('SYSTEM', 'HUMAN')),
  decided_by text,                     -- human id (caller-asserted until the API phase)
  decided_login text NOT NULL DEFAULT session_user,
  decided_at timestamptz NOT NULL DEFAULT now(),
  scorer_version text NOT NULL CHECK (length(scorer_version) > 0),
  reason text CHECK (reason IS NULL OR length(reason) <= 500));
CREATE INDEX ON derived.er_link (link_id, row_id DESC);
CREATE INDEX ON derived.er_link (mention_id);
CREATE INDEX ON derived.er_link (entity_id);

CREATE TABLE derived.entity_merge (
  event_id bigserial PRIMARY KEY,
  merge_id text NOT NULL,
  from_entity_id text NOT NULL REFERENCES derived.resolved_entity,
  into_entity_id text NOT NULL REFERENCES derived.resolved_entity,
  action text NOT NULL CHECK (action IN ('MERGE', 'UNMERGE')),
  approval_id text REFERENCES workflow.approval,
  actor text NOT NULL CHECK (length(btrim(actor)) > 0),
  decided_login text NOT NULL DEFAULT session_user,
  reason text NOT NULL CHECK (length(btrim(reason)) > 0 AND length(reason) <= 500),
  at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT merge_distinct CHECK (from_entity_id <> into_entity_id),
  CONSTRAINT merge_needs_approval CHECK (action <> 'MERGE' OR approval_id IS NOT NULL));
CREATE INDEX ON derived.entity_merge (merge_id, event_id DESC);
CREATE UNIQUE INDEX one_merge_per_approval ON derived.entity_merge (approval_id) WHERE action = 'MERGE';

CREATE VIEW derived.er_link_current AS
  SELECT DISTINCT ON (link_id) link_id, mention_id, entity_id, method, match_score,
         evidence_features, status, actor_kind, decided_by, decided_at, scorer_version, row_id
  FROM derived.er_link ORDER BY link_id, row_id DESC;
CREATE VIEW derived.entity_merge_current AS
  SELECT DISTINCT ON (merge_id) merge_id, from_entity_id, into_entity_id, action, approval_id,
         actor, at, event_id
  FROM derived.entity_merge ORDER BY merge_id, event_id DESC;

-- Insert-only everywhere (history rows, never updates).
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['derived.er_exact_identifier_type', 'derived.entity_mention',
      'derived.resolved_entity', 'derived.er_link', 'derived.entity_merge'] LOOP
    EXECUTE format('CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON %s
                    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation()', t);
    EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %s
                    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation()', t);
  END LOOP; END $$;

CREATE FUNCTION derived.er_link_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE p derived.er_link%ROWTYPE; m derived.entity_mention%ROWTYPE;
        e derived.resolved_entity%ROWTYPE; id_type text;
BEGIN
  NEW.decided_login := session_user;
  NEW.decided_at := now();
  IF NEW.actor_kind = 'HUMAN' AND (NEW.decided_by IS NULL OR length(btrim(NEW.decided_by)) = 0) THEN
    RAISE EXCEPTION 'a human decision needs decided_by';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtext('er_link:' || NEW.link_id));
  SELECT * INTO p FROM derived.er_link WHERE link_id = NEW.link_id ORDER BY row_id DESC LIMIT 1;
  IF NOT FOUND THEN
    IF NEW.method = 'EXACT_ID' THEN   -- the method is a claim; the database checks it
      SELECT * INTO m FROM derived.entity_mention WHERE mention_id = NEW.mention_id;
      SELECT * INTO e FROM derived.resolved_entity WHERE entity_id = NEW.entity_id;
      id_type := NEW.evidence_features->>'identifier_type';
      IF id_type IS NULL
         OR NOT EXISTS (SELECT 1 FROM derived.er_exact_identifier_type WHERE identifier_type = id_type)
         OR m.identifiers->>id_type IS NULL
         OR m.identifiers->>id_type IS DISTINCT FROM e.identifiers->>id_type THEN
        RAISE EXCEPTION 'EXACT_ID link needs equal verified identifiers (allow-listed type)';
      END IF;
      IF NEW.match_score <> 1 THEN
        RAISE EXCEPTION 'EXACT_ID links have match_score 1';
      END IF;
    END IF;
    IF NEW.status = 'CONFIRMED' AND NEW.method <> 'EXACT_ID'
       AND NOT (NEW.method = 'MANUAL' AND NEW.actor_kind = 'HUMAN') THEN
      RAISE EXCEPTION 'only an EXACT_ID link may start CONFIRMED automatically (D-09d)';
    END IF;
    IF NEW.status NOT IN ('PROPOSED', 'CONFIRMED') THEN
      RAISE EXCEPTION 'a link starts PROPOSED or CONFIRMED';
    END IF;
  ELSE
    IF NEW.mention_id <> p.mention_id OR NEW.entity_id <> p.entity_id OR NEW.method <> p.method
       OR NEW.match_score <> p.match_score OR NEW.scorer_version <> p.scorer_version
       OR NEW.evidence_features IS DISTINCT FROM p.evidence_features THEN
      RAISE EXCEPTION 'only the status of link % may change; create a new link to re-score', NEW.link_id;
    END IF;
    IF NOT ((p.status, NEW.status) IN (('PROPOSED', 'CONFIRMED'), ('PROPOSED', 'REJECTED'),
            ('PROPOSED', 'SUPERSEDED'), ('CONFIRMED', 'REJECTED'), ('CONFIRMED', 'SUPERSEDED'),
            ('REJECTED', 'PROPOSED'), ('REJECTED', 'CONFIRMED'))) THEN
      RAISE EXCEPTION 'illegal link status change % -> %', p.status, NEW.status;
    END IF;
    IF NEW.status IN ('REJECTED', 'PROPOSED') AND NEW.actor_kind <> 'HUMAN' THEN
      RAISE EXCEPTION 'only a human may reject or re-open a link';
    END IF;
    IF NEW.status = 'CONFIRMED' AND NEW.method <> 'EXACT_ID' AND NEW.actor_kind <> 'HUMAN' THEN
      RAISE EXCEPTION 'only a human may confirm a non-exact link (D-09d)';
    END IF;
  END IF;
  PERFORM audit.record('er.link.' || lower(NEW.status), 'er_link', NEW.link_id,
                       jsonb_build_object('mention', NEW.mention_id, 'entity', NEW.entity_id,
                                          'method', NEW.method, 'score', NEW.match_score,
                                          'actor_kind', NEW.actor_kind, 'decided_by', NEW.decided_by));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION derived.er_link_guard() FROM PUBLIC;
CREATE TRIGGER er_link_guard BEFORE INSERT ON derived.er_link
  FOR EACH ROW EXECUTE FUNCTION derived.er_link_guard();

CREATE FUNCTION derived.entity_merge_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE p derived.entity_merge%ROWTYPE;
BEGIN
  NEW.decided_login := session_user;
  NEW.at := now();
  PERFORM pg_advisory_xact_lock(hashtext('derived.entity_merge'));   -- serialise: no merge cycles
  SELECT * INTO p FROM derived.entity_merge WHERE merge_id = NEW.merge_id ORDER BY event_id DESC LIMIT 1;
  IF NEW.action = 'MERGE' THEN
    IF FOUND THEN
      RAISE EXCEPTION 'merge id % was already used; use a new merge id', NEW.merge_id;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM workflow.approval a JOIN workflow.approval_request r USING (request_id)
      WHERE a.approval_id = NEW.approval_id AND a.decision = 'APPROVED'
        AND r.action_type = 'ENTITY_MERGE'
        AND r.subject_ref = NEW.from_entity_id || '->' || NEW.into_entity_id) THEN
      RAISE EXCEPTION 'merging % into % needs an APPROVED ENTITY_MERGE decision for exactly that merge',
        NEW.from_entity_id, NEW.into_entity_id;
    END IF;
    IF EXISTS (SELECT 1 FROM derived.entity_merge_current c
               WHERE c.action = 'MERGE' AND c.from_entity_id = NEW.from_entity_id) THEN
      RAISE EXCEPTION 'entity % is already merged into another entity', NEW.from_entity_id;
    END IF;
    IF EXISTS (SELECT 1 FROM derived.entity_merge_current c
               WHERE c.action = 'MERGE' AND c.from_entity_id = NEW.into_entity_id) THEN
      RAISE EXCEPTION 'target % is itself merged; merge into its root entity', NEW.into_entity_id;
    END IF;
  ELSE
    IF NOT FOUND OR p.action <> 'MERGE' THEN
      RAISE EXCEPTION 'merge % is not active; nothing to unmerge', NEW.merge_id;
    END IF;
    IF NEW.from_entity_id <> p.from_entity_id OR NEW.into_entity_id <> p.into_entity_id THEN
      RAISE EXCEPTION 'unmerge must name the same entities as the merge';
    END IF;
  END IF;
  PERFORM audit.record('er.' || lower(NEW.action), 'entity_merge', NEW.merge_id,
                       jsonb_build_object('from', NEW.from_entity_id, 'into', NEW.into_entity_id,
                                          'approval', NEW.approval_id, 'actor', NEW.actor));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION derived.entity_merge_guard() FROM PUBLIC;
CREATE TRIGGER entity_merge_guard BEFORE INSERT ON derived.entity_merge
  FOR EACH ROW EXECUTE FUNCTION derived.entity_merge_guard();

GRANT USAGE ON SCHEMA derived TO aegis_app;
GRANT SELECT ON derived.er_exact_identifier_type TO aegis_app;
GRANT SELECT, INSERT ON derived.entity_mention, derived.resolved_entity, derived.er_link,
  derived.entity_merge TO aegis_app;
GRANT SELECT ON derived.er_link_current, derived.entity_merge_current TO aegis_app;
GRANT USAGE ON SEQUENCE derived.er_link_row_id_seq, derived.entity_merge_event_id_seq TO aegis_app;
