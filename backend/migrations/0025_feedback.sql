-- Phase 5 (D-30): analyst feedback, its validation, and approval-gated export of validated feedback
-- as a versioned dataset manifest (FR-HITL-01, -03, -04; DATA_FLOW flow 5).
--
-- All three tables are insert-only. The login and time are set by triggers (never by the caller);
-- the human id (user_id) is whatever the API asserts from a verified token. Feedback NEVER edits
-- claims, evidence or source: it is a separate record; an EDIT is a proposal kept in `payload`.
-- Validation is a separate history table (a feedback row can be re-validated, e.g. after a
-- second review); the current state is the latest row, with PENDING when none exists.

CREATE TABLE workflow.feedback (
  feedback_id text PRIMARY KEY,
  case_id text NOT NULL REFERENCES workflow.investigation_case,
  target_type text NOT NULL CHECK (target_type IN
    ('CLAIM', 'ER_LINK', 'CONTRADICTION', 'ALERT', 'REPORT')),
  target_id text NOT NULL,
  action text NOT NULL CHECK (action IN ('ACCEPT', 'REJECT', 'EDIT', 'COMMENT', 'LABEL')),
  payload jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
  reason text CHECK (reason IS NULL OR length(reason) <= 2000),
  user_id text NOT NULL CHECK (length(btrim(user_id)) > 0),
  user_role text NOT NULL CHECK (user_role IN ('analyst', 'approver')),
  source text NOT NULL CHECK (source IN ('HUMAN', 'SIMULATED')),   -- disclosure of simulated feedback (Q-09)
  user_login text NOT NULL DEFAULT session_user,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT reject_and_edit_need_a_reason
    CHECK (action NOT IN ('REJECT', 'EDIT') OR (reason IS NOT NULL AND length(btrim(reason)) > 0)));
CREATE INDEX ON workflow.feedback (case_id);
CREATE INDEX ON workflow.feedback (target_type, target_id);

