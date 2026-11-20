-- Phase 4 closure gating (FR-CON, EVIDENCE_MODEL 8, T-INV-18). PROPOSED by the Phase 4 stream;
-- NOT approved. Needs an owner entry in APPROVALS.json.
--
-- Replaces workflow.case_guard (body of 0008 kept verbatim) with ONE additional rule: a case
-- cannot be CLOSED while it has a contradiction of severity HIGH whose current status is OPEN.
-- Only a human can move a contradiction to EXPLAINED / RESOLVED_BY_HUMAN / DISMISSED (the
-- evidence schema already enforces that), so closing needs a human to have dealt with each one.
-- Every earlier guard is unchanged: legal transitions, the approval gate with the review-time
-- ordering, monotonic and finite priority, last_alert_at bounds, identity immutability, CLOSED
-- is frozen, the reason cap and the audit rows.
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
  IF NEW.last_alert_at < OLD.last_alert_at OR NEW.last_alert_at > now() THEN
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
