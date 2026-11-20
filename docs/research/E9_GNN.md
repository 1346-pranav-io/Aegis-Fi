# E9: GNN versus the XGBoost baseline (D-20, D-32)

Status: RESULTS · Run `e9_20261112T195948Z` (`experiments/e9_gnn.py`; stored in `experiments/results/`) · IBM HI-Small, synthetic · Needs `uv sync --group gnn` · torch 2.11 CUDA, RTX 4060 · Numbers are indexed in `RESULTS_INDEX.json`.

## Design
D-24 windows by day: train Sep 1-6, validation Sep 7-8, test Sep 9-10 (same test window as E1; stress slice unused). A transaction on day *d* is scored with node embeddings from the graph of transactions **strictly before day *d* starts**, so the GNN never sees earlier transactions of its own day, which B1's point-in-time features do. This handicaps the GNN; the size of the handicap is not measured. Model: 2-layer GraphSAGE (mean, 64-d) on five node statistics, plus an MLP head on [source embedding, destination embedding, transaction features]. Four variants, 3 seeds each:

| Variant | Graph | Transaction features |
|---|---|---|
| G0 | no | all point-in-time features (same as B1) |
| G0i | no | transaction-intrinsic only |
| G1 | yes | transaction-intrinsic only |
| G2 | yes | all |

Early stopping on validation PR-AUC (max 100 epochs, patience 8). B1 reference numbers are read from the stored E1 run. No model artefact was saved or activated.

**Process disclosure.** A first complete run used a 60-epoch cap and had no G0i control; I saw its test numbers, then added G0i and raised the cap to 100 because several runs hit the cap, and re-ran. That run's file was deleted and replaced, so the reported run is not a pre-registered single shot. The first run's G2 test PR-AUC values (0.44-0.46) were in the same range.

## Results (test window, PR-AUC)
- B1 XGBoost (E1, 3 seeds): 0.4901 (sd 0.0102). B1 intrinsic-only (1 seed): 0.0535.
- G2 (GNN, all features): 0.4677 (sd 0.0109).
- G0 (no graph, all features): 0.4507 (sd 0.0267); one of its three seeds hit the epoch cap.
- G1 (GNN, intrinsic features): 0.0698 (sd 0.0055).
- G0i (no graph, intrinsic features): 0.0200 (sd 0.0015).

## Reading
- **The GNN did not beat the baseline.** G2 is 0.0224 below B1 on mean PR-AUC; with three seeds each and seed sds of about 0.01 this is not a decisive gap, but the D-20 condition (a GNN must beat the baselines to justify inclusion) is **not met**. Given the same-day handicap, this does not show that a graph model cannot match B1 either.
- **Graph embeddings help when the transaction features are weak.** With intrinsic features only, adding the graph raises PR-AUC from 0.0200 to 0.0698, far outside seed spread.
- **With all features the graph effect is not distinguishable from noise.** G2 minus G0 is 0.0170, smaller than G0's seed sd (0.0267).
- The no-graph neural head is weaker than XGBoost on the same features (G0 0.4507 vs 0.4901 for B1), so part of any G2-versus-B1 gap is model class, not graph.
- Model-class comparisons on intrinsic features are not meaningful (G1 0.0698 beats XGBoost intrinsic 0.0535, but G0i 0.0200 loses to it).

## Limits
One synthetic dataset and one split; 3 seeds; GPU scatter operations are not bit-deterministic, so seeds fix initialisation and sampling only. No hyper-parameter search for either family. Negative sampling and class weighting differ from B1's training. Not evidence about real laundering. The product keeps XGBoost (B1) as the detector.
