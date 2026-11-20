-- Phase 2 re-review findings NEW-1, NEW-2, NEW-3 (docs/PHASE2_EXIT_REVIEW.md section 9), owner-approved.
--
-- NEW-1  bump_case now takes the alert that justifies the bump, refuses non-finite scores and
--        alerts not attached to the case, and audits a priority increase.
-- NEW-2  Cases are inserted with no assignee and a finite priority; the guard also refuses
--        non-finite priorities on update. derived.signal.score must be finite.
-- NEW-3  Application logins can no longer call audit.record (free-text actions). They get
--        audit.record_app, which only accepts actions starting with 'app.', so they cannot write
--        entries that look like system events such as case.closed or approval.*. Approval
--        requests and decisions now write their own audit rows.
-- Not addressed (LOW, left open): the closing disposition is still chosen by the closer and is
-- not bound to the approved request (NEW-4); a single shared approver login (NEW-5).

ALTER TABLE derived.signal ADD CONSTRAINT signal_score_finite
  CHECK (score > '-Infinity'::float8 AND score < 'Infinity'::float8);

DROP FUNCTION workflow.bump_case(text, double precision);
CREATE FUNCTION workflow.bump_case(p_case text, p_score double precision, p_alert text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE old_score double precision;
BEGIN
  IF p_score IS NULL OR NOT (p_score > '-Infinity'::float8 AND p_score < 'Infinity'::float8) THEN
    RAISE EXCEPTION 'bump_case needs a finite score';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM workflow.case_alert WHERE case_id = p_case AND alert_id = p_alert) THEN
    RAISE EXCEPTION 'alert % is not attached to case %', p_alert, p_case;
  END IF;
  SELECT priority_score INTO old_score FROM workflow.investigation_case
    WHERE case_id = p_case AND status <> 'CLOSED' FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no open case %', p_case;
  END IF;
  UPDATE workflow.investigation_case
     SET priority_score = greatest(priority_score, p_score), last_alert_at = now()
   WHERE case_id = p_case;
  IF p_score > old_score THEN
    PERFORM audit.record('case.priority_raised', 'investigation_case', p_case,
                         jsonb_build_object('from', old_score, 'to', p_score, 'alert', p_alert));
  END IF;
END $$;
REVOKE ALL ON FUNCTION workflow.bump_case(text, double precision, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION workflow.bump_case(text, double precision, text) TO aegis_app;

CREATE OR REPLACE FUNCTION workflow.case_insert_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.status <> 'OPEN' OR NEW.closed_at IS NOT NULL OR NEW.disposition IS NOT NULL
     OR NEW.closure_approval_id IS NOT NULL THEN
    RAISE EXCEPTION 'cases must be inserted OPEN with no closure data';
  END IF;
  IF NEW.assigned_to IS NOT NULL THEN
    RAISE EXCEPTION 'cases are assigned after creation through assign_case (audited)';
  END IF;
  IF NOT (NEW.priority_score > '-Infinity'::float8 AND NEW.priority_score < 'Infinity'::float8) THEN
    RAISE EXCEPTION 'priority_score must be finite';
  END IF;
  NEW.opened_at := now();
  NEW.last_alert_at := now();
  RETURN NEW;
END $$;

-- The update guard additionally refuses a non-finite priority. Body otherwise unchanged from 0007.
CREATE OR REPLACE FUNCTION workflow.case_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE review_at timestamptz;
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

-- Approval requests and decisions are audited by the triggers that already set their identity.
CREATE OR REPLACE FUNCTION workflow.request_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  NEW.requested_login := session_user;
  NEW.requested_at := now();
  PERFORM audit.record('approval.requested', 'approval_request', NEW.request_id,
                       jsonb_build_object('action_type', NEW.action_type,
                                          'subject_ref', NEW.subject_ref,
                                          'requested_by', NEW.requested_by));
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION workflow.approval_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE r workflow.approval_request%ROWTYPE;
BEGIN
  IF NOT pg_has_role(session_user, 'aegis_approver', 'member') THEN
    RAISE EXCEPTION 'only the approver role may record decisions';
  END IF;
  SELECT * INTO r FROM workflow.approval_request WHERE request_id = NEW.request_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown approval request %', NEW.request_id;
  END IF;
  IF r.requested_by = NEW.decided_by THEN
    RAISE EXCEPTION 'the requester (%) cannot decide their own request', r.requested_by;
  END IF;
  IF r.requested_login = session_user THEN
    RAISE EXCEPTION 'the requesting login cannot decide its own request';
  END IF;
  NEW.decided_login := session_user;
  NEW.decider_role := 'approver';
  NEW.decided_at := now();
  PERFORM audit.record('approval.' || lower(NEW.decision), 'approval_request', NEW.request_id,
                       jsonb_build_object('decided_by', NEW.decided_by,
                                          'action_type', r.action_type,
                                          'subject_ref', r.subject_ref));
  RETURN NEW;
END $$;

-- Application logins get a restricted audit writer; the free-text one becomes internal-only.
CREATE FUNCTION audit.record_app(p_action text, p_object_type text, p_object_id text,
                                 p_details jsonb DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF p_action IS NULL OR p_action NOT LIKE 'app.%' THEN
    RAISE EXCEPTION 'application audit actions must start with app.';
  END IF;
  PERFORM audit.record(p_action, p_object_type, p_object_id, p_details);
END $$;
REVOKE ALL ON FUNCTION audit.record_app(text, text, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit.record_app(text, text, text, jsonb)
  TO aegis_app, aegis_ingest, aegis_approver;
REVOKE EXECUTE ON FUNCTION audit.record(text, text, text, jsonb) FROM aegis_app, aegis_ingest;
