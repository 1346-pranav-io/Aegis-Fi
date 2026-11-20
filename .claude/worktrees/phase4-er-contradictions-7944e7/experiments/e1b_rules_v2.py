"""E1b: rule pack v2 (D-26) evaluated next to v1 and B1 under the same split and metrics.

Procedure is fixed in decisions.md D-26 before this script was run. Test and stress are
evaluated once. Run from backend/ as the read-only app role:
  AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis \
  uv run python ../experiments/e1b_rules_v2.py
"""

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

from aegis.analytics.features import FEATURE_SET_VERSION, build_features
from aegis.analytics.rules_v2 import RULE_SPECS, apply_rules_v2, fire
from aegis.common.hashing import content_hash
from aegis.eval.metrics import ranking_metrics
from aegis.eval.splits import Part, load_split
from aegis.ml.registry import write_manifest
from aegis.ml.rules_fit import fit_rule_pack_v2
from aegis.store.frames import load_transactions
from aegis.store.labels import fetch_label_series

ROOT = Path(__file__).resolve().parents[1]
DATASET = "IBM-AML-HI-Small"
KS = {
    Part.VALIDATION: [100, 500, 1000, 5000],
    Part.TEST: [100, 500, 1000, 5000],
    Part.STRESS: [10, 50, 100, 500],
}


def row(y: np.ndarray, s: np.ndarray, part: Part) -> dict[str, Any]:
    m = ranking_metrics(y, s, KS[part])
    return {
        "n": m.n,
        "positives": m.positives,
        "base_rate": m.base_rate,
        "pr_auc": m.pr_auc,
        "roc_auc": m.roc_auc,
        "recall_at_k": {str(k): v for k, v in m.recall_at_k.items()},
        "precision_at_k": {str(k): v for k, v in m.precision_at_k.items()},
        "distinct_scores": int(len(np.unique(s))),
    }


def main() -> None:
    t0 = time.monotonic()
    split = load_split(ROOT / "experiments" / "configs" / "split_ibm_hi_small_v1.json")
    with psycopg.connect(os.environ["AEGIS_APP_URL"]) as conn:
        cur = conn.execute("SELECT clock_timestamp()").fetchone()
        if cur is None:
            raise RuntimeError("no clock")
        as_of_ts: datetime = cur[0]
        batches = conn.execute(
            "SELECT batch_id, file_hashes FROM source.ingestion_batch "
            "WHERE dataset_name=%s AND status='COMPLETE' ORDER BY batch_id",
            (DATASET,),
        ).fetchall()
        raw = load_transactions(conn, as_of_ts)
        labels = fetch_label_series(conn, DATASET)
    manifest_hash = content_hash({"batches": [[b, h] for b, h in batches], "rows": len(raw)})
    feats = build_features(raw)
    y = labels.reindex(raw["ref"]).to_numpy().astype(np.int64)
    when = pd.to_datetime(raw["t"], unit="s", utc=True).dt.floor("h")
    hour_part = {ts: split.part_of(ts.to_pydatetime()) for ts in when.unique()}
    part_of = when.map(hour_part)
    idx = {p: np.flatnonzero((part_of == p).to_numpy()) for p in Part}
    tr, va, te, st = idx[Part.TRAIN], idx[Part.VALIDATION], idx[Part.TEST], idx[Part.STRESS]
    print(f"prepared in {time.monotonic() - t0:.0f}s", flush=True)

    pack = fit_rule_pack_v2(feats.iloc[tr], y[tr])  # TRAIN labels only
    print("fitted:", json.dumps(pack.as_dict()), flush=True)
    scored = apply_rules_v2(feats, pack)
    s = scored["score"].to_numpy()

    results: dict[str, Any] = {
        "dataset": DATASET,
        "as_of": as_of_ts.isoformat(),
        "split_hash": split.spec_hash(),
        "dataset_manifest_hash": manifest_hash,
        "feature_set_version": FEATURE_SET_VERSION,
        "rule_pack": pack.as_dict(),
        "metrics": {
            "validation": row(y[va], s[va], Part.VALIDATION),
            "test": row(y[te], s[te], Part.TEST),
            "stress": row(y[st], s[st], Part.STRESS),
        },
    }
    per_rule: dict[str, Any] = {}
    specs = {sp.name: sp for sp in RULE_SPECS}
    for r in pack.rules:
        if r.threshold is None:
            per_rule[r.name] = {"enabled": False}
            continue
        m = fire(feats, specs[r.name], r.threshold)
        per_rule[r.name] = {
            "enabled": True,
            "threshold": r.threshold,
            "weight": r.weight,
            "train_fires": int(m[tr].sum()),
            "test_fires": int(m[te].sum()),
            "test_precision": float(y[te][m[te]].mean()) if m[te].any() else None,
            "test_recall": float(y[te][m[te]].sum() / y[te].sum()),
        }
    results["per_rule"] = per_rule

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "experiments" / "artefacts" / f"rulesv2_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    pack_path = out_dir / "rule_pack_v2.json"
    pack_path.write_text(json.dumps(pack.as_dict(), indent=2), encoding="utf-8")
    mlflow.set_tracking_uri(f"sqlite:///{(ROOT / 'experiments' / 'mlflow.db').as_posix()}")
    mlflow.set_experiment("E1-detection-baselines")
    with mlflow.start_run(run_name="E1b-rules-v2") as run:
        mlflow.log_params({"split_hash": results["split_hash"][:16], "procedure": "D-26"})
        mlflow.log_metrics(
            {
                "val_prauc": results["metrics"]["validation"]["pr_auc"],
                "test_prauc": results["metrics"]["test"]["pr_auc"],
            }
        )
        write_manifest(
            pack_path,
            name="rules_v2",
            version=pack.version,
            kind="RULE_PACK",
            feature_set_version=FEATURE_SET_VERSION,
            split_hash=results["split_hash"],
            dataset_manifest_hash=manifest_hash,
            mlflow_run_id=run.info.run_id,
        )
        results["mlflow_run_id"] = run.info.run_id
    res = ROOT / "experiments" / "results" / f"e1b_{stamp}.json"
    res.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    print(f"done {time.monotonic() - t0:.0f}s -> {res}", flush=True)
    for p in ("validation", "test", "stress"):
        m = results["metrics"][p]
        print(p, f"AP {m['pr_auc']:.5f} ROC {m['roc_auc']:.3f} base {m['base_rate']:.5f}", flush=True)


if __name__ == "__main__":
    main()
