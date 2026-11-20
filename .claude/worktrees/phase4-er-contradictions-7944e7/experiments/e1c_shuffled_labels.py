"""E1c: shuffled-label negative control for the E1 XGBoost pipeline.

Pass criteria, fixed before the run: training and validation labels are permuted within their own
window (features untouched); the model is evaluated on the REAL test labels. If the pipeline has
no leakage the model can learn nothing, so we expect, for each permutation seed:
  ROC-AUC within 0.5 +- 0.03   (3 standard errors for ~956 positives is about 0.028)
  PR-AUC  <= 2 x test base rate
Anything outside this is a red flag to investigate before trusting E1.
Also reported: the E1 selected-seed model's scores against randomly permuted TEST labels
(evaluation-code smoke test; should sit at the base rate).

Run from backend/ as the read-only app role:
  AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis \
  uv run python ../experiments/e1c_shuffled_labels.py
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg
import xgboost as xgb

from aegis.analytics.features import build_features
from aegis.eval.metrics import ranking_metrics
from aegis.eval.splits import Part, load_split
from aegis.store.frames import load_transactions
from aegis.store.labels import fetch_label_series

ROOT = Path(__file__).resolve().parents[1]
DATASET = "IBM-AML-HI-Small"
PERM_SEEDS = [101, 202, 303]
XGB_PARAMS: dict[str, Any] = {  # identical to E1
    "objective": "binary:logistic",
    "eval_metric": "aucpr",
    "max_depth": 6,
    "eta": 0.1,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "tree_method": "hist",
    "nthread": 16,
}


def fit_predict(
    x_tr: pd.DataFrame, y_tr: np.ndarray, x_va: pd.DataFrame, y_va: np.ndarray, x_te: pd.DataFrame
) -> tuple[np.ndarray, int]:
    params = {
        **XGB_PARAMS,
        "seed": 11,
        "scale_pos_weight": float((y_tr == 0).sum() / (y_tr == 1).sum()),
    }
    names = list(x_tr.columns)
    b = xgb.train(
        params,
        xgb.DMatrix(x_tr, label=y_tr, feature_names=names),
        400,
        evals=[(xgb.DMatrix(x_va, label=y_va, feature_names=names), "val")],
        early_stopping_rounds=30,
        verbose_eval=False,
    )
    p = b.predict(xgb.DMatrix(x_te, feature_names=names), iteration_range=(0, b.best_iteration + 1))
    return np.asarray(p, dtype=np.float64), int(b.best_iteration) + 1


def main() -> None:
    t0 = time.monotonic()
    split = load_split(ROOT / "experiments" / "configs" / "split_ibm_hi_small_v1.json")
    with psycopg.connect(os.environ["AEGIS_APP_URL"]) as conn:
        cur = conn.execute("SELECT clock_timestamp()").fetchone()
        if cur is None:
            raise RuntimeError("no clock")
        raw = load_transactions(conn, cur[0])
        labels = fetch_label_series(conn, DATASET)
    feats = build_features(raw)
    y = labels.reindex(raw["ref"]).to_numpy().astype(np.int64)
    when = pd.to_datetime(raw["t"], unit="s", utc=True).dt.floor("h")
    part_of = when.map({ts: split.part_of(ts.to_pydatetime()) for ts in when.unique()})
    idx = {p: np.flatnonzero((part_of == p).to_numpy()) for p in Part}
    tr, va, te = idx[Part.TRAIN], idx[Part.VALIDATION], idx[Part.TEST]
    print(f"prepared {time.monotonic() - t0:.0f}s", flush=True)

    out: dict[str, Any] = {
        "criteria": {"roc_auc_within": [0.47, 0.53], "pr_auc_max_x_base": 2.0},
        "split_hash": split.spec_hash(),
        "runs": [],
    }
    base = float(y[te].mean())
    for ps in PERM_SEEDS:
        rng = np.random.default_rng(ps)
        y_tr = rng.permutation(y[tr])
        y_va = rng.permutation(y[va])
        scores, trees = fit_predict(feats.iloc[tr], y_tr, feats.iloc[va], y_va, feats.iloc[te])
        m = ranking_metrics(y[te], scores, [1000])
        ok = 0.47 <= m.roc_auc <= 0.53 and m.pr_auc <= 2.0 * base
        out["runs"].append(
            {
                "permutation_seed": ps,
                "trees": trees,
                "test_pr_auc": m.pr_auc,
                "test_roc_auc": m.roc_auc,
                "base_rate": base,
                "passes_criteria": bool(ok),
            }
        )
        print(
            f"perm {ps}: AP {m.pr_auc:.5f} ROC {m.roc_auc:.3f} base {base:.5f} pass={ok}",
            flush=True,
        )

    # evaluation-code smoke test: real-label model scores vs permuted TEST labels
    real_scores, _ = fit_predict(feats.iloc[tr], y[tr], feats.iloc[va], y[va], feats.iloc[te])
    real = ranking_metrics(y[te], real_scores, [1000])
    perm_te = np.random.default_rng(404).permutation(y[te])
    sm = ranking_metrics(perm_te, real_scores, [1000])
    out["positive_control_real_labels"] = {"test_pr_auc": real.pr_auc, "test_roc_auc": real.roc_auc}
    out["permuted_test_labels_with_real_model"] = {"pr_auc": sm.pr_auc, "roc_auc": sm.roc_auc}
    print(
        f"positive control (real labels): AP {real.pr_auc:.4f} ROC {real.roc_auc:.3f}", flush=True
    )
    print(
        f"real model vs permuted TEST labels: AP {sm.pr_auc:.5f} ROC {sm.roc_auc:.3f}", flush=True
    )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    res = ROOT / "experiments" / "results" / f"e1c_{stamp}.json"
    res.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"done {time.monotonic() - t0:.0f}s -> {res}", flush=True)


if __name__ == "__main__":
    main()
