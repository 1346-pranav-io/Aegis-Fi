-- Supports idempotent label inserts during bulk ingestion (anti-join on these columns).
CREATE INDEX label_dedupe_idx ON source.label (subject_ref, label_name, provenance, label_value);
