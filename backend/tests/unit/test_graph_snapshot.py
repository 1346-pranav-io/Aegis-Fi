import numpy as np

from aegis.ml.graph_snapshot import build_snapshot


def test_snapshot_contains_only_earlier_edges() -> None:
    t = np.array([10, 20, 30, 40])
    src = np.array([0, 1, 0, 2])
    dst = np.array([1, 2, 2, 0])
    amt = np.array([1.0, 2.0, 3.0, 4.0])
    s = build_snapshot(t, src, dst, amt, n_nodes=3, cutoff=30)
    assert s.n_edges == 2  # t=30 is NOT included: strictly before the cutoff
    assert s.x.shape == (3, 5)
    assert np.isclose(s.x[0, 0], np.log1p(1.0)) and np.isclose(s.x[2, 1], np.log1p(1.0))


def test_future_edges_cannot_change_a_snapshot() -> None:
    rng = np.random.default_rng(0)
    n, m = 50, 400
    t = rng.integers(0, 1000, m)
    src, dst = rng.integers(0, n, m), rng.integers(0, n, m)
    amt = rng.random(m) * 100
    base = build_snapshot(t, src, dst, amt, n, cutoff=500)
    amt2 = amt.copy()
    amt2[t >= 500] *= 1000  # perturb only the future
    again = build_snapshot(t, src, dst, amt2, n, cutoff=500)
    assert np.array_equal(base.x, again.x) and base.n_edges == again.n_edges
