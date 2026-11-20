import numpy as np

from aegis.eval.temporal import (
    average_precision_weighted,
    block_hit_at_k,
    bootstrap_ap,
    group_max,
    horizon_trend,
    psi,
    rank_correlation,
)


def test_weighted_ap_matches_sklearn_with_unit_weights() -> None:
    from sklearn.metrics import average_precision_score

    rng = np.random.default_rng(1)
    y = (rng.random(500) < 0.1).astype(float)
    s = rng.random(500) + y * 0.5
    order = np.argsort(-s, kind="stable")
    ours = average_precision_weighted(y[order], np.ones(500))
    assert abs(ours - average_precision_score(y, s)) < 1e-9


def test_bootstrap_interval_contains_point_and_is_deterministic() -> None:
    rng = np.random.default_rng(2)
    y = (rng.random(2000) < 0.05).astype(int)
    s = rng.random(2000) + y
    a = bootstrap_ap(y, s, n_boot=100, seed=3)
    assert a == bootstrap_ap(y, s, n_boot=100, seed=3)
    assert a[1] <= a[0] <= a[2]


def test_psi_zero_for_same_and_positive_for_shift() -> None:
    rng = np.random.default_rng(4)
    x = rng.normal(size=20_000)
    assert psi(x, x) == 0.0
    assert psi(x, x + 1.0) > 0.25


def test_horizon_trend_ignores_day_effects_and_finds_decay() -> None:
    # day effect +0.1 per day, and a true decay of -0.02 per day of horizon
    m = {(o, d): 0.1 * d - 0.02 * (d - o) for o in range(3) for d in range(o + 1, 6)}
    assert abs(horizon_trend(m)["slope_per_day"] - (-0.02)) < 1e-9
    flat = {(o, d): 0.1 * d for o in range(3) for d in range(o + 1, 6)}
    assert abs(horizon_trend(flat)["slope_per_day"]) < 1e-9


def test_block_hit_and_group_max() -> None:
    score = np.array([0.9, 0.8, 0.1, 0.7, 0.2])
    block = np.array([0, 0, 1, 1, -1])
    assert block_hit_at_k(score, block, 1) == (1, 2)
    assert block_hit_at_k(score, block, 4) == (2, 2)
    s, y = group_max(np.array([5, 5, 7]), np.array([0.2, 0.9, 0.4]), np.array([0, 1, 0]))
    assert s.tolist() == [0.9, 0.4] and y.tolist() == [1, 0]


def test_rank_correlation() -> None:
    assert rank_correlation([1, 2, 3, 4], [2, 4, 6, 8]) == 1.0
    assert np.isnan(rank_correlation([1, 2], [1, 2]))


def test_trace_checker_resolves_paths_and_selectors() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "check_results_trace", Path(__file__).parents[2] / "scripts" / "check_results_trace.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    doc = {"a": {"b": [{"k": "x", "v": 1.5}, {"k": "y", "v": 2.5}]}}
    assert mod.resolve(doc, "a.b.1.v") == 2.5
    assert mod.resolve(doc, "a.b.[k=x].v") == 1.5
