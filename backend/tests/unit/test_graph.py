import networkx as nx
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aegis.analytics.graph import (
    EdgeIndex,
    GraphLimits,
    cycles_through,
    ego_metrics,
    ego_subgraph,
    flow_through_fraction,
)

BIG = 10**9


def index(edges: list[tuple[int, int, int]]) -> EdgeIndex:
    s, d, t = (np.array(x, dtype=np.int64) for x in zip(*edges, strict=True))
    return EdgeIndex.build(s, d, t, np.ones(len(edges)))


def test_cycle_found_through_center_and_length() -> None:
    idx = index([(1, 2, 10), (2, 3, 20), (3, 1, 30), (3, 4, 40)])
    ego = ego_subgraph(idx, 1, 2, 0, BIG)
    m = ego_metrics(idx, ego)
    assert m["n_cycles_through_center"] == 1 and m["shortest_cycle_len"] == 3
    assert m["scc_size_of_center"] == 3
    assert m["truncated"] is False and m["algorithm"] == "graph-v1"


def test_matches_networkx_reference_on_random_graph() -> None:
    rng = np.random.default_rng(3)
    e = [(int(a), int(b), i) for i, (a, b) in enumerate(rng.integers(0, 8, (40, 2))) if a != b]
    idx = index(e)
    ego = ego_subgraph(idx, 0, 3, 0, BIG, GraphLimits(max_cycle_len=5))
    g = nx.DiGraph([(a, b) for a, b, _ in e])
    ref = {tuple(c) for c in nx.simple_cycles(g, length_bound=5) if 0 in c and len(c) >= 2}

    # compare as rotation-normalised cycles
    def norm(c: tuple[int, ...]) -> tuple[int, ...]:
        i = c.index(0)
        return c[i:] + c[:i]

    mine = {norm(c) for c in cycles_through(nx.DiGraph([(a, b) for a, b, _ in e]), 0, 5, 10_000)}
    assert mine == {norm(c) for c in ref}
    assert ego.nodes  # non-empty


def test_time_window_is_half_open() -> None:
    idx = index([(1, 2, 100), (1, 3, 200), (1, 4, 300)])
    ids = idx.incident(1, 100, 300)
    assert sorted(idx.t[ids].tolist()) == [100, 200]


def test_fan_out_counts_and_distinct() -> None:
    idx = index([(1, 2, 1), (1, 3, 2), (1, 3, 3), (1, 4, 4)])
    m = ego_metrics(idx, ego_subgraph(idx, 1, 1, 0, BIG))
    assert m["center_out_txns"] == 4 and m["center_out_distinct"] == 3
    assert m["center_in_txns"] == 0


def test_truncation_is_flagged_never_silent() -> None:
    idx = index([(1, i, i) for i in range(2, 50)])
    ego = ego_subgraph(idx, 1, 1, 0, BIG, GraphLimits(max_edges=10))
    assert ego.truncated and "max_edges reached" in ego.truncation_reasons
    assert len(ego.edge_ids) == 10
    capped = ego_subgraph(idx, 1, 1, 0, BIG, GraphLimits(max_edges_per_node=5))
    assert capped.truncated and len(capped.edge_ids) == 5
    nodes = ego_subgraph(idx, 1, 1, 0, BIG, GraphLimits(max_nodes=5))
    assert nodes.truncated and "max_nodes reached" in nodes.truncation_reasons
    m = ego_metrics(idx, ego)
    assert m["truncated"] is True and m["truncation_reasons"]


def test_flow_through_fraction() -> None:
    # center 5 receives at t=100 and sends at 150 (within 100s) and at 1000 (outside)
    idx = index([(9, 5, 100), (5, 6, 150), (5, 7, 1000)])
    ego = ego_subgraph(idx, 5, 1, 0, BIG)
    assert flow_through_fraction(idx, ego, 100) == pytest.approx(0.5)
    assert flow_through_fraction(idx, ego_subgraph(idx, 6, 1, 0, BIG), 100) is None


def test_deterministic() -> None:
    idx = index([(1, 2, 1), (2, 3, 2), (3, 1, 3), (1, 4, 4), (4, 1, 5)])
    a = ego_metrics(idx, ego_subgraph(idx, 1, 2, 0, BIG))
    b = ego_metrics(idx, ego_subgraph(idx, 1, 2, 0, BIG))
    assert a == b


@settings(max_examples=40, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(0, 6), st.integers(0, 6), st.integers(0, 100)),
        min_size=1,
        max_size=30,
    ),
    st.integers(0, 100),
)
def test_future_edges_outside_window_never_change_result(
    edges: list[tuple[int, int, int]], cut: int
) -> None:
    """as_of-style property: edges at or after the window end cannot change the window result."""
    full = index(edges)
    kept = [e for e in edges if e[2] < cut]
    if not kept:
        return
    part = index(kept)
    a = ego_metrics(full, ego_subgraph(full, edges[0][0], 2, 0, cut))
    b = ego_metrics(part, ego_subgraph(part, edges[0][0], 2, 0, cut))
    assert a == b


def test_bad_inputs() -> None:
    with pytest.raises(ValueError):
        EdgeIndex.build(np.array([1]), np.array([1, 2]), np.array([1]), np.array([1.0]))
    idx = index([(1, 2, 1)])
    with pytest.raises(ValueError):
        ego_subgraph(idx, 1, 0, 0, BIG)


def test_pagerank_nonconvergence_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> dict[int, float]:
        raise nx.PowerIterationFailedConvergence(200)

    monkeypatch.setattr(nx, "pagerank", boom)
    idx = index([(1, 2, 1), (2, 1, 2)])
    m = ego_metrics(idx, ego_subgraph(idx, 1, 1, 0, BIG))
    assert "pagerank_of_center" not in m and "no" in str(m["pagerank_failed"]).lower()
