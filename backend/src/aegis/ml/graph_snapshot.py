"""Causal graph snapshots for graph-model experiments (Phase 7, D-20, D-32).

A snapshot at `cutoff` contains only transactions with `t < cutoff`. A transaction scored on a day
uses the snapshot taken at the start of that day, so no edge from its own day or later can reach
it through message passing. This is stricter than the point-in-time features of B1 (which see
earlier transactions of the same day); the difference is reported with the results.
numpy only: importing this module never needs torch.
"""

from dataclasses import dataclass

import numpy as np

NODE_FEATURES = ("log1p_out_count", "log1p_in_count", "log1p_out_amount", "log1p_in_amount",
                 "has_history")  # fmt: skip


@dataclass(frozen=True)
class Snapshot:
    cutoff: int
    n_nodes: int
    src: np.ndarray  # int64 edge sources (only edges with t < cutoff)
    dst: np.ndarray
    x: np.ndarray  # float32 [n_nodes, len(NODE_FEATURES)]

    @property
    def n_edges(self) -> int:
        return int(len(self.src))


def build_snapshot(
    t: np.ndarray, src: np.ndarray, dst: np.ndarray, amount: np.ndarray, n_nodes: int, cutoff: int
) -> Snapshot:
    """Edges strictly before `cutoff` and per-node features computed from those edges alone."""
    m = t < cutoff
    s, d, a = src[m], dst[m], amount[m]
    out_c = np.bincount(s, minlength=n_nodes).astype(np.float64)
    in_c = np.bincount(d, minlength=n_nodes).astype(np.float64)
    out_a = np.bincount(s, weights=a, minlength=n_nodes)
    in_a = np.bincount(d, weights=a, minlength=n_nodes)
    x = np.stack(
        [np.log1p(out_c), np.log1p(in_c), np.log1p(out_a), np.log1p(in_a),
         ((out_c + in_c) > 0).astype(np.float64)],
        axis=1,
    ).astype(np.float32)  # fmt: skip
    return Snapshot(cutoff=cutoff, n_nodes=n_nodes, src=s.astype(np.int64),
                    dst=d.astype(np.int64), x=x)  # fmt: skip
