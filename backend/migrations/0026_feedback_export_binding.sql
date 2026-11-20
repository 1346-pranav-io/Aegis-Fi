-- 0026: a FEEDBACK_TO_TRAINING approval is bound to the exact export it approved.
-- The approval request's subject_ref must be 'feedback-export:<manifest_hash>' for the manifest
-- hash being exported. Changing the validated feedback after approval changes the hash, so the
-- old approval no longer covers it. (Review finding H-2.)
-- The hash is computed by the application over the exported records; the database checks the
-- binding, not the hash derivation.
CREATE OR REPLACE FUNCTION workflow.feedback_export_guard() RETURNS trigger
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
  IF NOT EXISTS (
    SELECT 1 FROM workflow.approval a JOIN workflow.approval_request r USING (request_id)
    WHERE a.approval_id = NEW.approval_id
      AND r.subject_ref = 'feedback-export:' || NEW.manifest_hash) THEN
    RAISE EXCEPTION 'the approval does not cover this export (manifest hash differs)';
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
