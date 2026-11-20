"""E1: transaction-level detection baselines on IBM HI-Small (D-24 split, D-25 unit).

Systems: B0 = rule pack v1 (rules fired); B1 = XGBoost on point-in-time features (3 seeds);
B1-intrinsic = XGBoost on transaction-intrinsic features only (diagnostic ablation).
Nothing here is tuned on the test window. Test/stress are touched once per run.

Run from backend/ as the read-only app role:
  AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis \
  uv run python ../experiments/e1_detection.py
"""

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import psycopg
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

from aegis.analytics.features import FEATURE_NAMES, FEATURE_SET_VERSION, build_features
from aegis.analytics.rules import RULE_NAMES, apply_rules, fit_thresholds
from aegis.common.hashing import content_hash
from aegis.eval.metrics import brier, expected_calibration_error, ranking_metrics
from aegis.eval.splits import Part, load_split
from aegis.ml.registry import write_manifest
from aegis.store.frames import load_transactions
from aegis.store.labels import fetch_label_series

ROOT = Path(__file__).resolve().parents[1]
DATASET = "IBM-AML-HI-Small"
SEEDS = [11, 22, 33]
INTRINSIC = [
    "log_amount", "same_bank", "self_transfer", "cross_currency", "recv_over_paid", "fmt",
    "currency", "hour_of_day",
]  # fmt: skip
XGB_PARAMS: dict[str, Any] = {
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
KS = {Part.VALIDATION: [100, 500, 1000, 5000], Part.TEST: [100, 500, 1000, 5000],
      Part.STRESS: [10, 50, 100, 500]}  # fmt: skip


def code_hash() -> str:
    h = hashlib.sha256()
    for p in sorted((ROOT / "backend" / "src" / "aegis").rglob("*.py")):
        h.update(p.relative_to(ROOT).as_posix().encode() + p.read_bytes())
    h.update((ROOT / "experiments" / "e1_detection.py").read_bytes())
    return h.hexdigest()


def metrics_row(y: np.ndarray, s: np.ndarray, part: Part) -> dict[str, Any]:
    m = ranking_metrics(y, s, KS[part])
    return {
        "n": m.n, "positives": m.positives, "base_rate": m.base_rate, "pr_auc": m.pr_auc,
        "roc_auc": m.roc_auc,
        "recall_at_k": {str(k): v for k, v in m.recall_at_k.items()},
        "precision_at_k": {str(k): v for k, v in m.precision_at_k.items()},
    }  # fmt: skip


def train_xgb(
    x: pd.DataFrame, y: np.ndarray, xv: pd.DataFrame, yv: np.ndarray, seed: int
) -> xgb.Booster:
    params = {**XGB_PARAMS, "seed": seed, "scale_pos_weight": float((y == 0).sum() / (y == 1).sum())}
    dtr = xgb.DMatrix(x, label=y, feature_names=list(x.columns))
    dva = xgb.DMatrix(xv, label=yv, feature_names=list(x.columns))
    return xgb.train(params, dtr, 400, evals=[(dva, "val")], early_stopping_rounds=30,
                     verbose_eval=False)  # fmt: skip


def predict(b: xgb.Booster, x: pd.DataFrame) -> np.ndarray:
    d = xgb.DMatrix(x, feature_names=list(x.columns))
    return np.asarray(b.predict(d, iteration_range=(0, b.best_iteration + 1)), dtype=np.float64)


def main() -> None:
    t0 = time.monotonic()
    split = load_split(ROOT / "experiments" / "configs" / "split_ibm_hi_small_v1.json")
    with psycopg.connect(os.environ["AEGIS_APP_URL"]) as conn:
        as_of = conn.execute("SELECT clock_timestamp()").fetchone()
        assert as_of is not None
        as_of_ts: datetime = as_of[0]
        batches = conn.execute(
            "SELECT batch_id, file_hashes FROM source.ingestion_batch "
            "WHERE dataset_name=%s AND status='COMPLETE' ORDER BY batch_id", (DATASET,)
        ).fetchall()
        raw = load_transactions(conn, as_of_ts)
        labels = fetch_label_series(conn, DATASET)
    print(f"loaded {len(raw):,} rows in {time.monotonic() - t0:.0f}s", flush=True)
    manifest_hash = content_hash({"batches": [[b, h] for b, h in batches], "rows": len(raw)})

    feats = build_features(raw)  # point-in-time; contains no label information
    print(f"features built {time.monotonic() - t0:.0f}s", flush=True)
    y_all = labels.reindex(raw["ref"]).to_numpy()
    if np.isnan(y_all.astype(float)).any():
        raise RuntimeError("transactions without labels")
    y_all = y_all.astype(np.int64)
    when = pd.to_datetime(raw["t"], unit="s", utc=True)
    hour_part = {ts: split.part_of(ts.to_pydatetime()) for ts in when.dt.floor("h").unique()}
    part_of = when.dt.floor("h").map(hour_part)
    idx = {p: np.flatnonzero((part_of == p).to_numpy()) for p in Part}
    sizes = {p.value: {"n": int(len(i)), "pos": int(y_all[i].sum())} for p, i in idx.items()}
    print("parts:", sizes, flush=True)

    mlflow.set_tracking_uri(f"sqlite:///{(ROOT / 'experiments' / 'mlflow.db').as_posix()}")
    mlflow.set_experiment("E1-detection-baselines")
    out_dir = ROOT / "experiments" / "artefacts" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {
        "dataset": DATASET, "as_of": as_of_ts.isoformat(), "split_hash": split.spec_hash(),
        "dataset_manifest_hash": manifest_hash, "feature_set_version": FEATURE_SET_VERSION,
        "code_hash": code_hash(), "parts": sizes, "systems": {},
    }  # fmt: skip

    tr, va, te, st = idx[Part.TRAIN], idx[Part.VALIDATION], idx[Part.TEST], idx[Part.STRESS]

    with mlflow.start_run(run_name="E1") as run:
        mlflow.log_params({"split_hash": results["split_hash"][:16], "seeds": SEEDS,
                           "feature_set": FEATURE_SET_VERSION, "dataset_manifest": manifest_hash[:16]})  # fmt: skip

        # ---- B0: rules, thresholds from unlabeled TRAIN features only
        pack = fit_thresholds(feats.iloc[tr])
        fired = apply_rules(feats, pack)
        b0 = {p.value: metrics_row(y_all[i], fired["rules_fired"].to_numpy()[i], p)
              for p, i in [(Part.VALIDATION, va), (Part.TEST, te), (Part.STRESS, st)]}  # fmt: skip
        per_rule = {}
        for r in RULE_NAMES:
            m = fired[r].to_numpy()[te]
            per_rule[r] = {"fires_test": int(m.sum()),
                           "precision_test": float(y_all[te][m].mean()) if m.any() else None,
                           "recall_test": float(y_all[te][m].sum() / y_all[te].sum())}  # fmt: skip
        results["systems"]["B0_rules"] = {"rule_pack": pack.as_dict(), "metrics": b0, "per_rule_test": per_rule}
        print("B0 test:", {k: round(v, 5) if isinstance(v, float) else v for k, v in b0["test"].items() if k in ("pr_auc", "roc_auc")}, flush=True)  # fmt: skip

        # ---- B1: XGBoost x seeds, early stopping on validation
        b1_runs = []
        best: tuple[float, xgb.Booster, int] | None = None
        for seed in SEEDS:
            b = train_xgb(feats.iloc[tr], y_all[tr], feats.iloc[va], y_all[va], seed)
            sv, ste, sst = (predict(b, feats.iloc[i]) for i in (va, te, st))
            row = {"seed": seed, "best_iteration": int(b.best_iteration),
                   "validation": metrics_row(y_all[va], sv, Part.VALIDATION),
                   "test": metrics_row(y_all[te], ste, Part.TEST),
                   "stress": metrics_row(y_all[st], sst, Part.STRESS)}  # fmt: skip
            b1_runs.append(row)
            mlflow.log_metrics({f"b1_s{seed}_val_prauc": row["validation"]["pr_auc"],
                                f"b1_s{seed}_test_prauc": row["test"]["pr_auc"]})  # fmt: skip
            print(f"B1 seed {seed}: val AP {row['validation']['pr_auc']:.4f} "
                  f"test AP {row['test']['pr_auc']:.4f} iters {b.best_iteration} "
                  f"({time.monotonic() - t0:.0f}s)", flush=True)  # fmt: skip
            if best is None or row["validation"]["pr_auc"] > best[0]:
                best = (row["validation"]["pr_auc"], b, seed)
        assert best is not None
        _, bst, bseed = best
        results["systems"]["B1_xgboost"] = {"params": XGB_PARAMS, "runs": b1_runs,
                                             "selected_seed_by_validation": bseed}  # fmt: skip
        imp = bst.get_score(importance_type="gain")
        results["systems"]["B1_xgboost"]["gain_importance_selected"] = dict(
            sorted(imp.items(), key=lambda kv: -kv[1]))

        model_path = out_dir / f"b1_xgb_seed{bseed}.json"
        bst.save_model(str(model_path))
        mf = write_manifest(model_path, name="b1_xgboost", version=FEATURE_SET_VERSION,
                            kind="CLASSIFIER", feature_set_version=FEATURE_SET_VERSION,
                            features=list(FEATURE_NAMES), split_hash=results["split_hash"],
                            dataset_manifest_hash=manifest_hash, code_hash=results["code_hash"],
                            seed=bseed, params=XGB_PARAMS, mlflow_run_id=run.info.run_id)  # fmt: skip

        # ---- calibration (D-06): isotonic fitted on VALIDATION scores, judged on TEST / STRESS
        sv = predict(bst, feats.iloc[va])
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(sv, y_all[va])
        cal = {}
        for p, i in [(Part.TEST, te), (Part.STRESS, st)]:
            s = predict(bst, feats.iloc[i])
            c = np.asarray(iso.predict(s), dtype=np.float64)
            cal[p.value] = {
                "raw_score_ece_NOT_A_PROBABILITY": expected_calibration_error(y_all[i], s),
                "calibrated_ece": expected_calibration_error(y_all[i], c),
                "raw_brier": brier(y_all[i], s), "calibrated_brier": brier(y_all[i], c),
                "mean_calibrated": float(c.mean()), "base_rate": float(y_all[i].mean()),
            }  # fmt: skip
        results["systems"]["B1_xgboost"]["calibration_isotonic_on_validation"] = cal
        cal_path = out_dir / "b1_isotonic_calibrator.json"
        cal_path.write_text(json.dumps({"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}))  # fmt: skip
        write_manifest(cal_path, name="b1_isotonic", version="1", kind="CALIBRATOR",
                       split_hash=results["split_hash"], dataset_manifest_hash=manifest_hash,
                       code_hash=results["code_hash"], mlflow_run_id=run.info.run_id)  # fmt: skip

        # ---- diagnostic ablation: intrinsic features only (seed 11)
        bi = train_xgb(feats.iloc[tr][INTRINSIC], y_all[tr], feats.iloc[va][INTRINSIC], y_all[va], 11)
        results["systems"]["B1_intrinsic_only"] = {
            "features": INTRINSIC,
            "test": metrics_row(y_all[te], predict(bi, feats.iloc[te][INTRINSIC]), Part.TEST),
            "stress": metrics_row(y_all[st], predict(bi, feats.iloc[st][INTRINSIC]), Part.STRESS),
            "validation": metrics_row(y_all[va], predict(bi, feats.iloc[va][INTRINSIC]), Part.VALIDATION),
        }  # fmt: skip

        res_path = ROOT / "experiments" / "results" / f"e1_{out_dir.name}.json"
        res_path.parent.mkdir(exist_ok=True)
        res_path.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
        mlflow.log_artifact(str(res_path))
        mlflow.log_artifact(str(mf))
        print(f"done {time.monotonic() - t0:.0f}s -> {res_path}", flush=True)


if __name__ == "__main__":
    main()
