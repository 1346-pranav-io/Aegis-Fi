"""Helpers for the short-horizon temporal experiment (E8, EVALUATION_FRAMEWORK 3.6).

Pure numpy functions, unit-tested. They exist so the experiment script holds no statistics of its
own: window noise (bootstrap), drift (PSI), horizon trend net of day effects, and the
investigation-level proxy metrics (block hit@k, account-level average precision).
"""

from collections.abc import Sequence

import numpy as np


def average_precision_weighted(y_sorted: np.ndarray, w: np.ndarray) -> float:
    """AP for rows already sorted by descending score, with non-negative row weights `w`."""
    wy = w * y_sorted
    tp = np.cumsum(wy)
    seen = np.cumsum(w)
    total = tp[-1]
    if total <= 0:
        return float("nan")
    with np.errstate(divide="ignore", invalid="ignore"):
        prec = np.where(seen > 0, tp / seen, 0.0)
    return float((wy * prec).sum() / total)


def bootstrap_ap(
    y: np.ndarray, score: np.ndarray, n_boot: int = 200, seed: int = 0
) -> tuple[float, float, float]:
    """(AP, lower, upper): Poisson(1) row-weight bootstrap, 95% percentile interval.
    Rows are resampled independently, which ignores that laundering rows cluster in pattern
    blocks, so the interval is too narrow. It is a lower bound on the noise, not the noise."""
    order = np.argsort(-score, kind="stable")
    ys = y[order].astype(np.float64)
    point = average_precision_weighted(ys, np.ones_like(ys))
    rng = np.random.default_rng(seed)
    vals = np.array(
        [average_precision_weighted(ys, rng.poisson(1.0, len(ys)).astype(np.float64))
         for _ in range(n_boot)]
    )  # fmt: skip
    vals = vals[~np.isnan(vals)]
    if len(vals) == 0:
        return point, float("nan"), float("nan")
    return point, float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    """Population stability index with quantile bins fixed on `reference` (declared in advance:
    10 bins, 1e-4 floor). Rule of thumb in the literature: <0.1 small, >0.25 large; here it is
    only compared with performance change, not used as a verdict."""
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)[1:-1]))
    ref = np.bincount(np.searchsorted(edges, reference, side="right"), minlength=len(edges) + 1)
    cur = np.bincount(np.searchsorted(edges, current, side="right"), minlength=len(edges) + 1)
    p = np.maximum(ref / float(max(int(ref.sum()), 1)), 1e-4)
    q = np.maximum(cur / float(max(int(cur.sum()), 1)), 1e-4)
    return float(np.sum((q - p) * np.log(q / p)))


def horizon_trend(matrix: dict[tuple[int, int], float]) -> dict[str, float]:
    """Trend of a metric with horizon, net of day effects.

    `matrix[(origin_day, eval_day)]` = metric. Horizon = eval_day - origin_day. Each eval day's
    mean over origins is subtracted from both the metric and the horizon (so a hard day does not
    look like decay), then the slope is fitted by least squares (a within-day estimator).
    NOTE: horizon is confounded with training-set size (an earlier origin has less history); the
    caller must say so when reporting it."""
    days = sorted({d for _, d in matrix})
    resid_h: list[float] = []
    resid_v: list[float] = []
    for d in days:
        cells = [(o, v) for (o, dd), v in matrix.items() if dd == d and not np.isnan(v)]
        if len(cells) < 2:
            continue
        mean = float(np.mean([v for _, v in cells]))
        mean_h = float(np.mean([d - o for o, _ in cells]))
        for o, v in cells:  # within-day estimator: demean both the metric and the horizon
            resid_h.append(d - o - mean_h)
            resid_v.append(v - mean)
    hh, vv = np.asarray(resid_h, dtype=float), np.asarray(resid_v, dtype=float)
    if len(hh) < 3 or np.ptp(hh) == 0:
        return {"n_cells": float(len(hh)), "slope_per_day": float("nan")}
    slope = float(np.polyfit(hh, vv, 1)[0])
    return {"n_cells": float(len(hh)), "slope_per_day": slope,
            "resid_sd": float(vv.std(ddof=1))}  # fmt: skip


def block_hit_at_k(score: np.ndarray, block_id: np.ndarray, k: int) -> tuple[int, int]:
    """(blocks with >=1 member in the top-k scored rows, blocks present). `block_id` is -1 for
    rows in no block. A block counts as present when at least one of its rows is in `score`."""
    present = set(np.unique(block_id[block_id >= 0]).tolist())
    if not present:
        return 0, 0
    k = min(k, len(score))
    top = np.argpartition(-score, k - 1)[:k] if k < len(score) else np.arange(len(score))
    hit = set(np.unique(block_id[top][block_id[top] >= 0]).tolist())
    return len(hit), len(present)


def group_max(keys: np.ndarray, score: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Account-level view: for each key the max score and the max label (any positive)."""
    order = np.argsort(keys, kind="stable")
    k, s, lab = keys[order], score[order], y[order]
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    return np.maximum.reduceat(s, starts), np.maximum.reduceat(lab, starts)


def rank_correlation(a: Sequence[float], b: Sequence[float]) -> float:
    """Spearman rank correlation (average ranks for ties)."""
    from scipy.stats import spearmanr

    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    ok = ~(np.isnan(x) | np.isnan(y))
    if ok.sum() < 3:
        return float("nan")
    r = spearmanr(x[ok], y[ok]).statistic
    return float(r)
