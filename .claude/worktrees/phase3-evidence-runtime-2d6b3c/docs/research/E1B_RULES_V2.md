# E1b - Rule pack v2 vs v1 vs B1 (measured)

Generated from `experiments/results/e1b_20261111T172642Z.json` (v2, MLflow run `2c664a8ecc21481db9bbc91064cdab5c`) and `e1_20261111T170703Z.json` (v1, B1). Procedure D-26, fixed before the run. Test and stress evaluated once. Same split hash `465b7276ab89a015...` as E1 (equal to E1: True).

## Test window (Sep 9-10)
| System | PR-AUC | ROC-AUC | recall@1000 | recall@5000 | precision@100 | distinct score values |
|---|---|---|---|---|---|---|
| B0 v1 | 0.00122 | 0.536 | 0.000 | 0.004 | 0.000 | n/a |
| B0 v2 | 0.00376 | 0.759 | 0.003 | 0.020 | 0.000 | 33 |
| B1 XGBoost (mean of 3 seeds) | 0.49011 | 0.980 | 0.471 | 0.727 | 0.973 | n/a |

Base rate on test: 0.00111. Validation (v2): PR-AUC 0.00373, ROC-AUC 0.792. Stress (v2, not comparable): PR-AUC 0.714, ROC-AUC 0.591 at base rate 0.591.

## Fitted v2 rules (train labels only)
| Rule | enabled | threshold | weight | test fires | test precision | test recall |
|---|---|---|---|---|---|---|
| FAN_OUT_24H | yes | 18 | 0.457 | 104,289 | 0.0012 | 0.131 |
| FAN_IN_24H | yes | 7 | 0.471 | 62,080 | 0.0014 | 0.094 |
| VELOCITY_1H | yes | 172 | 0.656 | 47,619 | 0.0016 | 0.078 |
| RAPID_FLOW_THROUGH | no (no threshold met min firings and lift > 1) | | | | | |
| REVERSE_FLOW | yes | 5 | 0.431 | 15,956 | 0.0021 | 0.035 |
| NEW_COUNTERPARTY_LARGE | yes | 0.102 | 1.099 | 21,096 | 0.0121 | 0.268 |
| FRESH_DEST | yes | 6 | 0.045 | 78,420 | 0.0046 | 0.381 |
| FRESH_ORIGIN | yes | 2 | 0.424 | 92,134 | 0.0035 | 0.340 |

## Interpretation (architect, not generated)
1. v2 is better than v1 (test PR-AUC about 3.4 times the base rate vs 1.1 times; ROC-AUC 0.76 vs 0.54) but still weak in absolute terms: about 130 times lower PR-AUC than B1 and recall@5000 of about 2%. Simple threshold rules over these features do not isolate laundering in this dataset.
2. The score takes only about 30 distinct values, so top-k metrics are dominated by ties broken by row order; recall@k and precision@k for v2 are therefore a weak guide. PR-AUC and ROC-AUC treat ties as tied.
3. Several rules carry modest lift individually (NEW_COUNTERPARTY_LARGE precision 0.012 vs base 0.0011) but FRESH_DEST and FRESH_ORIGIN fire on a large share of rows, so they add recall at low precision. The grid picked low NEW_COUNTERPARTY_LARGE threshold (0.10), which makes that rule mean roughly 'new counterparty' rather than 'large amount'; the name overstates what it detects. RAPID_FLOW_THROUGH was disabled (no qualifying threshold).
4. The validation-to-test change is small here (AP 0.00373 to 0.00376) because nothing was selected on validation; this is the cleaner of the two short-horizon comparisons, but with simple rules and two-day windows it still says nothing about drift beyond this dataset.
5. Disclosure per D-26: v1 test per-rule statistics had been seen before v2 was designed.

