-- Shared foundation for Phase 3 (evidence layer) and Phase 4 (contradictions): the evidence schema
-- from docs/architecture/EVIDENCE_MODEL.md. All records are insert-only (nothing is overwritten;
-- corrections supersede). Rules enforced here, not just in Python:
--   * non-human evidence lists at least one input (provenance, FR-EVD-01/03)
--   * a claim cites at least one evidence item, of the same case (D-07, FR-EVD-04)
--   * VERIFIED is impossible when a check failed (FR-VER-03)
--   * a contradiction links at least two existing items of the same case (FR-CON-02)
--   * contradiction status moves only along legal transitions, and anything but OPEN needs a
--     human actor (FR-CON-03); the actor kind is caller-asserted until the API phase.
-- Entity-resolution tables are NOT here: Phase 4 adds them in its own migration.
CREATE SCHEMA IF NOT EXISTS evidence;

CREATE TABLE evidence.evidence_item (
  evidence_id text PRIMARY KEY,
  case_id text NOT NULL REFERENCES workflow.investigation_case,
  run_id text,
  step_id text,
  evidence_type text NOT NULL CHECK (evidence_type IN (
    'SIGNAL', 'TXN_AGGREGATE', 'TXN_VELOCITY', 'TXN_STRUCTURING_CHECK', 'TXN_RAPID_MOVEMENT',
    'TXN_COUNTERPARTY_SUMMARY', 'GRAPH_NEIGHBOURHOOD', 'GRAPH_CYCLES', 'GRAPH_FANIN_FANOUT',
    'GRAPH_FLOW_THROUGH', 'GRAPH_COMMUNITY', 'GRAPH_CENTRALITY', 'BEHAV_SELF_DEVIATION',
    'BEHAV_PEER_DEVIATION', 'BEHAV_MODEL_SCORE', 'REF_LIST_MATCH', 'DOC_EXTRACTION',
    'ER_LINK_EVIDENCE', 'HUMAN_NOTE')),
  content jsonb NOT NULL,
  summary_text text NOT NULL,
  producer jsonb NOT NULL,
  as_of timestamptz NOT NULL,
  window_start timestamptz,
  window_end timestamptz,
  content_hash text NOT NULL,
  completeness text NOT NULL CHECK (completeness IN ('FULL', 'TRUNCATED', 'INSUFFICIENT')),
  quality_notes text,
  scores jsonb,
  supersedes text REFERENCES evidence.evidence_item,
  schema_version integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT probability_needs_calibrator CHECK (
    scores IS NULL OR scores->>'calibrated_probability' IS NULL
    OR scores->>'calibration_artefact_id' IS NOT NULL),
  CONSTRAINT idempotent_content UNIQUE (case_id, content_hash));
CREATE INDEX ON evidence.evidence_item (case_id, evidence_type);

CREATE TABLE evidence.evidence_input (
  evidence_id text NOT NULL REFERENCES evidence.evidence_item,
  ref_type text NOT NULL CHECK (ref_type IN ('SOURCE', 'EVIDENCE', 'DOCUMENT', 'REFERENCE')),
  ref_id text NOT NULL,
  ref_hash text NOT NULL,
  PRIMARY KEY (evidence_id, ref_type, ref_id));

