# Evidence Model

Status: DRAFT · Version 0.1 · Decisions: D-06, D-07, D-08, D-17. Supports research contributions 2 and 3.

## 1. Concepts
- **Source record** — immutable raw financial/reference record (`source.*`).
- **Evidence item** — an immutable observation *derived* from source records or other evidence by a named producer (a deterministic tool, a model, or an LLM extraction).
- **Claim** — an assertion about the case. Not itself evidence; it must be *supported by* evidence.
- **Verification result** — independent judgement on claim ↔ cited evidence.
- **Contradiction** — recorded conflict between evidence and/or claims.

```
source record ─▶ evidence (produced by tool/model)  ─▶ evidence (derived)…
                          │
                          ▼ cited by
                        claim ◀── verification_result
                          │
                          ▼ used by
                       report (findings only from VERIFIED claims)
```

## 2. Evidence item
| Field | Description |
|---|---|
| `evidence_id` | `ev_<ULID>` |
| `case_id`, `run_id`, `step_id` | where produced |
| `evidence_type` | enumerated (see §3) |
| `content` | JSONB validated by the Pydantic schema for `evidence_type`; includes `schema_version` |
| `summary_text` | short human-readable rendering (deterministic template for non-LLM types) |
| `producer` | `{kind: TOOL|MODEL|LLM|HUMAN, name, version, params_hash}` |
| `as_of` | data cut-off used |
| `window` | time window analysed |
| `inputs` | list of `{ref_type: SOURCE|EVIDENCE|DOCUMENT|REFERENCE, ref_id, ref_hash}` (derivation edges, table `evidence_input`) |
| `content_hash` | SHA-256 of canonical JSON of (type, content, producer, as_of, window, inputs) |
| `created_at` | UTC |
| `supersedes` | previous evidence id, if recomputed |
| `quality` | `{completeness: FULL|TRUNCATED|INSUFFICIENT, notes}` |
| `scores` | optional `{score, calibrated_probability?, calibration_artefact_id?}` |

Invariants: immutable; `inputs` non-empty except `HUMAN` entries; every `SOURCE` input hash equals the stored source `content_hash` at creation; deterministic producers are re-computable (re-running with the same inputs yields the same `content_hash`).

## 3. Evidence types (initial catalogue)
`SIGNAL` · `TXN_AGGREGATE` · `TXN_VELOCITY` · `TXN_STRUCTURING_CHECK` · `TXN_RAPID_MOVEMENT` · `TXN_COUNTERPARTY_SUMMARY` · `GRAPH_NEIGHBOURHOOD` · `GRAPH_CYCLES` · `GRAPH_FANIN_FANOUT` · `GRAPH_FLOW_THROUGH` · `GRAPH_COMMUNITY` · `GRAPH_CENTRALITY` · `BEHAV_SELF_DEVIATION` · `BEHAV_PEER_DEVIATION` · `BEHAV_MODEL_SCORE` · `REF_LIST_MATCH` · `DOC_EXTRACTION` · `ER_LINK_EVIDENCE` · `HUMAN_NOTE`.
Adding a type requires: Pydantic schema, version, producer, test fixture, and an entry here.

## 4. Claim
| Field | Description |
|---|---|
| `claim_id` | `clm_<ULID>` |
| `case_id`, `run_id`, `author` | `{kind: AGENT|HUMAN, name, version}` |
| `structure` | `{subject_ref, predicate, object_value, unit?, time_scope}` — structured to allow deterministic checks |
| `text` | natural-language rendering |
| `claim_kind` | `FACT_OBSERVATION | AGGREGATE | INFERENCE | TYPOLOGY_HYPOTHESIS | RECOMMENDATION` |
| `cited_evidence` | list of `{evidence_id, support_role}`; `support_role ∈ SUPPORTS, PARTIALLY_SUPPORTS, CONTEXT, CONTRADICTS` |
| `self_reported_confidence` | `LOW|MEDIUM|HIGH|NONE`, **labelled self-reported; not a probability** |
| `depends_on_er_links` | ER link ids whose status is not `CONFIRMED` → claim flagged "depends on unconfirmed link" |
| `status` | `DRAFT → VERIFIED|PARTIALLY_SUPPORTED|UNSUPPORTED|CONTRADICTED|UNVERIFIABLE` (derived from latest verification) |
| `human_review` | `NONE | ACCEPTED | REJECTED | EDITED(new claim id)` |

