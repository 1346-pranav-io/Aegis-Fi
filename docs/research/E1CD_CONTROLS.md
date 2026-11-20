# E1c / E1d - Label-shuffle negative controls (measured)

Generated from `experiments/results/e1c_20261111T173101Z.json` and `e1d_20261111T173347Z.json`. Same split and XGBoost hyperparameters as E1.

## E1c (criteria fixed before the run): shuffle TRAIN+VALIDATION labels, score REAL test labels
Criteria: ROC-AUC in [0.47, 0.53] and PR-AUC <= 2 x base rate, for every permutation seed.

| perm seed | PR-AUC | ROC-AUC | base rate | meets criteria |
|---|---|---|---|---|
| 101 | 0.00097 | 0.407 | 0.00111 | NO |
| 202 | 0.00076 | 0.353 | 0.00111 | NO |
| 303 | 0.00156 | 0.506 | 0.00111 | yes |

Positive control (real labels): PR-AUC 0.4912, ROC-AUC 0.982. Real model vs permuted TEST labels: PR-AUC 0.00107, ROC-AUC 0.496.

**E1c did NOT meet its pre-registered criteria (1 of 3 seeds in band).** Not reclassified as a pass.

## E1d (follow-up, criteria fixed before the run)
### (1) Univariate test ROC-AUC of each feature against REAL test labels (features with |AUC - 0.5| > 0.1)
| feature | AUC |
|---|---|
| pair_prior | 0.103 |
| fmt | 0.814 |
| o_secs_since_out | 0.701 |
| d_in_all | 0.304 |
| log_amount | 0.692 |
| o_out_all | 0.308 |
| o_out_1h | 0.348 |
| o_out_24h | 0.364 |
| d_secs_since_in | 0.634 |

### (2) Full permutation (train, validation AND test labels permuted)
| perm seed | PR-AUC | ROC-AUC | base rate | meets criteria |
|---|---|---|---|---|
| 101 | 0.00113 | 0.499 | 0.00111 | yes |
| 202 | 0.00107 | 0.482 | 0.00111 | yes |
| 303 | 0.00115 | 0.491 | 0.00111 | yes |

## Interpretation (architect, not generated)
1. E1d supports the explanation for E1c's miss: nine features individually sit more than 0.1 from ROC 0.5 against the real test labels (payment format 0.81, pair history 0.10 which is 0.90 reversed, time since last send 0.70, log amount 0.69), so a model fit on noise can land far from 0.5 on the real labels without any leakage. The E1c ROC band was the wrong criterion. The explanation is consistent with the data but I did not test it further.
2. With features independent of labels by construction (full permutation), the pipeline gives chance-level results in all 3 seeds (ROC 0.48-0.50, PR-AUC at the base rate). This rules out gross errors in the feature -> model -> metric machinery; it does NOT test for future information in features (covered by the point-in-time property test) or for generator artefacts.
3. A side finding relevant to the generator-artefact caveat: a single feature, `pair_prior` (how often this exact sender->receiver pair occurred before), has AUC 0.90 when reversed, and payment format alone 0.81. So much of the signal can be obtained from whether a transaction is between a previously-unseen pair and from its payment format. Whether that reflects the synthetic generator (benign flows repeating between fixed counterparties) rather than real laundering behaviour has not been investigated.
4. What remains unverified about E1: sensitivity of the headline number to the feature choices above, and behaviour on other data.

