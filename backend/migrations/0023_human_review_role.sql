-- Fixes the two HIGH findings of the Phase 3/4 security review (docs/RECONCILIATION.md):
--
-- H-1  The application login could write contradiction status events marked as human, dismiss a
--      HIGH finding and pass the closure gate (0022), or insert a contradiction with a chosen
--      fingerprint to pre-empt the real finding.
-- H-2  The application login could create CONFIRMED entity-resolution links with an asserted
--      human actor, undo merges, or fabricate a mention and an entity with equal identifiers to
--      get an auto-confirmed EXACT_ID link.
--
-- Mechanism (the same pattern as the approver role of 0007): a separate login, aegis_reviewer,
-- is the only role that can record HUMAN decisions about contradictions and entity links. The
-- database checks role membership with session_user, so a caller cannot claim to be human.
-- What stays caller-asserted until the API phase: the human id text (decided_by / actor) that the
-- reviewer login records; one shared reviewer login means one compromised credential can act
-- under any name.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis_reviewer') THEN
    CREATE ROLE aegis_reviewer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END $$;

-- ---------------------------------------------------------------- contradiction status (H-1)
ALTER TABLE evidence.contradiction_status_event
  ADD COLUMN actor_login text NOT NULL DEFAULT session_user;

-- The automatic OPEN event is written by the owner-rights trigger, since the app login can no
-- longer insert status events itself.
CREATE OR REPLACE FUNCTION evidence.open_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  INSERT INTO evidence.contradiction_status_event (contradiction_id, status, actor_kind, actor)
  VALUES (NEW.contradiction_id, 'OPEN', 'SYSTEM', session_user);
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION evidence.status_transition() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE cur text;
BEGIN
  NEW.actor_login := session_user;
  SELECT status INTO cur FROM evidence.contradiction_status_event
    WHERE contradiction_id = NEW.contradiction_id ORDER BY event_id DESC LIMIT 1;
  IF cur IS NULL THEN
    IF NEW.status <> 'OPEN' THEN
      RAISE EXCEPTION 'the first status of a contradiction must be OPEN';
    END IF;
  ELSE
    IF NOT pg_has_role(session_user, 'aegis_reviewer', 'member') THEN
      RAISE EXCEPTION 'only the human-review role may change a contradiction status';
    END IF;
    NEW.actor_kind := 'HUMAN';
    IF NOT ((cur, NEW.status) IN (('OPEN', 'EXPLAINED'), ('OPEN', 'RESOLVED_BY_HUMAN'),
            ('OPEN', 'DISMISSED'), ('EXPLAINED', 'RESOLVED_BY_HUMAN'),
            ('EXPLAINED', 'DISMISSED'), ('EXPLAINED', 'OPEN'))) THEN
      RAISE EXCEPTION 'illegal contradiction status change % -> %', cur, NEW.status;
    END IF;
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION evidence.open_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION evidence.status_transition() FROM PUBLIC;

REVOKE INSERT ON evidence.contradiction_status_event FROM aegis_app;

-- Identity of a finding includes its severity, so a record that merely reuses a fingerprint with a
-- lower severity can no longer make the real HIGH finding silently disappear.
DROP INDEX evidence.contradiction_fingerprint_once;
CREATE UNIQUE INDEX contradiction_fingerprint_once
  ON evidence.contradiction (case_id, (detector->>'fingerprint'), severity)
  WHERE detector ? 'fingerprint';

-- A stored fingerprint must be the one its own type, detector and members produce
-- (analytics.contradictions.fingerprint: SHA-256 of the canonical JSON
-- {"detector":[name,version],"m":[[member_type,member_id],...sorted],"type":type}).
-- Checked at COMMIT, after the members have been inserted.
CREATE FUNCTION evidence.check_fingerprint() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE members text; expected text;
BEGIN
  IF NEW.detector ? 'fingerprint' THEN
    SELECT string_agg('[' || to_json(member_type)::text || ',' || to_json(member_id)::text || ']',
                      ',' ORDER BY member_type COLLATE "C", member_id COLLATE "C")
      INTO members FROM evidence.contradiction_member
      WHERE contradiction_id = NEW.contradiction_id;
    expected := encode(sha256(convert_to(
      '{"detector":[' || to_json(NEW.detector->>'name')::text || ','
      || to_json(NEW.detector->>'version')::text || '],"m":[' || coalesce(members, '')
      || '],"type":' || to_json(NEW.type)::text || '}', 'UTF8')), 'hex');
    IF NEW.detector->>'fingerprint' IS DISTINCT FROM expected THEN
      RAISE EXCEPTION 'the stored fingerprint does not match the finding it is attached to';
    END IF;
  END IF;
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION evidence.check_fingerprint() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER check_fingerprint AFTER INSERT ON evidence.contradiction
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION evidence.check_fingerprint();