Rules: ≥1 cited evidence id (schema-enforced); cited ids must be in the author's context window; an `INFERENCE` or `TYPOLOGY_HYPOTHESIS` must cite the observations it infers from; claims are immutable (edits create new claims linked `supersedes`).

## 5. Verification result
`verification_id`, `claim_id`, `status`, `checks[]` (`{check_id, kind: DETERMINISTIC|SEMANTIC, outcome: PASS|FAIL|NA, detail}`), `rationale`, `verifier` (`{name, version, model_id?, prompt_version?}`), `inputs_seen` (hash of exactly what the verifier was shown — enables the isolation test), `created_at`.

Deterministic checks (initial): `EVIDENCE_EXISTS`, `HASH_INTACT`, `ENTITY_PRESENT_IN_EVIDENCE`, `AMOUNT_MATCHES`, `DATE_IN_WINDOW`, `AS_OF_RESPECTED`, `CITATION_ROLE_CONSISTENT`.
Semantic check: `ENTAILMENT` — does the evidence content entail the claim (with the claim's precision)? Verdict + quoted evidence span required in rationale.

Status derivation:
| Condition | Status |
|---|---|
| any deterministic FAIL on a factual element | `UNSUPPORTED` (or `CONTRADICTED` if evidence states the opposite) |
| all deterministic PASS and semantic entails fully | `VERIFIED` |
| entailment only for part of claim | `PARTIALLY_SUPPORTED` |
| missing/altered evidence, verifier unavailable | `UNVERIFIABLE` |
| evidence directly negates claim | `CONTRADICTED` (also opens a Contradiction) |

## 6. Confidence handling (D-06)
Three distinct quantities, never merged:
1. **Model score** — raw, uncalibrated; named `score`.
2. **Calibrated probability** — exists only with a registered calibrator artefact and a measured calibration error on a later temporal window; displayed with the artefact id.
3. **Self-reported LLM confidence** — ordinal, labelled; evaluated against verification outcomes (research question), not trusted.
ER `match_score` is a similarity score. Aggregating or multiplying these into a "case confidence" is **not done** in v1; instead the case view shows counts and statuses of claims (verified / partial / unsupported / contradicted) and dependencies on unconfirmed links.

## 7. Entity resolution uncertainty (contribution 3)
- Mentions → entity links are records with `status`. Only `EXACT_ID` (verified identifier equality) may be auto-`CONFIRMED`; all others `PROPOSED` until human action (D-09d).
- Evidence and claims that rely on a resolved entity store the **link ids used** (`ER_LINK_EVIDENCE`); if a link is later rejected/split, dependent claims are re-flagged (a new verification is queued) — originals remain unchanged.
- Alternative-hypothesis support: when two candidate entities exist for a mention, both are retained with scores; claims cite the one used and the report lists unresolved alternatives.

## 8. Contradictions
`contradiction`: `contradiction_id`, `case_id`, `type` (`ATTRIBUTE_CONFLICT | TIMELINE_IMPOSSIBLE | AMOUNT_MISMATCH | CLAIM_VS_EVIDENCE | ER_CONFLICT | SOURCE_DISAGREEMENT`), `severity` (`LOW|MEDIUM|HIGH`, rule-defined), `detector` (name+version), `status` (`OPEN|EXPLAINED|RESOLVED_BY_HUMAN|DISMISSED`), `explanation_proposal` (nullable, with cited evidence), `resolved_by`, `resolution_note`.
`contradiction_member`: `{contradiction_id, member_type: EVIDENCE|CLAIM, member_id, role}` (≥ 2 members).
Behaviour: open HIGH contradictions block case closure until acknowledged by an approver; reports must list open contradictions; resolution never deletes either side.

## 9. Provenance walk (basis of SAC-01)
`walk(claim_id)` returns the DAG claim → evidence → (evidence…) → source records, verifying hashes at each node. An automated test runs `walk` over all claims in fixture runs and asserts: no dangling reference, no hash mismatch, all leaves are `SOURCE`/`DOCUMENT`/`REFERENCE`/`HUMAN`.

## 10. Lifecycle summary
```
create evidence (immutable) → agent drafts claim (cites ids) → schema/citation check
 → verification (det. then semantic) → contradiction scan → human review (feedback)
 → report (VERIFIED claims → findings; others → labelled sections)
 → corrections = new items that supersede; nothing is overwritten
```

## 11. Open points
- Granularity of semantic verification for aggregated claims (chunking evidence) — Phase 3 design spike.
- Whether `HUMAN_NOTE` evidence may be cited as support for suspicion findings (policy decision for owner; default: contextual only).
