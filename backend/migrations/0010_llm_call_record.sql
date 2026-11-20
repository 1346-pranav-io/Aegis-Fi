-- Phase 3: one insert-only row per LLM gateway attempt (D-10, D-16, FR-AGT-*, replay/audit).
-- Only PSEUDONYMISED text is stored (response_text); the clear-text mapping never reaches the
-- database. Rows are never updated or deleted.
CREATE SCHEMA IF NOT EXISTS llm;

CREATE TABLE llm.call_record (
  call_id text PRIMARY KEY,
  run_id text,
  step_id text,
  agent text NOT NULL,
  role text NOT NULL,
  prompt_id text NOT NULL,
  prompt_version text NOT NULL,
  prompt_hash text NOT NULL,
  model_id text NOT NULL,             -- from configuration, never from code
  provider text NOT NULL,
  mode text NOT NULL CHECK (mode IN ('LIVE', 'RECORD', 'REPLAY')),
  request_key text NOT NULL,
  input_hash text NOT NULL,           -- hash of the exact pseudonymised system+user text
  repair_round integer NOT NULL CHECK (repair_round >= 0),
  attempt integer NOT NULL CHECK (attempt >= 0),
  status text NOT NULL CHECK (status IN (
    'OK', 'SCHEMA_INVALID', 'PROVIDER_ERROR', 'TIMEOUT', 'REPLAY_MISS', 'BUDGET_REFUSED',
    'LEAK_REFUSED')),
  from_replay boolean NOT NULL,
  tokens_in integer NOT NULL CHECK (tokens_in >= 0),
  tokens_out integer NOT NULL CHECK (tokens_out >= 0),
  cost_usd double precision NOT NULL CHECK (cost_usd >= 0),
  duration_ms integer NOT NULL CHECK (duration_ms >= 0),
  response_text text,
  error text CHECK (error IS NULL OR length(error) <= 2000),
  created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ON llm.call_record (run_id, created_at);
CREATE INDEX ON llm.call_record (request_key);

CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON llm.call_record
  FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON llm.call_record
  FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation();

GRANT USAGE ON SCHEMA llm TO aegis_app;
GRANT SELECT, INSERT ON llm.call_record TO aegis_app;