CREATE TABLE workflow.feedback_validation (
  validation_id bigserial PRIMARY KEY,
  feedback_id text NOT NULL REFERENCES workflow.feedback,
  status text NOT NULL CHECK (status IN ('VALIDATED', 'QUARANTINED')),
  checks jsonb NOT NULL,                       -- every rule that ran, with its outcome
  note text CHECK (note IS NULL OR length(note) <= 2000),
  validated_by text NOT NULL,                  -- 'rules:<version>' or a human id for a second review
  validator_kind text NOT NULL CHECK (validator_kind IN ('RULES', 'HUMAN')),
  validator_login text NOT NULL DEFAULT session_user,
  at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ON workflow.feedback_validation (feedback_id, validation_id DESC);

CREATE VIEW workflow.feedback_current AS
  SELECT f.*, coalesce(v.status, 'PENDING') AS validation_status, v.checks AS validation_checks
  FROM workflow.feedback f
  LEFT JOIN LATERAL (SELECT status, checks FROM workflow.feedback_validation
                     WHERE feedback_id = f.feedback_id ORDER BY validation_id DESC LIMIT 1) v ON true;

-- Exports of validated feedback for later training use. Each needs an APPROVED
-- FEEDBACK_TO_TRAINING decision (D-09f) naming this export's subject, and records the manifest
-- hash of exactly what was exported.
CREATE TABLE workflow.feedback_export (
  export_id text PRIMARY KEY,
  approval_id text NOT NULL UNIQUE REFERENCES workflow.approval,
  manifest_hash text NOT NULL CHECK (length(manifest_hash) = 64),
  feedback_ids text[] NOT NULL CHECK (cardinality(feedback_ids) > 0),
  n_simulated integer NOT NULL CHECK (n_simulated >= 0),
  created_by text NOT NULL CHECK (length(btrim(created_by)) > 0),
  created_login text NOT NULL DEFAULT session_user,
  created_at timestamptz NOT NULL DEFAULT now());

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['workflow.feedback', 'workflow.feedback_validation',
                           'workflow.feedback_export'] LOOP
    EXECUTE format('CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON %s
                    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation()', t);
    EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %s
                    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation()', t);
  END LOOP; END $$;

CREATE FUNCTION workflow.feedback_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  NEW.user_login := session_user;
  NEW.created_at := now();
  PERFORM audit.record('feedback.' || lower(NEW.action), 'feedback', NEW.feedback_id,
                       jsonb_build_object('case', NEW.case_id, 'target_type', NEW.target_type,
                                          'target_id', NEW.target_id, 'user', NEW.user_id,
                                          'source', NEW.source));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION workflow.feedback_guard() FROM PUBLIC;
CREATE TRIGGER feedback_guard BEFORE INSERT ON workflow.feedback
  FOR EACH ROW EXECUTE FUNCTION workflow.feedback_guard();

-- A rules validation is written by the application login; a HUMAN second review only by the
-- human-review login (aegis_reviewer, migration 0023), checked with session_user.
CREATE FUNCTION workflow.feedback_validation_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  NEW.validator_login := session_user;
  NEW.at := now();
  IF NEW.validator_kind = 'HUMAN' AND NOT pg_has_role(session_user, 'aegis_reviewer', 'member') THEN
    RAISE EXCEPTION 'a human second review needs the human-review role';
  END IF;
  PERFORM audit.record('feedback.' || lower(NEW.status), 'feedback', NEW.feedback_id,
                       jsonb_build_object('validated_by', NEW.validated_by,
                                          'kind', NEW.validator_kind));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION workflow.feedback_validation_guard() FROM PUBLIC;
CREATE TRIGGER feedback_validation_guard BEFORE INSERT ON workflow.feedback_validation
  FOR EACH ROW EXECUTE FUNCTION workflow.feedback_validation_guard();

CREATE FUNCTION workflow.feedback_export_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE bad integer;
BEGIN
  NEW.created_login := session_user;
  NEW.created_at := now();
  IF NOT EXISTS (
    SELECT 1 FROM workflow.approval a JOIN workflow.approval_request r USING (request_id)
    WHERE a.approval_id = NEW.approval_id AND a.decision = 'APPROVED'
      AND r.action_type = 'FEEDBACK_TO_TRAINING') THEN
    RAISE EXCEPTION 'an export needs an APPROVED FEEDBACK_TO_TRAINING decision';
  END IF;
  SELECT count(*) INTO bad FROM unnest(NEW.feedback_ids) AS x(id)
    WHERE NOT EXISTS (SELECT 1 FROM workflow.feedback_current c
                      WHERE c.feedback_id = x.id AND c.validation_status = 'VALIDATED');
  IF bad > 0 THEN
    RAISE EXCEPTION '% exported feedback item(s) are not VALIDATED', bad;
  END IF;
  SELECT count(*) INTO bad FROM unnest(NEW.feedback_ids) AS x(id)
    JOIN workflow.feedback f ON f.feedback_id = x.id WHERE f.source = 'SIMULATED';
  IF bad <> NEW.n_simulated THEN
    RAISE EXCEPTION 'n_simulated (%) does not match the exported items (%)', NEW.n_simulated, bad;
  END IF;
  PERFORM audit.record('feedback.exported', 'feedback_export', NEW.export_id,
                       jsonb_build_object('n', cardinality(NEW.feedback_ids),
                                          'manifest', NEW.manifest_hash,
                                          'approval', NEW.approval_id));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION workflow.feedback_export_guard() FROM PUBLIC;
CREATE TRIGGER feedback_export_guard BEFORE INSERT ON workflow.feedback_export
  FOR EACH ROW EXECUTE FUNCTION workflow.feedback_export_guard();

GRANT SELECT, INSERT ON workflow.feedback, workflow.feedback_export TO aegis_app;
GRANT SELECT, INSERT ON workflow.feedback_validation TO aegis_app, aegis_reviewer;
GRANT SELECT ON workflow.feedback_current TO aegis_app, aegis_reviewer;
GRANT SELECT ON workflow.feedback, workflow.feedback_export TO aegis_reviewer;
GRANT USAGE ON SEQUENCE workflow.feedback_validation_validation_id_seq TO aegis_app, aegis_reviewer;
