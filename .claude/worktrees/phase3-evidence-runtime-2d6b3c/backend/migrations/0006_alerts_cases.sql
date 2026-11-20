-- Phase 2: signals, alerts, cases, approvals (FR-DET-05, FR-CASE-*, D-06, D-09).
-- Insert-only records: signal, alert, case_alert, case_event, approval_request, approval.
-- Mutable but guarded: investigation_case (state machine + approval gate enforced by trigger).
-- Roles: aegis_app writes these tables directly (column-limited UPDATE on the case); audit rows
-- are written only through audit.record / the SECURITY DEFINER trigger below (0005).
CREATE SCHEMA IF NOT EXISTS derived;
CREATE SCHEMA IF NOT EXISTS workflow;

CREATE TABLE derived.signal (
  signal_id text PRIMARY KEY,
  subject_type text NOT NULL CHECK (subject_type IN ('TRANSACTION', 'ACCOUNT')),
  subject_ref text NOT NULL,
  account_ref text NOT NULL,                 -- account the signal is about (case grouping key)
  signal_type text NOT NULL,
  score double precision NOT NULL,           -- a score, never a probability (D-06)
  calibrated_probability double precision CHECK (calibrated_probability BETWEEN 0 AND 1),
  calibrator_artefact_id text,
  reason_codes text[] NOT NULL DEFAULT '{}',
  rule_pack_version text,
  model_artefact_id text,
  as_of timestamptz NOT NULL,
  window_start timestamptz,
  window_end timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT probability_needs_calibrator
    CHECK (calibrated_probability IS NULL OR calibrator_artefact_id IS NOT NULL),
  CONSTRAINT signal_has_producer
    CHECK (rule_pack_version IS NOT NULL OR model_artefact_id IS NOT NULL));
CREATE INDEX ON derived.signal (account_ref);

