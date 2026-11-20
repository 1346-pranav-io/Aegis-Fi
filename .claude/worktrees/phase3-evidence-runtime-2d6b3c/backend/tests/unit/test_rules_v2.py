import numpy as np
import pandas as pd
import pytest

from aegis.analytics.rules_v2 import FittedRule, RulePackV2, apply_rules_v2
from aegis.ml.rules_fit import MIN_FIRINGS, fit_rule_pack_v2


def make(n: int = 20_000, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.02).astype(np.int64)
    f = pd.DataFrame(
        {
            "o_out_24h": rng.poisson(1.0, n).astype(float) + 6 * y,  # informative
            "d_in_24h": rng.poisson(1.0, n).astype(float),  # noise
            "o_out_1h": rng.poisson(0.2, n).astype(float),
            "o_secs_since_in": np.where(rng.random(n) < 0.3, rng.exponential(5000, n), np.nan),
            "pair_reverse_prior": (rng.random(n) < 0.05).astype(float),
            "pair_prior": rng.poisson(0.5, n).astype(float),
            "amt_vs_own_mean": rng.lognormal(0, 1, n),
            "d_in_all": rng.poisson(5, n).astype(float),
            "o_out_all": rng.poisson(5, n).astype(float),
        }
    )
    return f, y


def test_informative_rule_is_enabled_with_positive_weight_noise_is_not_rewarded() -> None:
    f, y = make()
    pack = fit_rule_pack_v2(f, y)
    by = {r.name: r for r in pack.rules}
    assert by["FAN_OUT_24H"].threshold is not None and by["FAN_OUT_24H"].weight > 0
    assert all(r.weight >= 0 for r in pack.rules)
    assert all(r.threshold is None or r.weight > 0 for r in pack.rules)


def test_fit_is_deterministic_and_rejects_misaligned_or_empty() -> None:
    f, y = make()
    assert fit_rule_pack_v2(f, y) == fit_rule_pack_v2(f, y)
    with pytest.raises(ValueError):
        fit_rule_pack_v2(f.iloc[:10], y)
    with pytest.raises(ValueError):
        fit_rule_pack_v2(f, np.zeros(len(f), dtype=np.int64))


def test_rules_need_minimum_support() -> None:
    f, y = make(n=MIN_FIRINGS - 1)  # fewer rows than the minimum firings: nothing can qualify
    y[:3] = 1
    pack = fit_rule_pack_v2(f, y)
    assert all(r.threshold is None and r.weight == 0.0 for r in pack.rules)


def test_apply_scores_sum_of_fired_weights_and_nan_never_fires() -> None:
    pack = RulePackV2(
        "t",
        (
            FittedRule("FAN_OUT_24H", 5.0, 2.0),
            FittedRule("RAPID_FLOW_THROUGH", 3600.0, 1.5),
            FittedRule("NEW_COUNTERPARTY_LARGE", 10.0, 1.0),
            FittedRule("FRESH_DEST", None, 0.0),
        ),
    )
    f = pd.DataFrame(
        {
            "o_out_24h": [5.0, 1.0, 5.0],
            "o_secs_since_in": [100.0, np.nan, 5000.0],
            "amt_vs_own_mean": [20.0, 20.0, 1.0],
            "pair_prior": [0.0, 0.0, 3.0],
        }
    )
    r = apply_rules_v2(f, pack)
    assert list(r["score"]) == [2.0 + 1.5 + 1.0, 0.0 + 0.0 + 1.0, 2.0]
    assert "FRESH_DEST" not in r.columns  # disabled rules are not applied
