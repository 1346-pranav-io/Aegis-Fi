-- Decision P-2 (D-28, owner): LangGraph checkpoint tables are created here, inside the migration
-- gate, instead of by the library's own setup() at runtime.
--
-- The tables are the final state of langgraph-checkpoint-postgres 3.2.0 MIGRATIONS 0-9, placed in
-- schema `runtime`. The saver uses unqualified table names, so its connection sets
-- search_path=runtime (aegis.runtime.checkpoint.postgres_checkpointer). `checkpoint_migrations`
-- is pre-filled with 0..9 so the library's setup() would find nothing to do.
-- If the library is upgraded and ships new MIGRATIONS entries, add a new migration that applies
-- them; do not call setup() at runtime.
--
-- Privileges: the application login may SELECT, INSERT and UPDATE (the saver upserts) but not
-- DELETE or TRUNCATE. Resume state is still mutable by that login; the runner therefore
-- re-checks that every evidence and claim id in a resumed state exists in this case before it
-- continues (aegis.runtime.runner). Immutability triggers are not possible because the saver
-- legitimately updates rows.
CREATE TABLE runtime.checkpoint_migrations (v integer PRIMARY KEY);
INSERT INTO runtime.checkpoint_migrations (v) SELECT generate_series(0, 9);

CREATE TABLE runtime.checkpoints (
  thread_id text NOT NULL,
  checkpoint_ns text NOT NULL DEFAULT '',
  checkpoint_id text NOT NULL,
  parent_checkpoint_id text,
  type text,
  checkpoint jsonb NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id));

CREATE TABLE runtime.checkpoint_blobs (
  thread_id text NOT NULL,
  checkpoint_ns text NOT NULL DEFAULT '',
  channel text NOT NULL,
  version text NOT NULL,
  type text NOT NULL,
  blob bytea,
  PRIMARY KEY (thread_id, checkpoint_ns, channel, version));

CREATE TABLE runtime.checkpoint_writes (
  thread_id text NOT NULL,
  checkpoint_ns text NOT NULL DEFAULT '',
  checkpoint_id text NOT NULL,
  task_id text NOT NULL,
  idx integer NOT NULL,
  channel text NOT NULL,
  type text,
  blob bytea NOT NULL,
  task_path text NOT NULL DEFAULT '',
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx));

CREATE INDEX checkpoints_thread_id_idx ON runtime.checkpoints (thread_id);
CREATE INDEX checkpoint_blobs_thread_id_idx ON runtime.checkpoint_blobs (thread_id);
CREATE INDEX checkpoint_writes_thread_id_idx ON runtime.checkpoint_writes (thread_id);

GRANT SELECT ON runtime.checkpoint_migrations TO aegis_app;
GRANT SELECT, INSERT, UPDATE ON runtime.checkpoints, runtime.checkpoint_blobs,
  runtime.checkpoint_writes TO aegis_app;
