# E3 - Behaviour analytics on pattern blocks (measured, oracle-windowed)

Generated from `experiments/results/e3_behaviour_20261111T175549Z.json`. Unit = account. Block accounts: every distinct account in the 370 blocks (first block wins if shared); controls: the same number of other accounts active in the same test window, sampled uniformly (seed 11). Test window = [first block txn, last block txn + 1 day); baseline = [data start, first block txn). Window and membership come from the blocks, so this is a description of the module, not a detector evaluation. Accounts are clustered within blocks, so no intervals are given.

## History sufficiency
| Group | accounts | INSUFFICIENT_HISTORY | with enough history |
|---|---|---|---|
| block | 3,170 | 57.4% | 1,352 |
| control | 3,170 | 51.4% | 1,541 |

## Medians among accounts with enough history
| Measure | block | control |
|---|---|---|
| z_out_count | -0.455 | -0.562 |
| z_in_count | -0.722 | -1.106 |
| new_counterparty_share | 0.400 | 0.000 |
| z_log_amount_mean | 0.342 | -0.276 |
| peer_percentile_of_test_count | 0.850 | 0.682 |

## Separation, block vs control (ROC-AUC; accounts with enough history)
| Measure | AUC | n |
|---|---|---|
| z_out_count | 0.562 | 2,893 |
| z_in_count | 0.575 | 2,893 |
| new_counterparty_share | 0.928 | 2,892 |
| z_log_amount_mean | 0.643 | 2,893 |
| peer_percentile_of_test_count | 0.654 | 2,893 |

Peer percentile over all accounts including insufficient history: AUC 0.630 (n=6,340).

## Interpretation (architect, not generated)
1. **About half of all accounts have too little history to profile** (block 57%, control 51%), because the data spans 18 days and many accounts transact rarely. The hypothesis that laundering accounts are mostly brand new is only weakly supported (6 points higher), so insufficient history is a general coverage limit of this module on this data, not a laundering signature.
2. **New-counterparty share is the one strong measure (AUC 0.93; median 0.40 vs 0.00).** This matches the transaction-level finding that "never-seen sender-receiver pair" separates laundering (E1CD_CONTROLS). It likely reflects the synthetic generator's repeating benign counterparties. It is one signal seen twice, not two independent confirmations.
3. **Volume deviation (z of outgoing/incoming counts) barely separates (AUC 0.56-0.57)**; amount shift 0.64; peer percentile of activity 0.65 (0.63 over all accounts).
4. **Limits:** AUCs apply only to the 46% of accounts with enough history, a selected subset; windows are oracle-defined; amounts are not currency-converted; median z values are negative for both groups, which reflects the simple Poisson expectation, not a bug I have verified.
5. **Not tested:** whether behaviour measures add predictive value to B1 (they are account-window measures, B1 is point-in-time per transaction).
