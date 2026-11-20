# Data Schema

Status: DRAFT · Version 0.1. Logical schema; physical DDL is produced in Phase 1 via Alembic. Types are Pydantic/Postgres hybrid shorthand. All timestamps `timestamptz` UTC. All IDs are prefixed ULID strings. Decisions: D-04, D-06, D-09, D-17.

Conventions: `🔒` = immutable (insert-only, UPDATE/DELETE blocked by grants + trigger). `⟳` = mutable workflow state (every change also writes `audit.event`). `content_hash` = SHA-256 over canonical JSON of the record's business fields.

## 1. Schema `source` 🔒 (raw financial records)
**ingestion_batch** — `batch_id`, `dataset_name`, `dataset_version`, `adapter`, `adapter_version`, `file_hashes[]`, `record_count`, `quarantine_count`, `status` (`RUNNING|COMPLETE|FAILED`), `started_at`, `completed_at`. (status transitions are the one allowed update; recorded in audit.)

**party** 🔒 — `party_id`, `batch_id`, `source_party_ref`, `party_type` (`INDIVIDUAL|ORGANISATION`), `name`, `country`, `registration_or_dob` (nullable), `identifiers` (JSONB list of `{type,value}`), `attributes` (JSONB), `event_time`, `ingested_at`, `content_hash`, `supersedes` (nullable).

**account** 🔒 — `account_id`, `batch_id`, `source_account_ref`, `institution_id`, `currency`, `account_type`, `opened_at`, `status`, `event_time`, `ingested_at`, `content_hash`, `supersedes`.

**account_party_link** 🔒 — `link_id`, `account_id`, `party_id`, `role` (`OWNER|SIGNATORY|BENEFICIARY|…`), `valid_from`, `valid_to`, `ingested_at`, `content_hash`.

**transaction** 🔒 — `txn_id`, `batch_id`, `source_txn_ref`, `event_time` (execution time), `ingested_at`, `origin_account_id`, `destination_account_id`, `amount` (numeric, exact), `currency`, `amount_base` (nullable; converted amount if rate source present), `fx_rate_ref` (nullable), `txn_type` (`TRANSFER|CASH_IN|CASH_OUT|CARD|…`), `channel`, `counterparty_name_raw` (nullable), `narrative` (nullable), `label_source_flag` (nullable; **ground-truth label from dataset — stored in separate `source.label`, never in features table**), `content_hash`, `supersedes`.

**label** 🔒 — `label_id`, `subject_type`, `subject_id`, `label_name` (e.g. `LAUNDERING`), `label_value`, `pattern_type` (nullable, dataset-provided typology), `provenance` (dataset/version), `ingested_at`. *Kept separate to prevent label leakage into features.*

**quarantine** 🔒 — `quarantine_id`, `batch_id`, `raw_record` (text/JSONB), `error_code`, `error_detail`, `ingested_at`.

**reference_list** 🔒 / **reference_entry** 🔒 — versioned watchlist/sanctions/PEP data: `list_name`, `list_version`, `entry_id`, `names[]`, `identifiers`, `attributes`, `source_hash`.

**document** 🔒 — `document_id`, `kind` (`KYC_NOTE|ADVERSE_MEDIA|…`), `subject_ref`, `text`, `content_hash`, `ingested_at`.

## 2. Schema `derived` (recomputable; versioned)
**entity_mention** — `mention_id`, `source_table`, `source_id`, `mention_type`, `normalised_name`, `identifiers` (JSONB), `attributes` (JSONB), `created_at`, `extractor_version`.

**resolved_entity** ⟳ — `entity_id`, `entity_type`, `canonical_name`, `status` (`ACTIVE|MERGED_INTO|SPLIT`), `merged_into` (nullable), `created_at`.

**er_link** — `link_id`, `mention_id`, `entity_id`, `method` (`EXACT_ID|FUZZY_NAME|…`), `match_score` (numeric; **similarity, not probability**), `calibrated_probability` (nullable), `evidence_features` (JSONB: which attributes matched), `status` (`PROPOSED|CONFIRMED|REJECTED|SUPERSEDED`), `decided_by` (nullable user), `decided_at`, `scorer_version`. Status changes append new rows (history preserved).

**signal** — `signal_id`, `subject_type`, `subject_id`, `signal_type`, `score`, `calibrated_probability` (nullable), `reason_codes[]`, `rule_pack_version` (nullable), `model_artefact_id` (nullable), `as_of`, `window_start`, `window_end`, `created_at`.

**feature_snapshot** — `snapshot_id`, `subject_id`, `as_of`, `feature_set_version`, `features` (JSONB / columnar), `manifest_hash`.

## 3. Schema `evidence` 🔒
Detailed in EVIDENCE_MODEL.md: **evidence_item**, **evidence_input** (derivation edges), **claim**, **claim_evidence_link**, **verification_result**, **contradiction**, **contradiction_member**.

## 4. Schema `workflow` ⟳ (mutable state, audited)
**alert** — `alert_id`, `signal_id`, `subject_id`, `reason_codes[]`, `created_at`, `case_id` (nullable), `status`.

**case** ⟳ — `case_id`, `case_number`, `status` (`OPEN|INVESTIGATING|PENDING_REVIEW|CLOSED`), `priority_score` (`score`), `primary_typology_hypothesis` (nullable claim id), `assigned_to`, `opened_at`, `closed_at`, `disposition` (nullable: `CONFIRMED_SUSPICIOUS|NOT_SUSPICIOUS|INCONCLUSIVE`), `closure_approval_id`.

