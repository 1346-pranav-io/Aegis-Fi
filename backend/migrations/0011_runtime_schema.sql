-- Phase 3: investigation runs, agent steps, job queue, report versions (AGENT_ARCHITECTURE 3,10,12).
--   * investigation_run, run_status_event, agent_step, report are insert-only.
--   * run status moves QUEUED -> RUNNING -> one terminal status; terminal statuses are final.
--     (RUNNING -> RUNNING is allowed so a resumed run can record that it was resumed.)
--   * agent_step is unique per (run_id, task_id): a resumed run reuses a recorded step instead of
--     repeating it (kill/resume, T-INV-16).
--   * report versions are immutable and numbered per case (a regenerated report is a new version).
--   * job is a work queue and is the one mutable table (claimed with FOR UPDATE SKIP LOCKED).
-- LangGraph's own checkpoint tables are created by its saver setup(), not by this file.
CREATE SCHEMA IF NOT EXISTS runtime;

CREATE TABLE runtime.investigation_run (
  run_id text PRIMARY KEY,
  case_id text NOT NULL REFERENCES workflow.investigation_case,
  as_of timestamptz NOT NULL,
  policy_name text NOT NULL,
  policy_version text NOT NULL,
  budget jsonb NOT NULL,
  config jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ON runtime.investigation_run (case_id, created_at);

CREATE TABLE runtime.run_status_event (
  event_id bigserial PRIMARY KEY,
  run_id text NOT NULL REFERENCES runtime.investigation_run,
  status text NOT NULL CHECK (status IN (
    'QUEUED', 'RUNNING', 'COMPLETE', 'PARTIAL', 'BUDGET_EXHAUSTED', 'FAILED')),
  reason text CHECK (reason IS NULL OR length(reason) <= 2000),
  at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE INDEX ON runtime.run_status_event (run_id, event_id);

CREATE TABLE runtime.agent_step (
  step_id text PRIMARY KEY,
  run_id text NOT NULL REFERENCES runtime.investigation_run,
  task_id text NOT NULL,
  seq integer NOT NULL CHECK (seq >= 0),
  agent text NOT NULL,
  task_type text NOT NULL,
  capability_version text NOT NULL,
  status text NOT NULL CHECK (status IN ('OK', 'PARTIAL', 'FAILED', 'BUDGET_EXHAUSTED', 'DENIED')),
  input_hash text NOT NULL,
  output jsonb NOT NULL,            -- ids and hashes only: evidence ids, claim ids, proposals
  tool_calls jsonb NOT NULL,
  llm_call_ids jsonb NOT NULL,
  tokens_in integer NOT NULL CHECK (tokens_in >= 0),
  tokens_out integer NOT NULL CHECK (tokens_out >= 0),
  cost_usd double precision NOT NULL CHECK (cost_usd >= 0),
  duration_ms integer NOT NULL CHECK (duration_ms >= 0),
  error jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT one_step_per_task UNIQUE (run_id, task_id),
  CONSTRAINT failed_needs_error CHECK (status NOT IN ('FAILED', 'DENIED') OR error IS NOT NULL));
CREATE INDEX ON runtime.agent_step (run_id, seq);

CREATE TABLE runtime.report (
  report_id text PRIMARY KEY,
  case_id text NOT NULL REFERENCES workflow.investigation_case,
  run_id text REFERENCES runtime.investigation_run,
  version integer NOT NULL CHECK (version >= 1),
  body jsonb NOT NULL,
  body_hash text NOT NULL,
  generator jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT one_version_per_case UNIQUE (case_id, version));

CREATE TABLE runtime.job (
  job_id text PRIMARY KEY,
  run_id text NOT NULL REFERENCES runtime.investigation_run,
  status text NOT NULL CHECK (status IN ('QUEUED', 'CLAIMED', 'DONE', 'FAILED')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  locked_by text,
  locked_until timestamptz,
  last_error text CHECK (last_error IS NULL OR length(last_error) <= 2000),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ON runtime.job (status, created_at);

CREATE VIEW runtime.run_current AS
  SELECT DISTINCT ON (r.run_id) r.run_id, r.case_id, r.policy_name, r.policy_version,
         e.status, e.reason, e.at
  FROM runtime.investigation_run r JOIN runtime.run_status_event e USING (run_id)
  ORDER BY r.run_id, e.event_id DESC;

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['runtime.investigation_run', 'runtime.run_status_event',
                           'runtime.agent_step', 'runtime.report'] LOOP
    EXECUTE format('CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON %s
                    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation()', t);
    EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %s
                    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation()', t);
  END LOOP; END $$;

CREATE FUNCTION runtime.run_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur text;
BEGIN
  SELECT status INTO cur FROM runtime.run_status_event
    WHERE run_id = NEW.run_id ORDER BY event_id DESC LIMIT 1;
  IF cur IS NULL THEN
    IF NEW.status <> 'QUEUED' THEN
      RAISE EXCEPTION 'the first status of a run must be QUEUED';
    END IF;
  ELSIF NOT ((cur, NEW.status) IN (
      ('QUEUED', 'RUNNING'), ('QUEUED', 'FAILED'), ('RUNNING', 'RUNNING'),
      ('RUNNING', 'COMPLETE'), ('RUNNING', 'PARTIAL'), ('RUNNING', 'BUDGET_EXHAUSTED'),
      ('RUNNING', 'FAILED'))) THEN
    RAISE EXCEPTION 'illegal run status change % -> %', cur, NEW.status;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER status_transition BEFORE INSERT ON runtime.run_status_event
  FOR EACH ROW EXECUTE FUNCTION runtime.run_status_transition();

-- A report version must be the next number for its case (no gaps, no overwrites).
CREATE FUNCTION runtime.report_next_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prev integer;
BEGIN
  SELECT coalesce(max(version), 0) INTO prev FROM runtime.report WHERE case_id = NEW.case_id;
  IF NEW.version <> prev + 1 THEN
    RAISE EXCEPTION 'report version must be % for case %', prev + 1, NEW.case_id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER next_version BEFORE INSERT ON runtime.report
  FOR EACH ROW EXECUTE FUNCTION runtime.report_next_version();

GRANT USAGE ON SCHEMA runtime TO aegis_app;
GRANT SELECT, INSERT ON runtime.investigation_run, runtime.run_status_event, runtime.agent_step,
  runtime.report TO aegis_app;
GRANT SELECT, INSERT, UPDATE ON runtime.job TO aegis_app;
GRANT SELECT ON runtime.run_current TO aegis_app;
GRANT USAGE ON SEQUENCE runtime.run_status_event_event_id_seq TO aegis_app;
