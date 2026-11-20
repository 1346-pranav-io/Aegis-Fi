> **Naming note (reconciliation):** "E2" here is the Phase 2 graph benchmark. It is NOT experiment E2 (grounding) of the evaluation matrix in EVALUATION_FRAMEWORK section 5. Likewise "E3" in E3_BEHAVIOUR.md. The matrix experiments of Phase 3 are named G1/V1/O1 (docs/research/P3_FIXTURE_EXPERIMENTS.md).

# E2 - Graph analytics: latency, size limits, pattern-block validation (measured)

Generated from `experiments/results/e2_graph_20261111T174128Z.json`. Hardware: 20 logical cores, 16 GB. Data: IBM HI-Small, 5,078,345 edges, 515,088 accounts. EdgeIndex build over all edges: 1.1 s.

## Latency per ego subgraph (2 hops, 24 h trailing window, all metrics incl. cycles/PageRank/Louvain)

Random centres (n=1500; 128 of them with degree > 10,000): p50 1.4 ms, p95 107 ms, p99 160 ms, max 566 ms. Ego edges p50 17, p95 4656. **Truncated: 10.5%** of subgraphs hit a size limit.

30 highest-degree accounts (max degree 169,756): p50 37 ms, max 136 ms, truncated 17%.

NFR-PERF-02 target was 30 s at p95 per case for all deterministic steps; the graph step alone is far inside it here, but the other agents were not measured per case.

## Whole-graph NetworkX build (why windowed ego graphs are used)
| edges | build s | peak MB | nodes |
|---|---|---|---|
| 100,000 | 1.2 | 78 | 105,585 |
| 500,000 | 5.5 | 252 | 327,325 |
| 1,500,000 | 17.4 | 465 | 474,156 |

Measured up to 1.5M edges only; the full 5.08M-edge graph was not built, and metrics (cycles, communities) were not run on whole graphs.

## Pattern-block validation (ORACLE-WINDOWED: centre and window come from the block itself; not a detector evaluation)
| Block type | n | cycle through centre found | median distinct out | median distinct in | median flow-through (1h) | truncated |
|---|---|---|---|---|---|---|
| CYCLE | 54 | 57.4% | 2.0 | 2.0 | 0.00 | 9% |
| FAN-OUT | 48 | 29.2% | 7.5 | 2.0 | 0.00 | 19% |
| FAN-IN | 40 | 30.0% | 1.0 | 9.5 | 0.00 | 12% |
| GATHER-SCATTER | 51 | 35.3% | 2.0 | 2.0 | 0.00 | 18% |
| SCATTER-GATHER | 44 | 31.8% | 7.0 | 1.0 | 0.00 | 20% |
| BIPARTITE | 49 | 26.5% | 2.0 | 1.0 | 0.00 | 10% |
| STACK | 43 | 32.6% | 2.0 | 1.0 | 0.00 | 9% |
| RANDOM | 41 | 29.3% | 2.0 | 2.0 | 0.00 | 20% |
| CONTROL(random accounts) | 600 | 0.5% | 2.0 | 1.0 | 0.00 | 20% |

## Interpretation (architect, not generated)
1. **Speed is not the constraint; size limits are.** Median 1.4 ms but p95 about 0.1 s, driven by high-degree accounts. About one in ten subgraphs (about one in six for the busiest accounts) is truncated by a limit, so the Graph Agent's evidence must carry the truncation flag (it does) and results for hub accounts are partial by design. The per-account cap keeps the earliest edges in the window, a deliberate but arbitrary choice that has not been evaluated.
2. **Structure is exposed.** Given the right centre and window, FAN-OUT hubs show a median of 7.5 distinct receivers and FAN-IN hubs 9.5 distinct senders, against 2 and 1 for random accounts. Cycles through the centre appear in 26-57% of block centres but in only 0.5% of random controls (the controls are active accounts with matching window lengths).
3. **Type-specific recovery is weak.** CYCLE blocks show a cycle through the centre only 57% of the time, and other block types show one 27-35% of the time, so "a cycle exists" does not identify the pattern type. Possible reasons, none verified: the first sender is not the loop origin; cycles longer than the search limit (6) or ordered differently in time; cycles formed through other accounts' ordinary transactions.
4. **Flow-through is uninformative as built.** Median is 0.00 for every block type and for controls (1-hour window, count-based). Not investigated further.
5. **What this is not.** The centre and window come from the block, so this says nothing about finding blocks unaided. No detector evaluation was done and no graph features have been tested inside B1 yet.
6. **Risk R-05:** whole-graph NetworkX at 1.5M edges took about 17 s and 465 MB; the full 5.08M-edge graph was not built. Windowed ego graphs are the working approach.