**case_subject** — `case_id`, `entity_id`, `role` (`PRIMARY|RELATED`), `added_by`.

**investigation_run** ⟳ — `run_id`, `case_id`, `as_of`, `policy_name`, `policy_version`, `config_hash`, `code_version`, `status` (`QUEUED|RUNNING|COMPLETE|PARTIAL|BUDGET_EXHAUSTED|FAILED`), `budget` (JSONB), `usage` (JSONB), `started_at`, `finished_at`, `parent_run_id` (nullable; reruns).

**agent_step** 🔒 — `step_id`, `run_id`, `task` (JSONB AgentTask), `agent`, `agent_version`, `input_hash`, `result_status`, `output_refs[]`, `tool_calls` (JSONB), `llm_call_ids[]`, `tokens_in`, `tokens_out`, `cost`, `started_at`, `duration_ms`, `error` (JSONB).

**run_checkpoint** — LangGraph checkpoint rows (library-managed; documented, not hand-edited).

**job** ⟳ — `job_id`, `kind`, `payload`, `idempotency_key` (unique), `status`, `lease_until`, `attempts`, `run_after`.

**report** 🔒 — `report_id`, `case_id`, `version`, `run_id`, `content` (JSONB, sections with citations), `rendered_uri` (nullable), `created_at`, `template_version`. Versioned by new rows.

**pending_approval** ⟳ / **approval** 🔒 — `approval_id`, `action_type` (D-09 enumeration), `subject_ref`, `requested_by` (agent or user), `requested_at`, `justification_refs[]`, `decision` (`APPROVED|DENIED`), `decided_by`, `decided_at`, `decision_reason`.

**feedback** 🔒 — `feedback_id`, `case_id`, `target_type` (`CLAIM|ER_LINK|CONTRADICTION|ALERT|REPORT`), `target_id`, `action` (`ACCEPT|REJECT|EDIT|COMMENT|LABEL`), `payload` (JSONB), `reason`, `user_id`, `user_role`, `created_at`, `validation_status` (`PENDING|VALIDATED|QUARANTINED`), `validation_notes`, `eligible_for_training` (bool, requires approval D-09f).

**user** / **role_assignment** — identity mirror from OIDC; roles per D-15.

## 5. Schema `ml`
**dataset_manifest** 🔒 — `manifest_id`, `name`, `version`, `source_batches[]`, `time_range`, `split_spec`, `file_hashes`, `row_counts`, `created_by_code_version`, `manifest_hash`.

**model_artefact** 🔒 — `artefact_id`, `name`, `version`, `kind` (`CLASSIFIER|CALIBRATOR|ER_SCORER|POLICY`), `mlflow_run_id`, `training_manifest_id`, `code_version`, `hyperparameters`, `metrics` (JSONB; includes calibration error if any), `status` (`CANDIDATE|ACTIVE|RETIRED`), `activated_by_approval_id`, `created_at`.

## 6. Schema `eval`
**experiment** — `experiment_id`, `config` (JSONB), `config_hash`, `manifest_id`, `seeds`, `code_version`, `image_digest`, `mlflow_experiment`, `created_at`.
**experiment_run_result** — `run_result_id`, `experiment_id`, `case_or_unit_id`, `policy`, `ablation`, `metrics` (JSONB), `llm_record_set_id`.
**llm_call_record** 🔒 — `llm_call_id`, `model_id`, `prompt_id`, `prompt_version`, `prompt_hash`, `input_hash`, `request_pseudonymised`, `response`, `tokens`, `cost`, `latency_ms`, `created_at`, `run_id`, `step_id`. (Used for replay and audit; stored under `audit` in production mode.)

## 7. Schema `audit` 🔒
**event** — `event_id`, `ts`, `actor_type` (`USER|AGENT|SYSTEM`), `actor_id`, `action`, `object_type`, `object_id`, `before_hash`, `after_hash`, `correlation_id`, `details` (JSONB).

## 8. Key relationships
```
party ─< account_party_link >─ account ─< transaction (origin/destination)
party/account/transaction → entity_mention → er_link → resolved_entity
signal → alert → case ─< case_subject >─ resolved_entity
case ─< investigation_run ─< agent_step → evidence_item ─< claim_evidence_link >─ claim
claim ─< verification_result ;  contradiction ─< contradiction_member >─ (evidence|claim)
case ─< report(version) ;  case/claim/er_link ─< feedback ;  approval ↔ pending_approval
```

## 9. Integrity rules
1. FKs everywhere; no orphan evidence/claims.
2. `source.*` and `evidence.*` insert-only (grants + triggers); supersession via `supersedes`.
3. Labels separated from features; feature builders have no access to `source.label` except in training/eval jobs (enforced by separate repository interface).
4. `calibrated_probability` must be NULL unless a `model_artefact` of kind `CALIBRATOR` is referenced.
5. `er_link.match_score` and any `score` column must not be named or displayed as `probability`.
6. All queries in analytics take `as_of`; repositories apply `ingested_at <= as_of` centrally.
7. Pydantic models in `domain/` are the contract; DB models are mapped from them; JSONB fields have registered Pydantic schemas and a `schema_version`.
