# Phase 4 notes (entity resolution, contradictions, closure gating, perturbation generator)

Stream: Phase 4 (branch `claude/phase4-er-contradictions-7944e7`). Status: **stream finished, not merged** (commits on the branch; nothing pushed). Section 0 was written and committed BEFORE the experiments were run; sections 1-8 were written after.

## 0. Pre-registration of E5 and E6 (written before any run)

Rule: each experiment is run once with the configuration below; the first run is the reported run. If a bug is found afterwards the fix and a re-run are reported as such, with the reason. Thresholds and weights below were fixed before the runs; I had spot-checked the scorer on a handful of hand-written name pairs while writing unit tests (e.g. a one-digit-different name scores about 0.79, a one-letter typo about 0.96), so the 0.80 proposal threshold and the 0.85 digit factor were NOT chosen blind to those examples. No tuning against the experiment corpora has been done.

### E5 - entity resolution: proposal-only vs auto-merge under seeded name perturbation (RQ3)
* **Data.** 2,000 distinct entity names sampled with `random.Random(0)` from the sorted unique `Entity Name` values of `HI-Small_accounts.csv` (read-only, file SHA-256 recorded in the result). Per run, `eval/perturbation.generate_er_corpus` adds near-collision distractor entities (one digit changed, `NUMBER_CHANGE`) for 20 % of the names, and creates 2 mentions per entity. Each mention is perturbed with probability = *perturbation rate* using one identity-preserving kind (typo substitute/delete/insert/transpose, token swap/drop, suffix change, case, punctuation, whitespace, exact duplicate). Ground truth = the entity a mention was generated from.
* **Grid.** perturbation rate in {0.0, 0.1, 0.3, 0.5, 0.8} x seeds {1, 2, 3, 4, 5}; generator version 1.0.0; scorer `name-sim-1.0.0`; blocking `block-1.0.0`; proposal threshold 0.80; top-1 candidate per mention (a mention with no candidate >= 0.80 is *unresolved*).
* **Arms.** (A) *proposal-only* = the production policy: every fuzzy link is PROPOSED, so every claim built on it carries the "depends on unconfirmed link" flag. (B) *auto-merge at tau* for tau in {0.85, 0.90, 0.95}: a simulation only (production code and the database refuse it, tested): a fuzzy link with score >= tau is treated as CONFIRMED, the rest stay PROPOSED.
* **Downstream model.** One claim per resolved mention, built on its top-1 link. A claim is *wrong* if the link's entity is not the true entity. A claim is *flagged* if its link is not CONFIRMED.
* **Metrics.** Primary: **silent error propagation** = wrong claims that are NOT flagged, per mention (this is "claims built on wrong links" that nothing warns about). Secondary: wrong-link rate among resolved mentions; flagged share of claims (review burden); flagged-but-correct share; unresolved share; top-1 link accuracy; blocking recall (true entity in the candidate set). Mean and standard deviation over the 5 seeds; no significance test (the seeds share one name sample).
* **Expected, stated up front.** Proposal-only has silent propagation 0 *by construction* (nothing is auto-confirmed), so that number is not evidence of a good scorer; the informative quantities are the auto-merge arm's silent propagation as the perturbation rate grows, and what proposal-only costs in review burden. No claim about a reviewer: there are no real reviewers (Q-09), so reviewer accuracy is not simulated.
* **Not covered.** Identifier-based (EXACT_ID) linking (IBM names only, no identifiers in this experiment); alternative-hypothesis handling; multi-hop merge chains.
* **Limits to state wherever reported.** Names are synthetic ("Corporation #33520"); the perturbation catalogue is my own and may be easier or harder than natural errors; the digit-change distractors are constructed, not sampled from a natural near-duplicate rate (a 2,000-name sample of 166k entities has almost none naturally).

