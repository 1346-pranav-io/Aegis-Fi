"""Fit rule pack v2 thresholds and weights from TRAIN labels only (D-26).

Lives in aegis.ml because it consumes labels (passed in as an array by the caller); the pure rule
application code in aegis.analytics never sees labels.
"""

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from aegis.analytics.rules_v2 import (
    RULE_PACK_V2_VERSION,
    RULE_SPECS,
    FittedRule,
    RulePackV2,
    fire,
)

UPPER_QUANTILES = (0.5, 0.75, 0.9, 0.95, 0.99, 0.999)
LOWER_QUANTILES = (0.001, 0.01, 0.05, 0.1, 0.25, 0.5)
MIN_FIRINGS = 500


def fit_rule_pack_v2(train: pd.DataFrame, y: np.ndarray) -> RulePackV2:
    if len(train) != len(y):
        raise ValueError("features and labels must align")
    y = y.astype(np.int64)
    base = float(y.mean())
    if y.sum() == 0:
        raise ValueError("no positives in training data")
    fitted: list[FittedRule] = []
    for spec in RULE_SPECS:
        x = train[spec.feature].to_numpy(np.float64)
        qs = UPPER_QUANTILES if spec.direction == "ge" else LOWER_QUANTILES
        cands = sorted({float(np.nanquantile(x, q)) for q in qs})
        best: tuple[float, float, float] | None = None  # (ap, threshold, lift)
        for th in cands:
            m = fire(train, spec, th)
            n = int(m.sum())
            if n < MIN_FIRINGS or n == len(m):
                continue
            precision = float(y[m].mean())
            lift = precision / base if base > 0 else 0.0
            if lift <= 1.0:
                continue
            ap = float(average_precision_score(y, m.astype(np.float64)))
            if best is None or ap > best[0]:
                best = (ap, th, lift)
        if best is None:
            fitted.append(FittedRule(spec.name, None, 0.0))
        else:
            fitted.append(FittedRule(spec.name, best[1], float(np.log(best[2]))))
    return RulePackV2(RULE_PACK_V2_VERSION, tuple(fitted))
