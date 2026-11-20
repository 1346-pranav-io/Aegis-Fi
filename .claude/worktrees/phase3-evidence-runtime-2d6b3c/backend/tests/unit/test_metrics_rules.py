import numpy as np
import pandas as pd
import pytest

from aegis.analytics.rules import RULE_NAMES, RulePack, apply_rules, fit_thresholds
from aegis.eval.metrics import (
    brier,
    expected_calibration_error,
    precision_at_k,
    ranking_metrics,
    recall_at_k,
)


def test_ranking_metrics_hand_computed() -> None:
    y = np.array([1, 0, 1, 0, 0])
    s = np.array([0.9, 0.8, 0.7, 0.2, 0.1])
    m = ranking_metrics(y, s, [1, 2, 3])
    # ranking: pos, neg, pos, neg, neg -> AP = (1/1 + 2/3) / 2
    assert m.pr_auc == pytest.approx((1.0 + 2 / 3) / 2)
    assert m.roc_auc == pytest.approx(5 / 6)  # 5 of 6 (pos,neg) pairs ordered correctly
    assert m.recall_at_k == {1: 0.5, 2: 0.5, 3: 1.0}
    assert m.precision_at_k[3] == pytest.approx(2 / 3)
    assert m.base_rate == 0.4 and m.positives == 2 and m.n == 5


def test_ties_are_deterministic() -> None:
    y = np.array([0, 1, 0, 1])
    s = np.array([1.0, 1.0, 1.0, 1.0])
    assert recall_at_k(y, s, 2) == recall_at_k(y, s, 2) == 0.5  # index order breaks ties
    assert precision_at_k(y, s, 2) == 0.5


def test_single_class_rejected() -> None:
    with pytest.raises(ValueError):
        ranking_metrics(np.zeros(4), np.arange(4.0), [1])


def test_ece_and_brier() -> None:
    y = np.array([0, 0, 1, 1])
    assert expected_calibration_error(y, np.array([0.0, 0.0, 1.0, 1.0])) == 0.0
    # always predicting 0.5 when half are positive is perfectly calibrated in aggregate
    assert expected_calibration_error(y, np.full(4, 0.5)) == pytest.approx(0.0)
    assert expected_calibration_error(y, np.array([1.0, 1.0, 0.0, 0.0])) == 1.0
    assert brier(y, np.full(4, 0.5)) == 0.25
    with pytest.raises(ValueError):
        expected_calibration_error(y, np.array([1.2, 0, 0, 0]))


def feats(**kw: list[float]) -> pd.DataFrame:
    base = {
        "o_out_24h": [0.0],
        "d_in_24h": [0.0],
        "o_out_1h": [0.0],
        "o_in_24h": [0.0],
        "o_secs_since_in": [np.nan],
        "pair_reverse_prior": [0.0],
        "pair_prior": [1.0],
        "amt_vs_own_mean": [1.0],
    }
    base.update(kw)
    return pd.DataFrame(base)


PACK = RulePack(
    "t", fan_out_24h=5, fan_in_24h=5, velocity_1h=3, flow_through_secs=3600, new_cp_amount_ratio=10
)


def test_each_rule_fires_on_its_own_condition() -> None:
    cases = {
        "FAN_OUT_24H": feats(o_out_24h=[5.0]),
        "FAN_IN_24H": feats(d_in_24h=[5.0]),
        "VELOCITY_1H": feats(o_out_1h=[3.0]),
        "RAPID_FLOW_THROUGH": feats(o_in_24h=[1.0], o_secs_since_in=[3600.0]),
        "REVERSE_FLOW": feats(pair_reverse_prior=[1.0]),
        "NEW_COUNTERPARTY_LARGE": feats(pair_prior=[0.0], amt_vs_own_mean=[10.0]),
    }
    assert set(cases) == set(RULE_NAMES)
    for name, f in cases.items():
        r = apply_rules(f, PACK)
        assert bool(r.loc[0, name]) and r.loc[0, "rules_fired"] == 1, name
    quiet = apply_rules(feats(), PACK)
    assert quiet.loc[0, "rules_fired"] == 0


def test_thresholds_use_only_unlabeled_training_features() -> None:
    rng = np.random.default_rng(0)
    n = 10_000
    train = pd.DataFrame(
        {
            "o_out_24h": rng.poisson(1, n).astype(float),
            "d_in_24h": rng.poisson(1, n).astype(float),
            "o_out_1h": rng.poisson(0.2, n).astype(float),
            "amt_vs_own_mean": rng.lognormal(0, 1, n),
        }
    )
    assert "label" not in train.columns
    pack = fit_thresholds(train)
    assert pack.fan_out_24h >= 2 and pack.new_cp_amount_ratio >= 1
    assert pack == fit_thresholds(train)  # deterministic