### E6 - contradiction detection: deterministic detectors vs a naive baseline on seeded contradictions (RQ3)
* **Data.** `eval/perturbation.generate_contradiction_corpus`, seeds {1, 2, 3, 4, 5}, 40 seeded contradictions and 40 consistent decoys per type (attribute conflict, amount mismatch, timeline impossible, claim vs evidence, ER conflict): 200 seeded + 200 decoys per seed. Decoys are look-alikes that must NOT be flagged (case/whitespace-only differences, multi-valued predicates, different time scope, equal amounts under formatting, different units, correct event order, other subject, VERIFIED/PARTIAL claims, same-entity or REJECTED competing links).
* **Systems.** (D) `analytics.contradictions.detect_all` with default config. (N) naive baseline: flag any two sources (evidence facts or claim structures) that state the same (subject, predicate) with different raw string values; type guessed AMOUNT_MISMATCH if both parse as numbers else ATTRIBUTE_CONFLICT; it reads no events, no verification results, no ER links, ignores time scope, unit and cardinality. (L) *LLM-only detector*: **not run** - no live Claude call is permitted before Q-02 is confirmed.
* **Metrics.** Strict match = same type AND same member set. Precision = matched flags / all flags; recall = matched seeded / seeded; F1; overall and per type; mean and standard deviation over seeds. Also a loose match (member set only) for the baseline.
* **Expected, stated up front.** D was written by the same author as the generator and against the same content conventions (declared in `analytics/contradictions.py`), so recall near 1.0 on seeded data is largely by construction. This run shows the detectors implement their specification and that the specified decoy behaviour holds; it says NOTHING about recall on natural contradictions, whose prevalence and forms are unknown. The baseline comparison is informative only to the extent the decoy mix is realistic, and the mix is my choice.

---