CREATE TABLE workflow.alert (
  alert_id text PRIMARY KEY,
  signal_id text NOT NULL REFERENCES derived.signal,
  account_ref text NOT NULL,
  reason_codes text[] NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE workflow.approval_request (
  request_id text PRIMARY KEY,
  action_type text NOT NULL CHECK (action_type IN (
    'REGULATORY_FILING_RECOMMENDATION', 'CASE_CLOSURE', 'RESTRICTION_RECOMMENDATION',
    'ENTITY_MERGE', 'MODEL_ACTIVATION', 'FEEDBACK_TO_TRAINING')),
  subject_ref text NOT NULL,
  requested_by text NOT NULL,
  requested_at timestamptz NOT NULL DEFAULT now(),
  justification text NOT NULL);

CREATE TABLE workflow.approval (
  approval_id text PRIMARY KEY,
  request_id text NOT NULL UNIQUE REFERENCES workflow.approval_request,
  decision text NOT NULL CHECK (decision IN ('APPROVED', 'DENIED')),
  decided_by text NOT NULL,
  decider_role text NOT NULL,   -- asserted by the caller until the API/auth phase supplies real identity
  decided_at timestamptz NOT NULL DEFAULT now(),
  reason text NOT NULL,
  CONSTRAINT approval_needs_approver_role CHECK (decision = 'DENIED' OR decider_role = 'approver'));

CREATE TABLE workflow.investigation_case (
  case_id text PRIMARY KEY,
  case_number bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  status text NOT NULL CHECK (status IN ('OPEN', 'INVESTIGATING', 'PENDING_REVIEW', 'CLOSED')),
  subject_ref text NOT NULL,                 -- account the case is about
  priority_score double precision NOT NULL,  -- max alert score; a score, not a probability
  assigned_to text,
  opened_at timestamptz NOT NULL DEFAULT now(),
  last_alert_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz,
  disposition text CHECK (disposition IN ('CONFIRMED_SUSPICIOUS', 'NOT_SUSPICIOUS', 'INCONCLUSIVE')),
  closure_approval_id text REFERENCES workflow.approval,
  CONSTRAINT closed_iff_complete CHECK (
    (status = 'CLOSED') = (disposition IS NOT NULL AND closed_at IS NOT NULL
                           AND closure_approval_id IS NOT NULL)));
CREATE INDEX ON workflow.investigation_case (subject_ref, status);
CREATE INDEX ON workflow.investigation_case (priority_score DESC, opened_at, case_id);

CREATE TABLE workflow.case_alert (
  case_id text NOT NULL REFERENCES workflow.investigation_case,
  alert_id text NOT NULL UNIQUE REFERENCES workflow.alert,
  added_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (case_id, alert_id));

CREATE TABLE workflow.case_event (
  event_id bigserial PRIMARY KEY,
  case_id text NOT NULL REFERENCES workflow.investigation_case,
  from_status text NOT NULL,
  to_status text NOT NULL,
  actor text NOT NULL,
  reason text,
  at timestamptz NOT NULL DEFAULT now());

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['derived.signal', 'workflow.alert', 'workflow.approval_request',
      'workflow.approval', 'workflow.case_alert', 'workflow.case_event'] LOOP
    EXECUTE format('CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON %s
                    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation()', t);
    EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %s
                    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation()', t);
  END LOOP; END $$;

-- Case state machine and approval gate (D-09). Legal moves:
--   OPEN -> INVESTIGATING -> PENDING_REVIEW -> CLOSED ; PENDING_REVIEW -> INVESTIGATING (rework).
-- CLOSED is terminal and the row is then frozen. Closing requires an APPROVED CASE_CLOSURE
-- decision for this exact case. The reason for a transition is passed through the transaction-local
-- setting `aegis.transition_reason`; the actor is always the database login (session_user).
CREATE FUNCTION workflow.case_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
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
  IF NEW.status <> OLD.status THEN
    IF NOT ((OLD.status, NEW.status) IN (('OPEN', 'INVESTIGATING'),
            ('INVESTIGATING', 'PENDING_REVIEW'), ('PENDING_REVIEW', 'INVESTIGATING'),
            ('PENDING_REVIEW', 'CLOSED'))) THEN
      RAISE EXCEPTION 'illegal case transition % -> %', OLD.status, NEW.status;
    END IF;
    IF NEW.status = 'CLOSED' THEN
      IF NOT EXISTS (
        SELECT 1 FROM workflow.approval a JOIN workflow.approval_request r USING (request_id)
        WHERE a.approval_id = NEW.closure_approval_id AND a.decision = 'APPROVED'
          AND r.action_type = 'CASE_CLOSURE' AND r.subject_ref = NEW.case_id) THEN
        RAISE EXCEPTION 'closing case % requires an APPROVED CASE_CLOSURE decision for it', NEW.case_id;
      END IF;
    END IF;
    INSERT INTO workflow.case_event (case_id, from_status, to_status, actor, reason)
    VALUES (NEW.case_id, OLD.status, NEW.status, session_user,
            nullif(current_setting('aegis.transition_reason', true), ''));
    PERFORM audit.record('case.' || lower(NEW.status), 'investigation_case', NEW.case_id,
                         jsonb_build_object('from', OLD.status));
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION workflow.case_guard() FROM PUBLIC;
CREATE TRIGGER case_guard BEFORE UPDATE OR DELETE ON workflow.investigation_case
  FOR EACH ROW EXECUTE FUNCTION workflow.case_guard();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON workflow.investigation_case
  FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation();

GRANT USAGE ON SCHEMA derived, workflow TO aegis_app;
GRANT SELECT ON ALL TABLES IN SCHEMA derived, workflow TO aegis_app;
GRANT INSERT ON derived.signal, workflow.alert, workflow.approval_request, workflow.approval,
  workflow.case_alert, workflow.investigation_case TO aegis_app;
GRANT UPDATE (status, assigned_to, priority_score, last_alert_at, closed_at, disposition,
  closure_approval_id) ON workflow.investigation_case TO aegis_app;
