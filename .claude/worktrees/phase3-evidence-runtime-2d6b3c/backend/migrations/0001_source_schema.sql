-- Phase 1: source schema, insert-only (D-04). Run by aegis_migrator only.
CREATE SCHEMA IF NOT EXISTS source;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE source.ingestion_batch (
  batch_id text PRIMARY KEY, dataset_name text NOT NULL, dataset_version text NOT NULL,
  adapter text NOT NULL, adapter_version text NOT NULL, file_hashes jsonb NOT NULL,
  record_count bigint, quarantine_count bigint,
  status text NOT NULL CHECK (status IN ('RUNNING','COMPLETE','FAILED')),
  started_at timestamptz NOT NULL, completed_at timestamptz);

CREATE TABLE source.account (
  account_id text PRIMARY KEY, batch_id text NOT NULL REFERENCES source.ingestion_batch,
  source_account_ref text NOT NULL, institution_id text, currency text,
  event_time timestamptz, ingested_at timestamptz NOT NULL DEFAULT now(),
  content_hash text NOT NULL, supersedes text REFERENCES source.account,
  UNIQUE (source_account_ref, content_hash));

CREATE TABLE source.transaction (
  txn_id text PRIMARY KEY, batch_id text NOT NULL REFERENCES source.ingestion_batch,
  source_txn_ref text NOT NULL, event_time timestamptz NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  origin_account_id text NOT NULL REFERENCES source.account,
  destination_account_id text NOT NULL REFERENCES source.account,
  amount numeric NOT NULL CHECK (amount > 0), currency char(3) NOT NULL,
  txn_type text, channel text, attributes jsonb NOT NULL DEFAULT '{}',
  content_hash text NOT NULL, supersedes text REFERENCES source.transaction,
  UNIQUE (source_txn_ref, content_hash));
CREATE INDEX ON source.transaction (event_time);
CREATE INDEX ON source.transaction (ingested_at);

CREATE TABLE source.label (
  label_id text PRIMARY KEY, batch_id text NOT NULL REFERENCES source.ingestion_batch,
  subject_type text NOT NULL, subject_ref text NOT NULL, label_name text NOT NULL,
  label_value boolean NOT NULL, pattern_type text, provenance text NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE source.quarantine (
  quarantine_id bigserial PRIMARY KEY, batch_id text NOT NULL REFERENCES source.ingestion_batch,
  raw_record text NOT NULL, error_code text NOT NULL, error_detail text NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE audit.event (
  event_id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(),
  actor_type text NOT NULL, actor_id text NOT NULL, action text NOT NULL,
  object_type text NOT NULL, object_id text NOT NULL, correlation_id text, details jsonb);

-- Immutability: reject UPDATE/DELETE/TRUNCATE on protected tables for everyone.
CREATE FUNCTION audit.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'table %.% is insert-only', TG_TABLE_SCHEMA, TG_TABLE_NAME; END $$;

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['source.account','source.transaction','source.label',
                           'source.quarantine','audit.event'] LOOP
    EXECUTE format('CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON %s
                    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation()', t);
    EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %s
                    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation()', t);
  END LOOP; END $$;

-- ingestion_batch: only status/counts/completed_at may change; no DELETE.
CREATE FUNCTION source.batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'batches cannot be deleted'; END IF;
  IF NEW.batch_id <> OLD.batch_id OR NEW.file_hashes <> OLD.file_hashes
     OR NEW.dataset_name <> OLD.dataset_name THEN
    RAISE EXCEPTION 'batch identity fields are immutable'; END IF;
  RETURN NEW; END $$;
CREATE TRIGGER batch_guard BEFORE UPDATE OR DELETE ON source.ingestion_batch
  FOR EACH ROW EXECUTE FUNCTION source.batch_guard();

-- Roles (passwords set outside this file; never committed).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aegis_ingest') THEN CREATE ROLE aegis_ingest LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aegis_app') THEN CREATE ROLE aegis_app LOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA source, audit TO aegis_ingest, aegis_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA source TO aegis_ingest;
GRANT UPDATE (status, record_count, quarantine_count, completed_at) ON source.ingestion_batch TO aegis_ingest;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA source TO aegis_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA source TO aegis_app;
GRANT SELECT, INSERT ON audit.event TO aegis_app, aegis_ingest;
GRANT USAGE ON SEQUENCE audit.event_event_id_seq TO aegis_app, aegis_ingest;