CREATE TABLE evidence.claim (
  claim_id text PRIMARY KEY,
  case_id text NOT NULL REFERENCES workflow.investigation_case,
  run_id text,
  author_kind text NOT NULL CHECK (author_kind IN ('AGENT', 'HUMAN')),
  author_name text NOT NULL,
  author_version text NOT NULL,
  structure jsonb NOT NULL,
  text text NOT NULL,
  claim_kind text NOT NULL CHECK (claim_kind IN (
    'FACT_OBSERVATION', 'AGGREGATE', 'INFERENCE', 'TYPOLOGY_HYPOTHESIS', 'RECOMMENDATION')),
  self_reported_confidence text NOT NULL
    CHECK (self_reported_confidence IN ('NONE', 'LOW', 'MEDIUM', 'HIGH')),
  depends_on_er_links text[] NOT NULL DEFAULT '{}',
  supersedes text REFERENCES evidence.claim,
  created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ON evidence.claim (case_id);

CREATE TABLE evidence.claim_evidence_link (
  claim_id text NOT NULL REFERENCES evidence.claim,
  evidence_id text NOT NULL REFERENCES evidence.evidence_item,
  support_role text NOT NULL CHECK (support_role IN (
    'SUPPORTS', 'PARTIALLY_SUPPORTS', 'CONTEXT', 'CONTRADICTS')),
  PRIMARY KEY (claim_id, evidence_id));

CREATE TABLE evidence.verification_result (
  verification_id text PRIMARY KEY,
  claim_id text NOT NULL REFERENCES evidence.claim,
  status text NOT NULL CHECK (status IN (
    'VERIFIED', 'PARTIALLY_SUPPORTED', 'UNSUPPORTED', 'CONTRADICTED', 'UNVERIFIABLE')),
  checks jsonb NOT NULL,
  rationale text NOT NULL,
  verifier jsonb NOT NULL,
  inputs_seen_hash text NOT NULL,   -- hash of exactly what the verifier was shown (D-08)
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT verified_means_no_failed_check
    CHECK (NOT (status = 'VERIFIED' AND checks @> '[{"outcome": "FAIL"}]'::jsonb)));
CREATE INDEX ON evidence.verification_result (claim_id, created_at);

CREATE TABLE evidence.contradiction (
  contradiction_id text PRIMARY KEY,
  case_id text NOT NULL REFERENCES workflow.investigation_case,
  type text NOT NULL CHECK (type IN (
    'ATTRIBUTE_CONFLICT', 'TIMELINE_IMPOSSIBLE', 'AMOUNT_MISMATCH', 'CLAIM_VS_EVIDENCE',
    'ER_CONFLICT', 'SOURCE_DISAGREEMENT')),
  severity text NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
  detector jsonb NOT NULL,
  explanation_proposal jsonb,       -- a proposal only; never changes status by itself
  created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ON evidence.contradiction (case_id);

CREATE TABLE evidence.contradiction_member (
  contradiction_id text NOT NULL REFERENCES evidence.contradiction,
  member_type text NOT NULL CHECK (member_type IN ('EVIDENCE', 'CLAIM')),
  member_id text NOT NULL,
  role text NOT NULL DEFAULT '',
  PRIMARY KEY (contradiction_id, member_type, member_id));

CREATE TABLE evidence.contradiction_status_event (
  event_id bigserial PRIMARY KEY,
  contradiction_id text NOT NULL REFERENCES evidence.contradiction,
  status text NOT NULL CHECK (status IN ('OPEN', 'EXPLAINED', 'RESOLVED_BY_HUMAN', 'DISMISSED')),
  actor_kind text NOT NULL CHECK (actor_kind IN ('SYSTEM', 'AGENT', 'HUMAN')),
  actor text NOT NULL,
  note text CHECK (note IS NULL OR length(note) <= 2000),
  at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT only_humans_move_past_open CHECK (status = 'OPEN' OR actor_kind = 'HUMAN'));

-- Current-state views (latest event / latest verification); nothing is ever updated.
CREATE VIEW evidence.contradiction_current AS
  SELECT DISTINCT ON (c.contradiction_id) c.contradiction_id, c.case_id, c.type, c.severity,
         e.status, e.actor_kind, e.actor, e.at
  FROM evidence.contradiction c
  JOIN evidence.contradiction_status_event e USING (contradiction_id)
  ORDER BY c.contradiction_id, e.event_id DESC;
CREATE VIEW evidence.claim_current_verification AS
  SELECT DISTINCT ON (claim_id) claim_id, verification_id, status, created_at
  FROM evidence.verification_result
  ORDER BY claim_id, created_at DESC, verification_id DESC;

-- Insert-only everywhere.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['evidence.evidence_item', 'evidence.evidence_input', 'evidence.claim',
      'evidence.claim_evidence_link', 'evidence.verification_result', 'evidence.contradiction',
      'evidence.contradiction_member', 'evidence.contradiction_status_event'] LOOP
    EXECUTE format('CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON %s
                    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation()', t);
    EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %s
                    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation()', t);
  END LOOP; END $$;

-- Cross-row rules. Deferred ones run at COMMIT so a transaction may insert parent and children
-- in any order.
CREATE FUNCTION evidence.require_inputs() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.producer->>'kind' <> 'HUMAN'
     AND NOT EXISTS (SELECT 1 FROM evidence.evidence_input WHERE evidence_id = NEW.evidence_id) THEN
    RAISE EXCEPTION 'non-human evidence % must list at least one input (provenance)', NEW.evidence_id;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER require_inputs AFTER INSERT ON evidence.evidence_item
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION evidence.require_inputs();

CREATE FUNCTION evidence.require_citation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM evidence.claim_evidence_link WHERE claim_id = NEW.claim_id) THEN
    RAISE EXCEPTION 'claim % must cite at least one evidence item', NEW.claim_id;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER require_citation AFTER INSERT ON evidence.claim
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION evidence.require_citation();

CREATE FUNCTION evidence.link_same_case() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT case_id FROM evidence.claim WHERE claim_id = NEW.claim_id)
     IS DISTINCT FROM (SELECT case_id FROM evidence.evidence_item WHERE evidence_id = NEW.evidence_id) THEN
    RAISE EXCEPTION 'claim % may only cite evidence of its own case', NEW.claim_id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER link_same_case BEFORE INSERT ON evidence.claim_evidence_link
  FOR EACH ROW EXECUTE FUNCTION evidence.link_same_case();

CREATE FUNCTION evidence.member_valid() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c_case text; m_case text;
BEGIN
  SELECT case_id INTO c_case FROM evidence.contradiction WHERE contradiction_id = NEW.contradiction_id;
  IF NEW.member_type = 'EVIDENCE' THEN
    SELECT case_id INTO m_case FROM evidence.evidence_item WHERE evidence_id = NEW.member_id;
  ELSE
    SELECT case_id INTO m_case FROM evidence.claim WHERE claim_id = NEW.member_id;
  END IF;
  IF m_case IS NULL OR m_case <> c_case THEN
    RAISE EXCEPTION 'contradiction member % % does not exist in case %',
      NEW.member_type, NEW.member_id, c_case;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER member_valid BEFORE INSERT ON evidence.contradiction_member
  FOR EACH ROW EXECUTE FUNCTION evidence.member_valid();

CREATE FUNCTION evidence.require_two_members() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT count(*) FROM evidence.contradiction_member
      WHERE contradiction_id = NEW.contradiction_id) < 2 THEN
    RAISE EXCEPTION 'contradiction % must link at least two items', NEW.contradiction_id;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER require_two_members AFTER INSERT ON evidence.contradiction
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION evidence.require_two_members();