-- ----------------------------------------------------- entity-resolution identifiers (H-2)
-- An identifier may only be stated by a mention if it can be checked against source data. Today the
-- only verifiable pair is source.account / account_number (the stored account reference). Anything
-- else (for example the IBM entity id, whose source file is not ingested) cannot start an EXACT_ID
-- link until a source table and a rule for it are added here.
CREATE FUNCTION derived.identifier_verified(
  p_table text, p_id text, p_type text, p_value text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT CASE
    WHEN p_table = 'source.account' AND p_type = 'account_number' THEN EXISTS (
      SELECT 1 FROM source.account WHERE account_id = p_id AND source_account_ref = p_value)
    ELSE false END
$$;
REVOKE ALL ON FUNCTION derived.identifier_verified(text, text, text, text) FROM PUBLIC;

CREATE FUNCTION derived.mention_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE k text; v text;
BEGIN
  FOR k, v IN SELECT key, value FROM jsonb_each_text(NEW.identifiers) LOOP
    IF NOT derived.identifier_verified(NEW.source_table, NEW.source_id, k, v) THEN
      RAISE EXCEPTION 'identifier % of this mention cannot be verified against source data', k;
    END IF;
  END LOOP;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION derived.mention_guard() FROM PUBLIC;
CREATE TRIGGER mention_guard BEFORE INSERT ON derived.entity_mention
  FOR EACH ROW EXECUTE FUNCTION derived.mention_guard();

-- An entity may only carry identifiers that its founding mention carries (and that were therefore
-- verified): an entity cannot be minted with an identifier of the attacker's choosing.
CREATE FUNCTION derived.entity_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE k text; v text; m derived.entity_mention%ROWTYPE;
BEGIN
  IF NEW.identifiers <> '{}'::jsonb THEN
    SELECT * INTO m FROM derived.entity_mention WHERE mention_id = NEW.founding_mention_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'an entity with identifiers needs a founding mention that carries them';
    END IF;
    FOR k, v IN SELECT key, value FROM jsonb_each_text(NEW.identifiers) LOOP
      IF m.identifiers->>k IS DISTINCT FROM v THEN
        RAISE EXCEPTION 'entity identifier % is not carried by its founding mention', k;
      END IF;
    END LOOP;
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION derived.entity_guard() FROM PUBLIC;
CREATE TRIGGER entity_guard BEFORE INSERT ON derived.resolved_entity
  FOR EACH ROW EXECUTE FUNCTION derived.entity_guard();

-- ------------------------------------------------------ human link and merge decisions (H-2)
CREATE OR REPLACE FUNCTION derived.er_link_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE p derived.er_link%ROWTYPE; m derived.entity_mention%ROWTYPE;
        e derived.resolved_entity%ROWTYPE; id_type text;
BEGIN
  NEW.decided_login := session_user;
  NEW.decided_at := now();
  IF NEW.actor_kind = 'HUMAN' AND NOT pg_has_role(session_user, 'aegis_reviewer', 'member') THEN
    RAISE EXCEPTION 'human link decisions need the human-review role';
  END IF;
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
    IF p.status = 'REJECTED' AND NEW.actor_kind <> 'HUMAN' THEN
      RAISE EXCEPTION 'only a human may change a link a human rejected';
    END IF;
    IF p.status = 'CONFIRMED' AND p.actor_kind = 'HUMAN' AND NEW.actor_kind <> 'HUMAN' THEN
      RAISE EXCEPTION 'a link a human confirmed can only be changed by a human (link %)', NEW.link_id;
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

CREATE OR REPLACE FUNCTION derived.entity_merge_guard() RETURNS trigger
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
    IF NOT pg_has_role(session_user, 'aegis_reviewer', 'member') THEN
      RAISE EXCEPTION 'undoing a merge needs the human-review role';
    END IF;
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

-- ------------------------------------------------------------------------------- case guard
-- Same body as 0022 with one change: the last_alert_at bounds are checked only when the column
-- actually changes. Re-checking an unchanged value against the transaction clock could reject an
-- unrelated update (a close, a transition) if the database clock steps backwards.
CREATE OR REPLACE FUNCTION workflow.case_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE review_at timestamptz; open_high integer;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'cases cannot be deleted';
  END IF;
  IF OLD.status = 'CLOSED' THEN
    RAISE EXCEPTION 'case % is CLOSED and frozen', OLD.case_id;
  END IF;
  IF NEW.case_id <> OLD.case_id OR NEW.subject_ref <> OLD.subject_ref
     OR NEW.opened_at <> OLD.opened_at OR NEW.case_number <> OLD.case_number THEN
    RAISE EXCEPTION 'case identity fields are immutable';
  END IF;
  IF NOT (NEW.priority_score > '-Infinity'::float8 AND NEW.priority_score < 'Infinity'::float8) THEN
    RAISE EXCEPTION 'priority_score must be finite';
  END IF;
  IF NEW.priority_score < OLD.priority_score THEN
    RAISE EXCEPTION 'priority_score may not decrease';
  END IF;
  IF NEW.last_alert_at IS DISTINCT FROM OLD.last_alert_at
     AND (NEW.last_alert_at < OLD.last_alert_at OR NEW.last_alert_at > now()) THEN
    RAISE EXCEPTION 'last_alert_at must be non-decreasing and not in the future';
  END IF;
  IF NEW.status <> OLD.status THEN
    IF NOT ((OLD.status, NEW.status) IN (('OPEN', 'INVESTIGATING'),
            ('INVESTIGATING', 'PENDING_REVIEW'), ('PENDING_REVIEW', 'INVESTIGATING'),
            ('PENDING_REVIEW', 'CLOSED'))) THEN
      RAISE EXCEPTION 'illegal case transition % -> %', OLD.status, NEW.status;
    END IF;
    IF NEW.status = 'CLOSED' THEN
      SELECT count(*) INTO open_high FROM evidence.contradiction_current
        WHERE case_id = NEW.case_id AND severity = 'HIGH' AND status = 'OPEN';
      IF open_high > 0 THEN
        RAISE EXCEPTION
          'closing case % is blocked by % open HIGH contradiction(s); a human must explain, resolve or dismiss each',
          NEW.case_id, open_high;
      END IF;
      SELECT max(at) INTO review_at FROM workflow.case_event
        WHERE case_id = NEW.case_id AND to_status = 'PENDING_REVIEW';
      IF review_at IS NULL OR NOT EXISTS (
        SELECT 1 FROM workflow.approval a JOIN workflow.approval_request r USING (request_id)
        WHERE a.approval_id = NEW.closure_approval_id AND a.decision = 'APPROVED'
          AND r.action_type = 'CASE_CLOSURE' AND r.subject_ref = NEW.case_id
          AND r.requested_at >= review_at) THEN
        RAISE EXCEPTION
          'closing case % needs an APPROVED CASE_CLOSURE decision requested after it entered review',
          NEW.case_id;
      END IF;
    END IF;
    INSERT INTO workflow.case_event (case_id, from_status, to_status, actor, reason)
    VALUES (NEW.case_id, OLD.status, NEW.status, session_user,
            left(nullif(current_setting('aegis.transition_reason', true), ''), 500));
    PERFORM audit.record('case.' || lower(NEW.status), 'investigation_case', NEW.case_id,
                         jsonb_build_object('from', OLD.status));
  END IF;
  RETURN NEW;
END $$;

-- ---------------------------------------------------------------------------------- grants
GRANT USAGE ON SCHEMA evidence, derived, workflow TO aegis_reviewer;
GRANT SELECT ON ALL TABLES IN SCHEMA evidence, derived TO aegis_reviewer;
GRANT SELECT ON workflow.investigation_case TO aegis_reviewer;
GRANT INSERT ON evidence.contradiction_status_event TO aegis_reviewer;
GRANT INSERT ON derived.er_link, derived.entity_merge TO aegis_reviewer;
GRANT USAGE ON SEQUENCE evidence.contradiction_status_event_event_id_seq,
  derived.er_link_row_id_seq, derived.entity_merge_event_id_seq TO aegis_reviewer;
