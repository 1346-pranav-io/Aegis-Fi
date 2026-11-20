"""Windowed ego-subgraph analytics for the Graph Agent (AG-03). Pure: arrays in, results out.

Nodes are integer account codes; an edge is one transaction (src, dst, t, amount). The caller
passes only edges already visible at `as_of`. Size limits are explicit: when a limit is hit the
result says so (`truncated`, `truncation_reason`); truncation is never silent (AG-03, NFR-PERF-05).
"""

from collections import deque
from dataclasses import dataclass, field

import networkx as nx
import numpy as np

ALGORITHM_VERSION = "graph-v1"


@dataclass(frozen=True)
class GraphLimits:
    max_nodes: int = 5_000
    max_edges: int = 50_000
    max_edges_per_node: int = 2_000  # earliest edges in the window are kept, the rest flagged
    max_cycle_len: int = 6
    max_cycles: int = 1_000
    community_edge_limit: int = 20_000


DEFAULT_LIMITS = GraphLimits()


@dataclass(frozen=True)
class EdgeIndex:
    """Sorted views for O(log n) neighbour lookup. Edge id = position in the input arrays."""

    src: np.ndarray
    dst: np.ndarray
    t: np.ndarray
    amount: np.ndarray
    _by_src: np.ndarray
    _src_sorted: np.ndarray
    _by_dst: np.ndarray
    _dst_sorted: np.ndarray

    @classmethod
    def build(
        cls, src: np.ndarray, dst: np.ndarray, t: np.ndarray, amount: np.ndarray
    ) -> "EdgeIndex":
        if not (len(src) == len(dst) == len(t) == len(amount)):
            raise ValueError("edge arrays must have equal length")
        by_src = np.argsort(src, kind="stable")
        by_dst = np.argsort(dst, kind="stable")
        return cls(src, dst, t, amount, by_src, src[by_src], by_dst, dst[by_dst])

    def incident(self, node: int, t_start: int, t_end: int) -> np.ndarray:
        """Edge ids touching `node` with t in [t_start, t_end), ordered by (t, id)."""
        lo, hi = np.searchsorted(self._src_sorted, [node, node + 1])
        out = self._by_src[lo:hi]
        lo, hi = np.searchsorted(self._dst_sorted, [node, node + 1])
        inn = self._by_dst[lo:hi]
        ids = np.unique(np.concatenate([out, inn]))  # self-loops appear once
        tt = self.t[ids]
        ids = ids[(tt >= t_start) & (tt < t_end)]
        return np.asarray(ids[np.lexsort((ids, self.t[ids]))])


@dataclass(frozen=True)
class EgoResult:
    center: int
    hops: int
    edge_ids: np.ndarray
    nodes: tuple[int, ...]
    truncated: bool
    truncation_reasons: tuple[str, ...] = field(default=())


def ego_subgraph(
    index: EdgeIndex,
    center: int,
    hops: int,
    t_start: int,
    t_end: int,
    limits: GraphLimits = DEFAULT_LIMITS,
) -> EgoResult:
    if hops < 1 or hops > 3:
        raise ValueError("hops must be 1..3")
    seen_nodes: set[int] = {center}
    edge_set: set[int] = set()
    reasons: list[str] = []
    frontier = deque([center])
    for _ in range(hops):
        nxt: deque[int] = deque()
        for node in sorted(frontier):
            ids = index.incident(node, t_start, t_end)
            if len(ids) > limits.max_edges_per_node:
                reasons.append(f"node {node}: {len(ids)} edges > per-node cap; kept earliest")
                ids = ids[: limits.max_edges_per_node]
            for e in ids:
                if len(edge_set) >= limits.max_edges and int(e) not in edge_set:
                    if "max_edges reached" not in reasons:
                        reasons.append("max_edges reached")
                    continue
                edge_set.add(int(e))
                for other in (int(index.src[e]), int(index.dst[e])):
                    if other not in seen_nodes:
                        if len(seen_nodes) >= limits.max_nodes:
                            if "max_nodes reached" not in reasons:
                                reasons.append("max_nodes reached")
                            continue
                        seen_nodes.add(other)
                        nxt.append(other)
        frontier = nxt
    ids_sorted = np.array(sorted(edge_set), dtype=np.int64)
    return EgoResult(
        center, hops, ids_sorted, tuple(sorted(seen_nodes)), bool(reasons), tuple(reasons)
    )


