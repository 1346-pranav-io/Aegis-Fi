"""E1d: follow-up to E1c, whose pre-registered criteria were met by only 1 of 3 permutations.

Hypothesis (NOT yet verified): E1c's criterion was badly designed. A model fit on shuffled TRAIN
labels still outputs an arbitrary function of the features, and the REAL test labels are strongly
related to the features, so such a model can land well away from ROC 0.5 in either direction.

Test of that hypothesis, criteria fixed before running:
  (1) Per-feature univariate test ROC-AUC against REAL test labels: if several features are far from
      0.5, an arbitrary feature-driven score can easily miss the E1c band.
  (2) Full permutation: permute labels independently in train, validation AND test, retrain, and
      evaluate against the permuted test labels. Features are then independent of labels by
      construction, so we expect ROC within 0.5 +- 0.03 and PR-AUC <= 2 x base rate for all seeds.
This is a check of the machinery (features -> model -> metrics), not of generator artefacts.

Run from backend/ as the read-only app role:
  AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis \
  uv run python ../experiments/e1d_full_permutation.py
"""

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e1c_shuffled_labels import DATASET, ROOT, fit_predict  # noqa: E402

from aegis.analytics.features import FEATURE_NAMES, build_features  # noqa: E402
from aegis.eval.metrics import ranking_metrics  # noqa: E402
from aegis.eval.splits import Part, load_split  # noqa: E402
from aegis.store.frames import load_transactions  # noqa: E402
from aegis.store.labels import fetch_label_series  # noqa: E402


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

    # (1) univariate AUC of each feature against real test labels (NaN -> median of feature)
    uni: dict[str, float] = {}
    for name in FEATURE_NAMES:
        x = feats.iloc[te][name].to_numpy(np.float64)
        x = np.where(np.isnan(x), np.nanmedian(x), x)
        uni[name] = float(roc_auc_score(y[te], x))
    far = {k: round(v, 3) for k, v in uni.items() if abs(v - 0.5) > 0.1}
    print("features with |AUC-0.5| > 0.1 vs real test labels:", far, flush=True)

    # (2) full permutation
    out: dict[str, Any] = {"univariate_test_auc": uni, "full_permutation": []}
    for ps in (101, 202, 303):
        rng = np.random.default_rng(ps)
        y_tr, y_va, y_te = rng.permutation(y[tr]), rng.permutation(y[va]), rng.permutation(y[te])
        scores, trees = fit_predict(feats.iloc[tr], y_tr, feats.iloc[va], y_va, feats.iloc[te])
        m = ranking_metrics(y_te, scores, [1000])
        base = float(y_te.mean())
        ok = 0.47 <= m.roc_auc <= 0.53 and m.pr_auc <= 2.0 * base
        out["full_permutation"].append(
            {
                "permutation_seed": ps,
                "trees": trees,
                "pr_auc": m.pr_auc,
                "roc_auc": m.roc_auc,
                "base_rate": base,
                "passes": bool(ok),
            }
        )
        print(f"full perm {ps}: AP {m.pr_auc:.5f} ROC {m.roc_auc:.3f} base {base:.5f} pass={ok}", flush=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    res = ROOT / "experiments" / "results" / f"e1d_{stamp}.json"
    res.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"done {time.monotonic() - t0:.0f}s -> {res}", flush=True)


if __name__ == "__main__":
    main()
