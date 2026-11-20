-- OWNER-APPROVED: yes (2026-11-11, owner replied "Yes, fix them" to security findings S-1..S-3, S-5)
-- S-3: ingestion_batch may only move RUNNING -> COMPLETE|FAILED, then is frozen; every
-- transition is written to audit.event with the acting database user. Adds the missing
-- TRUNCATE guard and protects the migration-tracking table.
CREATE OR REPLACE FUNCTION source.batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'batches cannot be deleted';
  END IF;
  IF OLD.status <> 'RUNNING' THEN
    RAISE EXCEPTION 'batch % is % and terminal', OLD.batch_id, OLD.status;
  END IF;
  IF NEW.status NOT IN ('COMPLETE', 'FAILED') THEN
    RAISE EXCEPTION 'batch status may only move RUNNING -> COMPLETE or FAILED';
  END IF;
  IF (NEW.batch_id, NEW.dataset_name, NEW.dataset_version, NEW.adapter, NEW.adapter_version,
      NEW.file_hashes, NEW.started_at)
     IS DISTINCT FROM
     (OLD.batch_id, OLD.dataset_name, OLD.dataset_version, OLD.adapter, OLD.adapter_version,
      OLD.file_hashes, OLD.started_at) THEN
    RAISE EXCEPTION 'batch identity fields are immutable';
  END IF;
  INSERT INTO audit.event (actor_type, actor_id, action, object_type, object_id, details)
  VALUES ('SYSTEM', current_user, 'batch.' || lower(NEW.status), 'ingestion_batch', NEW.batch_id,
          jsonb_build_object('record_count', NEW.record_count,
                             'quarantine_count', NEW.quarantine_count));
  RETURN NEW;
END $$;

CREATE TRIGGER no_truncate BEFORE TRUNCATE ON source.ingestion_batch
  FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation();

CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON public.schema_migrations
  FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON public.schema_migrations
  FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation();

-- Least privilege: ingestion has no need to read the audit log.
REVOKE SELECT ON audit.event FROM aegis_ingest;