def _digraph(index: EdgeIndex, ego: EgoResult) -> "nx.DiGraph[int]":
    g: nx.DiGraph[int] = nx.DiGraph()
    g.add_nodes_from(ego.nodes)
    for e in ego.edge_ids:
        u, v = int(index.src[e]), int(index.dst[e])
        if g.has_edge(u, v):
            g[u][v]["count"] += 1
        else:
            g.add_edge(u, v, count=1)
    return g


def cycles_through(
    g: "nx.DiGraph[int]", center: int, max_len: int, max_cycles: int
) -> list[tuple[int, ...]]:
    """Simple directed cycles of length 2..max_len that contain `center` (DFS from center)."""
    found: list[tuple[int, ...]] = []
    path = [center]
    on_path = {center}

    def dfs(node: int) -> None:
        if len(found) >= max_cycles:
            return
        for nb in sorted(g.successors(node)):
            if nb == center and len(path) >= 2:
                found.append(tuple(path))
            elif nb not in on_path and len(path) < max_len:
                path.append(nb)
                on_path.add(nb)
                dfs(nb)
                path.pop()
                on_path.discard(nb)

    dfs(center)
    return found


def flow_through_fraction(index: EdgeIndex, ego: EgoResult, window_s: int) -> float | None:
    """Share of the centre's outgoing transactions that follow an incoming one within window_s."""
    c = ego.center
    ids = ego.edge_ids
    src, dst, t = index.src[ids], index.dst[ids], index.t[ids]
    out_t = np.sort(t[(src == c) & (dst != c)])
    in_t = np.sort(t[(dst == c) & (src != c)])
    if len(out_t) == 0:
        return None
    if len(in_t) == 0:
        return 0.0
    pos = np.searchsorted(in_t, out_t, side="left")  # incoming strictly before each outgoing
    has_prev = pos > 0
    last_in = in_t[np.maximum(pos - 1, 0)]
    return float(np.mean(has_prev & (out_t - last_in <= window_s)))


def ego_metrics(
    index: EdgeIndex,
    ego: EgoResult,
    limits: GraphLimits = DEFAULT_LIMITS,
    flow_window_s: int = 3600,
) -> dict[str, object]:
    """Deterministic summary of an ego subgraph; results carry algorithm name/version/params."""
    c = ego.center
    g = _digraph(index, ego)
    src, dst = index.src[ego.edge_ids], index.dst[ego.edge_ids]
    out_mask = (src == c) & (dst != c)
    in_mask = (dst == c) & (src != c)
    cyc = cycles_through(g, c, limits.max_cycle_len, limits.max_cycles)
    scc_size = next(len(s) for s in nx.strongly_connected_components(g) if c in s)
    res: dict[str, object] = {
        "algorithm": ALGORITHM_VERSION,
        "params": {
            "hops": ego.hops,
            "max_cycle_len": limits.max_cycle_len,
            "flow_window_s": flow_window_s,
        },
        "truncated": ego.truncated,
        "truncation_reasons": list(ego.truncation_reasons),
        "n_nodes": g.number_of_nodes(),
        "n_edges": int(len(ego.edge_ids)),
        "center_out_txns": int(out_mask.sum()),
        "center_in_txns": int(in_mask.sum()),
        "center_out_distinct": int(len(set(dst[out_mask].tolist()))),
        "center_in_distinct": int(len(set(src[in_mask].tolist()))),
        "n_cycles_through_center": len(cyc),
        "shortest_cycle_len": min((len(x) for x in cyc), default=None),
        "cycle_search_capped": len(cyc) >= limits.max_cycles,
        "scc_size_of_center": scc_size,
        "flow_through_fraction": flow_through_fraction(index, ego, flow_window_s),
    }
    if g.number_of_nodes() > 1:
        try:
            pr = nx.pagerank(g, alpha=0.85, tol=1e-8, max_iter=200, weight="count")
            res["pagerank_of_center"] = float(pr[c])
        except nx.PowerIterationFailedConvergence:
            res["pagerank_failed"] = "power iteration did not converge in 200 iterations"
    if len(ego.edge_ids) <= limits.community_edge_limit and g.number_of_edges() > 0:
        comms = nx.community.louvain_communities(g.to_undirected(), seed=0, weight="count")
        res["n_communities"] = len(comms)
        res["center_community_size"] = next(len(s) for s in comms if c in s)
    else:
        res["community_skipped"] = "subgraph larger than community_edge_limit"
    return res
