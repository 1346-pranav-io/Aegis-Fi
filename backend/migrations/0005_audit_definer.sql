-- Audit forgery fix (review findings S-4 / M-3). Application roles lose direct INSERT on
-- audit.event; rows can only be written through SECURITY DEFINER functions that bind the actor
-- to session_user (the login role), so callers cannot choose or fake an actor.
-- Note: definer functions are owned by the migration role (a superuser in local dev). In a real
-- deployment use a dedicated non-superuser owner holding only INSERT on audit.event.
CREATE FUNCTION audit.record(p_action text, p_object_type text, p_object_id text,
                             p_details jsonb DEFAULT NULL)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  INSERT INTO audit.event (actor_type, actor_id, action, object_type, object_id, details)
  VALUES ('DB_ROLE', session_user, p_action, p_object_type, p_object_id, p_details)
$$;
REVOKE ALL ON FUNCTION audit.record(text, text, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit.record(text, text, text, jsonb) TO aegis_ingest, aegis_app;

CREATE OR REPLACE FUNCTION source.batch_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
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
  PERFORM audit.record('batch.' || lower(NEW.status), 'ingestion_batch', NEW.batch_id,
                       jsonb_build_object('record_count', NEW.record_count,
                                          'quarantine_count', NEW.quarantine_count));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION source.batch_guard() FROM PUBLIC;

REVOKE INSERT ON audit.event FROM aegis_ingest, aegis_app;
REVOKE USAGE ON SEQUENCE audit.event_event_id_seq FROM aegis_ingest, aegis_app;
