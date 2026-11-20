-- Phase 4: detectors are re-run, so recording the same contradiction twice must be impossible.
-- A deterministic fingerprint (type + detector + sorted member ids) is stored in the existing
-- `detector` jsonb; this unique index makes a duplicate insert fail. Index only; PROPOSED by the
-- Phase 4 stream, needs an owner entry in APPROVALS.json if the runner classifies it protected.
CREATE UNIQUE INDEX contradiction_fingerprint_once
  ON evidence.contradiction (case_id, (detector->>'fingerprint'))
  WHERE detector ? 'fingerprint';
