# Phase 3 fixture experiments: G1 grounding, V1 verification, O1 orchestration

Run id `b7c544398b2e` · code commit `727dad7` · config `experiments/configs/p3_fixture_experiments.json`. Regenerate with `uv run python ../experiments/p3_fixture_experiments.py` from `backend/`.

**What these numbers are.** Properties of this pipeline and a scripted STUB LLM on 40 tiny synthetic fixture cases (4 typologies x 10 seeds). Faults are injected on purpose by the stub; 'unsupported' means 'the stub injected a fault into this claim'. Nothing here measures Claude, real investigators, or real data, and no result may be quoted as evidence for or against LLM verification. Intervals are Wilson 95% on counts within one seeded set (cases are not independent samples of any population).

**Naming.** EVALUATION_FRAMEWORK section 5 lists these as E2/E3/E4. `E2_GRAPH.md` and `E3_BEHAVIOUR.md` already use E2/E3 for other things, so they are named G1/V1/O1 here. MLflow is not used for these fixture runs (JSON + this file).

## G1 grounding (B2 accept-as-drafted vs B3 citation/context-checked)

| stub fault rate | B2 unsupported / accepted | B3 unsupported / stored | removed by grounding |
|---|---|---|---|
| 0.0 | 0/1010 = 0.000 [0.000, 0.004] | 0/1010 = 0.000 [0.000, 0.004] | - |
| 0.1 | 101/1007 = 0.100 [0.083, 0.120] | 72/978 = 0.074 [0.059, 0.092] | {'FAKE_EVIDENCE_ID': 29} |
| 0.25 | 235/979 = 0.240 [0.214, 0.268] | 161/905 = 0.178 [0.154, 0.204] | {'FAKE_EVIDENCE_ID': 74} |
| 0.5 | 425/924 = 0.460 [0.428, 0.492] | 293/792 = 0.370 [0.337, 0.404] | {'FAKE_EVIDENCE_ID': 132} |

Reading: grounding only removes claims that cite evidence outside their context (FAKE_EVIDENCE_ID). Wrong numbers, wrong entities and unsupported leaps cite real evidence and pass this layer by design; catching them is verification's job (V1).

## V1 verification (fault rate 0.3)

| variant | unsupported in findings | faulty claims kept out of findings | clean claims wrongly excluded |
|---|---|---|---|
| B3 no verification | 182/889 = 0.205 [0.179, 0.232] | 0/182 = 0.000 [0.000, 0.021] | 0/707 = 0.000 [0.000, 0.005] |
| B4 deterministic only | 0/697 = 0.000 [0.000, 0.005] | 182/182 = 1.000 [0.979, 1.000] | 10/707 = 0.014 [0.008, 0.026] |
| B4 deterministic + faithful verifier | 0/707 = 0.000 [0.000, 0.005] | 182/182 = 1.000 [0.979, 1.000] | 0/707 = 0.000 [0.000, 0.005] |
| B4 deterministic + rubber-stamp verifier | 34/741 = 0.046 [0.033, 0.063] | 148/182 = 0.813 [0.750, 0.863] | 0/707 = 0.000 [0.000, 0.005] |
| B4 deterministic + reject-all verifier | n/a (0) | 182/182 = 1.000 [0.979, 1.000] | 707/707 = 1.000 [0.995, 1.000] |

Faulty claims kept out of findings, by fault kind (stored claims only):

- B4 deterministic only: WRONG_NUMBER: 69/69 = 1.000 [0.947, 1.000], UNSUPPORTED_LEAP: 34/34 = 1.000 [0.898, 1.000], WRONG_ENTITY: 79/79 = 1.000 [0.954, 1.000]
- B4 deterministic + faithful verifier: WRONG_NUMBER: 69/69 = 1.000 [0.947, 1.000], UNSUPPORTED_LEAP: 34/34 = 1.000 [0.898, 1.000], WRONG_ENTITY: 79/79 = 1.000 [0.954, 1.000]
- B4 deterministic + rubber-stamp verifier: WRONG_NUMBER: 69/69 = 1.000 [0.947, 1.000], UNSUPPORTED_LEAP: 0/34 = 0.000 [0.000, 0.102], WRONG_ENTITY: 79/79 = 1.000 [0.954, 1.000]
- B4 deterministic + reject-all verifier: WRONG_NUMBER: 69/69 = 1.000 [0.947, 1.000], UNSUPPORTED_LEAP: 34/34 = 1.000 [0.898, 1.000], WRONG_ENTITY: 79/79 = 1.000 [0.954, 1.000]

Isolation-off ablation: NOT MEASURABLE with the stub: the stub verifier ignores context by construction; this ablation needs a real model.

## O1 orchestration (fixed_pipeline vs rule_adaptive under a step budget)

Quality target per case = the evidence type that explains it (cycle ring -> GRAPH_CYCLES with a cycle; fan-out -> GRAPH_FANIN_FANOUT with >=10 distinct destinations; structuring -> TXN_STRUCTURING_CHECK with >=3 hits; drift -> BEHAV_SELF_DEVIATION with status OK). Cost = agent steps (and stub-estimated tokens, characters/4).

| policy | max steps | mean steps used | required evidence found | report produced | run COMPLETE |
|---|---|---|---|---|---|
| fixed_pipeline | 3 | 3.00 | 40/40 = 1.000 [0.912, 1.000] | 0/40 = 0.000 [0.000, 0.088] | 0/40 = 0.000 [0.000, 0.088] |
| fixed_pipeline | 4 | 4.00 | 40/40 = 1.000 [0.912, 1.000] | 0/40 = 0.000 [0.000, 0.088] | 0/40 = 0.000 [0.000, 0.088] |
| fixed_pipeline | 5 | 5.00 | 40/40 = 1.000 [0.912, 1.000] | 0/40 = 0.000 [0.000, 0.088] | 0/40 = 0.000 [0.000, 0.088] |
| fixed_pipeline | 6 | 6.00 | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] |
| fixed_pipeline | 8 | 6.00 | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] |
| rule_adaptive | 3 | 2.00 | 0/40 = 0.000 [0.000, 0.088] | 40/40 = 1.000 [0.912, 1.000] | 0/40 = 0.000 [0.000, 0.088] |
| rule_adaptive | 4 | 4.00 | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] |
| rule_adaptive | 5 | 4.00 | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] |
| rule_adaptive | 6 | 4.00 | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] |
| rule_adaptive | 8 | 4.00 | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] | 40/40 = 1.000 [0.912, 1.000] |

Run statuses per row are in the JSON result file.
