"""Rule pack v2 application (pure, no labels). Fitting lives in aegis.ml.rules_fit (D-26)."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

RULE_PACK_V2_VERSION = "rules-v2"


@dataclass(frozen=True)
class RuleSpec:
    name: str
    feature: str
    direction: str  # "ge": fires when feature >= threshold; "le": fires when feature <= threshold
    requires_new_pair: bool = False  # also require pair_prior == 0 (NEW_COUNTERPARTY_LARGE)


RULE_SPECS: tuple[RuleSpec, ...] = (
    RuleSpec("FAN_OUT_24H", "o_out_24h", "ge"),
    RuleSpec("FAN_IN_24H", "d_in_24h", "ge"),
    RuleSpec("VELOCITY_1H", "o_out_1h", "ge"),
    RuleSpec("RAPID_FLOW_THROUGH", "o_secs_since_in", "le"),
    RuleSpec("REVERSE_FLOW", "pair_reverse_prior", "ge"),
    RuleSpec("NEW_COUNTERPARTY_LARGE", "amt_vs_own_mean", "ge", requires_new_pair=True),
    RuleSpec("FRESH_DEST", "d_in_all", "le"),
    RuleSpec("FRESH_ORIGIN", "o_out_all", "le"),
)


@dataclass(frozen=True)
class FittedRule:
    name: str
    threshold: float | None  # None = disabled
    weight: float


@dataclass(frozen=True)
class RulePackV2:
    version: str
    rules: tuple[FittedRule, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "rules": [
                {"name": r.name, "threshold": r.threshold, "weight": r.weight} for r in self.rules
            ],
        }


def fire(f: pd.DataFrame, spec: RuleSpec, threshold: float) -> np.ndarray:
    x = f[spec.feature].to_numpy(np.float64)
    with np.errstate(invalid="ignore"):  # NaN compares False: missing history never fires a rule
        m = x >= threshold if spec.direction == "ge" else x <= threshold
    if spec.requires_new_pair:
        m = m & (f["pair_prior"].to_numpy(np.float64) == 0)
    return np.asarray(m, dtype=bool)


def apply_rules_v2(f: pd.DataFrame, pack: RulePackV2) -> pd.DataFrame:
    """One boolean column per enabled rule plus `score` = sum of weights of fired rules."""
    specs = {s.name: s for s in RULE_SPECS}
    out = pd.DataFrame(index=f.index)
    score = np.zeros(len(f))
    for r in pack.rules:
        if r.threshold is None:
            continue
        m = fire(f, specs[r.name], r.threshold)
        out[r.name] = m
        score += np.where(m, r.weight, 0.0)
    out["score"] = score
    return out
