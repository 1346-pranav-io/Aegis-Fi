"""Rule pack v1 (baseline B0): deterministic typology indicators over point-in-time features.

Thresholds are derived from TRAINING-window feature distributions only (quantiles; no labels),
then frozen in the pack and stored with every output (FR-DET-01).
"""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

RULE_PACK_VERSION = "rules-v1"
RULE_NAMES = [
    "FAN_OUT_24H",
    "FAN_IN_24H",
    "VELOCITY_1H",
    "RAPID_FLOW_THROUGH",
    "REVERSE_FLOW",
    "NEW_COUNTERPARTY_LARGE",
]


@dataclass(frozen=True)
class RulePack:
    version: str
    fan_out_24h: float
    fan_in_24h: float
    velocity_1h: float
    flow_through_secs: float
    new_cp_amount_ratio: float

    def as_dict(self) -> dict[str, float | str]:
        return asdict(self)


def fit_thresholds(train: pd.DataFrame, quantile: float = 0.999) -> RulePack:
    """Freeze thresholds from unlabeled training features. Quantile is a declared constant."""
    q99 = float(np.nanquantile(train["amt_vs_own_mean"], 0.99))
    return RulePack(
        version=RULE_PACK_VERSION,
        fan_out_24h=max(2.0, float(np.nanquantile(train["o_out_24h"], quantile))),
        fan_in_24h=max(2.0, float(np.nanquantile(train["d_in_24h"], quantile))),
        velocity_1h=max(2.0, float(np.nanquantile(train["o_out_1h"], quantile))),
        flow_through_secs=3600.0,
        new_cp_amount_ratio=max(1.0, q99),
    )


def apply_rules(f: pd.DataFrame, pack: RulePack) -> pd.DataFrame:
    """Boolean frame, one column per rule, plus `rules_fired` (the B0 score; an integer count)."""
    out = pd.DataFrame(index=f.index)
    out["FAN_OUT_24H"] = f["o_out_24h"] >= pack.fan_out_24h
    out["FAN_IN_24H"] = f["d_in_24h"] >= pack.fan_in_24h
    out["VELOCITY_1H"] = f["o_out_1h"] >= pack.velocity_1h
    out["RAPID_FLOW_THROUGH"] = (f["o_in_24h"] >= 1) & (
        f["o_secs_since_in"] <= pack.flow_through_secs
    )
    out["REVERSE_FLOW"] = f["pair_reverse_prior"] >= 1
    out["NEW_COUNTERPARTY_LARGE"] = (f["pair_prior"] == 0) & (
        f["amt_vs_own_mean"] >= pack.new_cp_amount_ratio
    )
    out["rules_fired"] = out[RULE_NAMES].sum(axis=1).astype(np.int64)
    return out
