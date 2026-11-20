"""Detection metrics (EVALUATION_FRAMEWORK 3.1). Scores are scores; calibration is separate."""

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


@dataclass(frozen=True)
class RankingMetrics:
    n: int
    positives: int
    base_rate: float
    pr_auc: float  # average precision; a random scorer scores about base_rate
    roc_auc: float
    recall_at_k: dict[int, float]
    precision_at_k: dict[int, float]


def _rank_desc(score: np.ndarray) -> np.ndarray:
    """Indices sorted by score descending; ties broken by index (stable, deterministic)."""
    return np.argsort(-score, kind="stable")


def recall_at_k(y: np.ndarray, score: np.ndarray, k: int) -> float:
    pos = int(y.sum())
    if pos == 0:
        return float("nan")
    top = _rank_desc(score)[:k]
    return float(y[top].sum() / pos)


def precision_at_k(y: np.ndarray, score: np.ndarray, k: int) -> float:
    if k <= 0:
        return float("nan")
    top = _rank_desc(score)[:k]
    return float(y[top].sum() / len(top))


def ranking_metrics(y: np.ndarray, score: np.ndarray, ks: list[int]) -> RankingMetrics:
    y = y.astype(np.int64)
    pos = int(y.sum())
    if pos == 0 or pos == len(y):
        raise ValueError("need both classes to compute PR-AUC / ROC-AUC")
    return RankingMetrics(
        n=len(y),
        positives=pos,
        base_rate=pos / len(y),
        pr_auc=float(average_precision_score(y, score)),
        roc_auc=float(roc_auc_score(y, score)),
        recall_at_k={k: recall_at_k(y, score, k) for k in ks},
        precision_at_k={k: precision_at_k(y, score, k) for k in ks},
    )


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 15) -> float:
    """ECE, equal-width bins on [0,1], weighted by bin size. For probabilities only."""
    if np.any((p < 0) | (p > 1)):
        raise ValueError("probabilities must be in [0, 1]")
    idx = np.minimum((p * bins).astype(int), bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(float(y[m].mean()) - float(p[m].mean()))
    return ece


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))