## 1. What exists
| Area | Files |
|---|---|
| Perturbation generator (versioned `1.0.0`) | `eval/perturbation.py`: 12 name perturbation kinds (11 identity-preserving + `NUMBER_CHANGE` = a different entity), `generate_er_corpus` (entities, near-collision distractors, mentions with ground truth), `generate_contradiction_corpus` (5 seeded contradiction types + consistent decoys, with ground truth), `to_detection_input` |
| ER pipeline | `entities/similarity.py` (normaliser `norm-1.0.0`, scorer `name-sim-1.0.0`, stdlib only), `entities/blocking.py` (`block-1.0.0`, incl. one-digit-deletion keys), `entities/pipeline.py` (pure: exact-identifier confirm only when the identifier names ONE entity, everything else PROPOSED, top-k kept), `entities/repo.py` (mentions, entities, link history, confirm/reject/reopen/supersede, merge request, merge, unmerge, root resolution, `PgERLinkReader`) |
| ER migration | `0020_entity_resolution.sql`: `derived.entity_mention`, `resolved_entity`, `er_link` (append-only history, `er_link_current` view), `entity_merge` (append-only, `entity_merge_current` view), allow-list `er_exact_identifier_type`; triggers enforce the rules below; all audited |
| Contradiction detectors | `analytics/contradictions.py`: ATTRIBUTE_CONFLICT, AMOUNT_MISMATCH, TIMELINE_IMPOSSIBLE, CLAIM_VS_EVIDENCE, ER_CONFLICT; pure, deterministic, order-independent; SOURCE_DISAGREEMENT deliberately NOT implemented (see section 4) |
| Contradiction capability | `capabilities/contradiction/capability.py` (implements `Capability`; permissions READ_EVIDENCE, WRITE_PROPOSAL, CALL_LLM; read-only tools; explanation proposals are validated and discarded unless they cite the finding's own evidence), `service.py` (idempotent recording, human-only status changes, open-contradiction listing) |
| Closure gating | `0021_contradiction_fingerprint.sql` (unique fingerprint index), `0022_contradiction_closure_gate.sql` (replaces `workflow.case_guard`: the 0008 body verbatim plus "no CLOSED while an OPEN HIGH contradiction exists") |
| Experiments | `experiments/e5_er.py`, `experiments/e6_contradictions.py` (results in git-ignored `experiments/results/`) |
| Test-only toggle | `tests/integration/conftest.py`: `AEGIS_TEST_PROVISIONAL_APPROVALS=1` builds a TEMP copy of the migrations with provisional approval entries for files not yet in `APPROVALS.json` (nothing is written to the repo; once the owner approves, the real entries are used) |

Rules enforced in the database (migration 0020), each with a test:
* a link starts PROPOSED; it may start CONFIRMED only as `EXACT_ID` whose identifier type is allow-listed and whose mention/entity identifier values are equal (score 1), or as a human `MANUAL` link; a fuzzy link cannot be confirmed by a system actor, not even by raw SQL;
* confirming a non-exact link, rejecting and re-opening need a HUMAN actor (the id is caller-asserted until the API phase; the DB login is recorded by trigger); the facts of a link (mention, entity, method, score, features, scorer version) never change, only its status; SUPERSEDED is terminal;
* `match_score` is a similarity in [0,1]; there is no probability column;
* a merge needs an APPROVED `ENTITY_MERGE` decision for exactly `from->into`, single-use; targets must be roots, so no cycles; unmerge appends a row and restores the prior state; every link change and merge/unmerge writes an audit row; all new tables are insert-only (UPDATE/DELETE/TRUNCATE refused).

## 2. Tests added (measured)
Before this stream: 134 tests (60 unit + 74 integration). After: **101 unit + 93 integration (1 skipped, pre-existing) = 194**; so +41 unit and +19 integration.
* unit: `test_perturbation.py` 11, `test_er_pipeline.py` 12, `test_contradiction_detectors.py` 10, `test_contradiction_capability.py` 8.
* integration: `test_entity_resolution.py` 10 (incl. T-INV-17), `test_contradiction_workflow.py` 9 (incl. T-INV-18).
* `uv run python scripts/dev.py check` (ruff, mypy strict, import-linter 5 contracts, unit tests): green. Integration tests: 92 passed, 1 skipped, run with `AEGIS_TEST_PROVISIONAL_APPROVALS=1` against scratch databases on the local Docker Postgres (never the `aegis` database itself: every test creates and drops its own `aegis_test_*` database). Before the owner approved 0020-0022 the suite needed that variable (the runner refused unapproved migrations; confirmed). After approval it runs without it: 92 passed, 1 skipped.
* Regression of the earlier guards: the whole existing integration suite (`test_cases_workflow`, `test_approval_hardening`, `test_security_regressions`, ...) passes against the replaced `case_guard`, plus `test_earlier_guards_still_hold_with_the_new_gate`.

## 3. Measured results
Everything below is read from `experiments/results/*.json` written by the scripts in `experiments/`. Means over seeds 1-5 (standard deviations are in the JSON). Accounts file SHA-256 `786808526e33cfc441212dd6fccda7edfc24172149bed59c6ef59b186836b014`; 2,418 entities (2,000 sampled + 418 distractors), 4,836 mentions per run.

### E5 as pre-registered (complete registry, proposal threshold 0.80)
| perturbation rate | changed mentions (seeds 1-5) | blocking recall | unresolved | top-1 link accuracy | wrong links among resolved |
|---|---|---|---|---|---|
| 0.0 | 0 in all | 1.000 | 0.000 | 1.000 | 0.000 |
| 0.1 | 413-463 | 1.000 | 0.008 | 0.992 | 0.000 |
| 0.3 | 1,228-1,312 | 1.000 | 0.024 | 0.976 | 0.000 |
| 0.5 | 2,075-2,152 | 1.000 | 0.039 | 0.961 | 0.000 |
| 0.8 | 3,294-3,455 | 1.000 | 0.063 | 0.937 | 0.000 |

Silent error propagation was **0.0000 in every arm and every cell** (proposal-only and auto-merge at 0.85, 0.90, 0.95). Review burden, share of resolved claims flagged: proposal-only 1.000 in every cell; auto-merge at 0.85: 0.000 in all cells; at 0.90: 0.000 / 0.000 / 0.001 / 0.001 / 0.002; at 0.95: 0.000 / 0.020 / 0.063 / 0.104 / 0.169 (rates 0.0 / 0.1 / 0.3 / 0.5 / 0.8).
**Result: the pre-registered expectation (auto-merge shows non-zero silent propagation that grows with the perturbation rate) was NOT observed.** Reasons I can identify: (1) the registry contained every true entity, so a mention's own canonical name was always the nearest candidate; (2) the scorer multiplies by 0.85 when numeric tokens differ, so a near-collision name can never score above 0.85 and so never reaches an auto-merge threshold of 0.85 or higher. This is a limit of the experimental design (complete registry) as much as a property of the scorer.

### E5 post-hoc variants (NOT pre-registered; exploratory; run after seeing the null result above)
Variant: the registry omits the near-collision distractor entities, so a mention of a distractor has no correct entity and any link for it is wrong (a new entity wrongly absorbed into an existing one).
* Proposal threshold 0.80 (the shipped value): wrong links among resolved mentions 0.073 / 0.070 / 0.066 / 0.060 / 0.051 across rates 0.0 / 0.1 / 0.3 / 0.5 / 0.8; silent error propagation still 0.0000 for auto-merge at 0.85, 0.90 and 0.95 (every wrong link scores below 0.85 because of the digit factor).
* Proposal threshold lowered to 0.65 with lower auto-merge thresholds, rates 0.0 / 0.3 / 0.8: silent error propagation (per mention) for auto-merge at 0.70: 0.165 / 0.161 / 0.154; at 0.75: 0.165 / 0.152 / 0.129; at 0.80: 0.066 / 0.057 / 0.042; proposal-only 0.000 in all cells. Wrong links among resolved: 0.165 / 0.162 / 0.157.

Reading: proposal-only removes silent propagation by construction. Whether auto-merge would be unsafe depends entirely on the threshold sitting above the near-collision score, and in the shipped scorer that margin is thin (a one-digit-different name scores about 0.79 against a 0.80 proposal threshold). That is a reason to keep fuzzy links proposal-only (as built), not evidence that the scorer is good. These variants were chosen after seeing a null result and must be presented as exploratory.

### E6 (pre-registered; 5 seeds x (200 seeded + 200 decoys))
| system | overall precision | overall recall | overall F1 |
|---|---|---|---|
| deterministic detectors | 1.000 | 1.000 | 1.000 |
| naive baseline (strict match) | 0.500 | 0.400 | 0.444 |
| naive baseline (member set only) | 0.500 | 0.400 | 0.444 |

Per type, detectors: precision 1.000 and recall 1.000 for all five types. Baseline: ATTRIBUTE_CONFLICT precision 0.500 / recall 1.000; AMOUNT_MISMATCH 0.500 / 1.000; TIMELINE_IMPOSSIBLE, CLAIM_VS_EVIDENCE and ER_CONFLICT 0 / 0 (it reads none of the inputs those need). The standard deviation across seeds is 0.000 because the generator plants a fixed number of each form.
**How to read this:** the detectors and the generator share an author and the content conventions, so 1.000 is largely by construction. It shows the detectors implement their specification and ignore the specified decoy forms. It says nothing about recall or precision on natural contradictions. The LLM-only arm was not run (no live Claude call before Q-02). The baseline's 0.5 precision comes from the decoy forms I chose (formatting-equal amounts, different units, different time scope, case differences, multi-valued predicates).

## 4. Deviations from specs / decisions to record
* **All merges need an approval**, not only "non-exact" ones (stricter than D-09d's wording): exact-identifier agreement is handled at link level (the mention is linked to the entity that holds the identifier), so no merge is needed for it.
* `er_link` has no `calibrated_probability` column (DATA_SCHEMA lists one): D-06 allows it only with a calibration artefact and none exists; adding it later is a migration.
* `resolved_entity` is insert-only; DATA_SCHEMA marks it mutable (`status`, `merged_into`). Merge state lives in `entity_merge` history instead, so nothing is overwritten.
* Mentions/entities have types ORGANISATION/PERSON/UNKNOWN only; no `case_subject` table (not asked, not built).
* **SOURCE_DISAGREEMENT not implemented**: the frozen evidence contract has no notion of a source system; defining one would mean inventing a field.
* The IBM accounts file was **not ingested** (no adapter or table added): the ER experiments need only names (read-only CSV), and a new insert-only source table is a larger approval surface than the task required. E5 therefore ran from the CSV, not from the database.
* The ER identifier allow-list (`account_number`, `entity_id`) is my choice, not from a spec.
* The contradiction status service lives in `capabilities/contradiction/service.py` because Phase 4 does not own `services/`; it should move there at reconciliation.
* Detectors read free-form `evidence.content` through conventions I declared (module docstring of `analytics/contradictions.py`); Phase 3 must emit the same shapes or the detectors find nothing (silently).
* **Proposed `decisions.md` entries (I did not edit that file):** (a) ER policy: exact-identifier-only auto-confirm, all merges approved, merge/unmerge as history; (b) closure gate on OPEN HIGH contradictions (EVIDENCE_MODEL says "acknowledged by an approver"; I built "a human moved it to EXPLAINED / RESOLVED_BY_HUMAN / DISMISSED", as the task specified; neither checks the approver role, and the human actor kind is caller-asserted); (c) the declared scorer and blocking versions.

## 5. Migrations (APPROVED by the owner in chat on 2026-11-12, after this section was first written; entries added to `APPROVALS.json`, commit `da7f705`)
All three match the runner's protected pattern, so each has an entry with its SHA-256 (any later edit invalidates it). Not yet applied to the `aegis` dev database. The security review of 0022 is still requested.
1. `0020_entity_resolution.sql` - new ER tables, triggers (definer functions, audit), grants to `aegis_app`.
2. `0021_contradiction_fingerprint.sql` - unique index on `evidence.contradiction (case_id, detector->>'fingerprint')` (adds a constraint to a table of the frozen contract; no column change).
3. `0022_contradiction_closure_gate.sql` - replaces `workflow.case_guard` (SECURITY DEFINER). Diff against 0008: only the `open_high` block. **Security review requested** (it reads `evidence.contradiction_current` as the function owner).
Note on line endings: on this machine git converts LF to CRLF; the runner hashes the text it reads, so compute the approval hash from the file as it exists in the checkout that will run the migrations.

## 6. Requests for changes to frozen files (not made)
* R-1 `common/ids.py`: add prefix `erm` (merge ids; for now generated locally in `entities/repo.py`).
* R-2 `domain/entities.py`: `ERLinkReader` has no method for merges or for flagged claims; I added `unconfirmed_dependencies` accepting merge ids and `flagged_claims()` on the concrete `PgERLinkReader` only. If Phase 3 wants them on the protocol, that is an architect change.
* R-3 `tests/integration/conftest.py` (shared, not on the frozen list): I added the `AEGIS_TEST_PROVISIONAL_APPROVALS` toggle (default off). Phase 3 needs the same for 0010-0019; expect a trivial merge conflict if they wrote their own.
* R-4 With Phase 3, define the evidence content shapes the detectors read (`facts`, `events`, ER_LINK_EVIDENCE `{link_id, mention_id, entity_id, merge_ids?}`) and the shapes of the three read tools (see the `capability.py` docstring).

## 7. Open risks
* Caller-asserted human identity (`actor_kind`, `decided_by`): same limitation as Phases 1-2; ER and contradiction decisions are only as trustworthy as that assertion until the API phase.
* The closure gate does not check that the human who dismisses a HIGH contradiction holds an approver role, and a note is required by the service, not by the database.
* Dependency flagging reads `claim.depends_on_er_links` plus cited `ER_LINK_EVIDENCE.content.link_id/merge_ids`; evidence derived transitively from ER evidence is not followed (the provenance walk is Phase 3).
* Detector coverage depends on the content conventions; a mismatch fails silent (no findings), not loud.
* Scorer margin: near-collision names score about 0.79 against the 0.80 threshold; the scorer was exercised only on synthetic IBM-style names and on perturbations I designed.
* Blocking ignores blocks above 200 members; fine for the 2,418-entity experiment, untested at 166k entities.
* The seeded decoy mix, perturbation catalogue and detector rules come from one author; E5/E6 numbers are not evidence about natural data.

## 8. Reconciliation integration points
1. Swap Phase 3's fake `ERLinkReader` for `entities.repo.PgERLinkReader(conn)`; Phase 3 stores `unconfirmed_dependencies(...)` in `claim.depends_on_er_links` and writes ER_LINK_EVIDENCE content with `link_id` (and `merge_ids` when a merge was relied on).
2. Register `capabilities.contradiction.capability.ContradictionCapability` in the runtime registry; the real `ToolGateway` must serve `get_evidence`, `get_claims` (with latest `verification`) and `get_er_links` (+ optional `entity_roots` from `repo.root_entity`) for the `detect_contradictions` task, enforcing `as_of` (the capability also filters).
3. Persist `AgentResult.contradictions` through `capabilities.contradiction.service.record_contradiction` (idempotent); status changes only through `set_status` with a human actor.
4. Wire `ExplanationProposer` to the gateway; the capability attaches only proposals that cite the finding's own evidence, and only when the task budget allows LLM use.
5. C6 (rejected link -> re-verification): `PgERLinkReader.flagged_claims(case_id)` is the query Phase 3 polls or reacts to; Phase 4 queues nothing.
6. Apply 0020-0022 after 0010-0019 on a fresh database and re-run the security review; re-run the import contracts (`capabilities.*` independence now has a submodule).
