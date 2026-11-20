-- Phase 2 security fixes H-1, M-1, M-2, L-1 (docs/PHASE2_EXIT_REVIEW.md), owner-approved.
--
-- H-1  Approvals can only be written by a separate login role (aegis_approver). The request and
--      decision logins are recorded by trigger (session_user, not caller text), the human ids may
--      not match (requester != decider), and timestamps are set by the database.
-- M-1  aegis_app loses direct UPDATE on priority_score / last_alert_at / assigned_to; these change
--      only through definer functions that keep them monotonic and audited. Inserts of cases are
--      normalised by trigger.
-- M-2  A closing approval must have been REQUESTED after the case entered PENDING_REVIEW.
-- L-1  case_event.reason is capped at 500 characters.
--
-- Limit that remains: the human ids (requested_by / decided_by) are still caller-asserted text
-- until the API phase supplies verified identities. What the database now guarantees is that
-- the app service login cannot write approval decisions at all.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis_approver') THEN
    CREATE ROLE aegis_approver LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END $$;

ALTER TABLE workflow.approval_request
  ADD COLUMN requested_login text NOT NULL DEFAULT session_user;
ALTER TABLE workflow.approval
  ADD COLUMN decided_login text NOT NULL DEFAULT session_user;
ALTER TABLE workflow.case_event
  ADD CONSTRAINT case_event_reason_len CHECK (reason IS NULL OR length(reason) <= 500);

-- Requests: login and time are set by the database, never by the caller.
CREATE FUNCTION workflow.request_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  NEW.requested_login := session_user;
  NEW.requested_at := now();
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION workflow.request_guard() FROM PUBLIC;
CREATE TRIGGER request_guard BEFORE INSERT ON workflow.approval_request
  FOR EACH ROW EXECUTE FUNCTION workflow.request_guard();

-- Decisions: only members of aegis_approver, requester != decider, login and time set by the DB.
CREATE FUNCTION workflow.approval_guard() RETURNS trigger
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
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION workflow.approval_guard() FROM PUBLIC;
CREATE TRIGGER approval_guard BEFORE INSERT ON workflow.approval
  FOR EACH ROW EXECUTE FUNCTION workflow.approval_guard();

-- Cases may only be inserted OPEN and fresh.
CREATE FUNCTION workflow.case_insert_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.status <> 'OPEN' OR NEW.closed_at IS NOT NULL OR NEW.disposition IS NOT NULL
     OR NEW.closure_approval_id IS NOT NULL THEN
    RAISE EXCEPTION 'cases must be inserted OPEN with no closure data';
  END IF;
  NEW.opened_at := now();
  NEW.last_alert_at := now();
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION workflow.case_insert_guard() FROM PUBLIC;
CREATE TRIGGER case_insert_guard BEFORE INSERT ON workflow.investigation_case
  FOR EACH ROW EXECUTE FUNCTION workflow.case_insert_guard();

-- Replacement of the case guard: adds monotonic priority/last_alert_at, the reason cap, and
-- requires the closing request to postdate the case's latest move into PENDING_REVIEW.
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

-- Audited ways to change what aegis_app can no longer UPDATE directly.
CREATE FUNCTION workflow.bump_case(p_case text, p_score double precision) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  UPDATE workflow.investigation_case
     SET priority_score = greatest(priority_score, p_score), last_alert_at = now()
   WHERE case_id = p_case AND status <> 'CLOSED'
$$;
CREATE FUNCTION workflow.assign_case(p_case text, p_user text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  UPDATE workflow.investigation_case SET assigned_to = p_user
   WHERE case_id = p_case AND status <> 'CLOSED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no open case %', p_case;
  END IF;
  PERFORM audit.record('case.assigned', 'investigation_case', p_case,
                       jsonb_build_object('assigned_to', p_user));
END $$;
REVOKE ALL ON FUNCTION workflow.bump_case(text, double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION workflow.assign_case(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION workflow.bump_case(text, double precision) TO aegis_app;
GRANT EXECUTE ON FUNCTION workflow.assign_case(text, text) TO aegis_app;

REVOKE UPDATE (priority_score, last_alert_at, assigned_to) ON workflow.investigation_case
  FROM aegis_app;
REVOKE INSERT ON workflow.approval FROM aegis_app;

GRANT USAGE ON SCHEMA workflow TO aegis_approver;
GRANT SELECT ON workflow.approval_request, workflow.approval, workflow.investigation_case
  TO aegis_approver;
GRANT INSERT ON workflow.approval TO aegis_approver;