CREATE FUNCTION evidence.open_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO evidence.contradiction_status_event (contradiction_id, status, actor_kind, actor)
  VALUES (NEW.contradiction_id, 'OPEN', 'SYSTEM', session_user);
  RETURN NULL;
END $$;
CREATE TRIGGER open_event AFTER INSERT ON evidence.contradiction
  FOR EACH ROW EXECUTE FUNCTION evidence.open_event();

CREATE FUNCTION evidence.status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur text;
BEGIN
  SELECT status INTO cur FROM evidence.contradiction_status_event
    WHERE contradiction_id = NEW.contradiction_id ORDER BY event_id DESC LIMIT 1;
  IF cur IS NULL THEN
    IF NEW.status <> 'OPEN' THEN
      RAISE EXCEPTION 'the first status of a contradiction must be OPEN';
    END IF;
  ELSIF NOT ((cur, NEW.status) IN (('OPEN', 'EXPLAINED'), ('OPEN', 'RESOLVED_BY_HUMAN'),
             ('OPEN', 'DISMISSED'), ('EXPLAINED', 'RESOLVED_BY_HUMAN'),
             ('EXPLAINED', 'DISMISSED'), ('EXPLAINED', 'OPEN'))) THEN
    RAISE EXCEPTION 'illegal contradiction status change % -> %', cur, NEW.status;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER status_transition BEFORE INSERT ON evidence.contradiction_status_event
  FOR EACH ROW EXECUTE FUNCTION evidence.status_transition();

GRANT USAGE ON SCHEMA evidence TO aegis_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA evidence TO aegis_app;
GRANT SELECT ON evidence.contradiction_current, evidence.claim_current_verification TO aegis_app;
GRANT USAGE ON SEQUENCE evidence.contradiction_status_event_event_id_seq TO aegis_app;
