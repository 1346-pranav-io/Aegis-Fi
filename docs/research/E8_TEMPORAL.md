# E8: short-horizon temporal robustness (RQ5)

Status: RESULTS · Run `e8_20261112T082104Z` (code `experiments/e8_temporal.py`, post-analysis `e8_report.py`, stored in `experiments/results/`) · Decisions D-22, D-24, D-31 · Synthetic data (IBM HI-Small). No LLM involved. Every number below is indexed in `RESULTS_INDEX.json` and checked by `scripts/check_results_trace.py`.

## What was run
Rolling origin over day-level windows, Sep 1-10 only (the Sep 11-18 stress slice is not used). For each origin day *o* in 2..6: train on days before *o*, validate on day *o* (early stopping, isotonic fit), evaluate on every later day to Sep 10. Systems: B0 rules v1 (thresholds from unlabelled train features), B1 XGBoost (3 seeds), B1-intrinsic (transaction-intrinsic features only, 1 seed). 25 (origin, day) cells per system. Intervals are a row bootstrap (100 resamples).

Investigation-level proxy (not an agent investigation): the 370 pattern blocks. All 3209 block lines matched exactly one laundering transaction. *Block hit@1000* = share of blocks present that day with at least one member in the day's top 1000 scores. *Account AP* = average precision over origin accounts (max score per account, positive if any laundering transaction that day). Only in-block laundering (62% of laundering rows) enters the block metric.

## Results
**Level.** Mean PR-AUC over all cells: B1 0.4090, B1-intrinsic 0.0540, B0 0.0013. The base rate is about 0.001, so B0 is near chance (as in E1) and the intrinsic-only model is weak; B1's strength comes from the point-in-time behavioural features, as E1 found.

**Window noise.** Mean bootstrap interval width for B1 is 0.0838 PR-AUC; the standard deviation across B1 seeds (same origin and day) is 0.0234; the standard deviation of the per-day means is 0.0424. The bootstrap treats rows as independent although laundering rows cluster in blocks, so the true noise is larger than the interval.

**Horizon.** Mean B1 PR-AUC falls from 0.4435 at horizon 1 to 0.3060 at horizon 7. **This is not evidence of temporal degradation.** Horizon, origin and evaluation day are tied by horizon = day - origin, so an additive model cannot separate them, and in this design long horizons come from early origins that had only 2-3 days of training data. The data show this directly: validation PR-AUC itself rises with origin (0.2978 at origin 2, 0.5317 at origin 6) before any later day is seen. Within a single origin the training set is fixed, and the spread of B1 PR-AUC over later days is small: 0.0555 for origin 6 and 0.0829 for origin 2, which is comparable to the bootstrap interval width. A within-day estimate that removes day effects gives a slope of -0.0534 PR-AUC per horizon-day (residual SD 0.0716), but it inherits the training-size confound and should not be read as decay.

**A day effect is visible.** Sep 9 (day 8) scores lower than Sep 8 for every origin (B1 mean difference over origins is negative in all five). That points to day-level difficulty rather than horizon.

**Drift versus performance.** Spearman correlation between mean feature PSI and the change from validation PR-AUC is 0.5075; for score PSI it is -0.0545, and for horizon -0.0826 (n = 75). The positive feature-PSI sign (more drift, smaller drop) is the opposite of the expected direction and the variables are confounded with origin, so this is **not** evidence that PSI predicts degradation.

**Calibration.** ECE of the raw B1 score (a score, not a probability; trained with positive-class weighting) grows from 0.0438 at horizon 1 to 0.1117 at horizon 7, which says only that the raw scale moved. After isotonic calibration on the validation day, ECE stays below 0.001 at every horizon, but at a base rate near 0.1% ECE is dominated by negatives and says little. A more direct check: mean calibrated score divided by the true base rate is 0.9379 at horizon 1 and 0.7672 at horizon 7 (1.0 would be unbiased). Horizon 7 comes from a single origin (2 training days), so this too is confounded.

**Detection metrics versus investigation proxy (H5a).** All three systems are ordered identically by PR-AUC, block hit@1000 and account AP on every evaluation day. Within B1, the rank correlation of PR-AUC with block hit@1000 is 0.8876 and with account AP is 0.9704. System means of block hit@1000: B1 0.8073, B1-intrinsic 0.4887, B0 0.0049. So on this proxy the detection metric and the investigation-level metric agree. This is weak evidence on H5a: the proxy is computed from the same scores, so agreement is partly by construction, and no agent-based investigation was evaluated.

## What this supports and what it does not
- Supported: B1 beats the rules by a wide margin on every evaluated day; its day-to-day variation is of the size of the window noise; training size matters more than horizon in this design.
- Not supported / not claimed: any degradation over time, any drift-predicts-degradation claim, any statement about long-term drift (the dataset spans 18 days; D-22).
- To separate horizon from training size, a design with a fixed training length and a moving origin is needed (a sliding window instead of an expanding one). With 10 usable days that leaves very few cells; it is listed as a limitation, not run.
