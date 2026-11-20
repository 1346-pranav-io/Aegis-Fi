"""Demo: open real cases from the stored B1 model's top scores on the Sep 9-10 test window.

Loads the IBM data from the local database, builds the point-in-time features, scores the test
window with the stored B1 XGBoost artefact (experiments/artefacts/.../b1_xgb_seed33.json, hash
checked against its manifest), and records the top-N transactions as alerts (one case per account).
Scores are scores, not probabilities. Labels are not read. This writes alerts and cases to the
LOCAL development database only.

Run from backend/: AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis \
  uv run python scripts/demo_seed.py [--top 25]
"""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import psycopg
import xgboost as xgb

from aegis.analytics.features import FEATURE_NAMES, build_features
from aegis.services.cases import ScoredItem, create_alerts
from aegis.store.frames import load_transactions

ROOT = Path(__file__).resolve().parents[2]
ARTEFACT = ROOT / "experiments" / "artefacts" / "20261111T180202Z"
WINDOW = (datetime(2022, 9, 9, tzinfo=UTC), datetime(2022, 9, 11, tzinfo=UTC))


def main(top: int) -> None:
    model_file = ARTEFACT / "b1_xgb_seed33.json"
    manifest = json.loads((ARTEFACT / "b1_xgb_seed33.json.manifest.json").read_text("utf-8"))
    digest = hashlib.sha256(model_file.read_bytes()).hexdigest()
    if digest != manifest["sha256"]:
        raise SystemExit("model file does not match its manifest hash; refusing to use it")
    bst = xgb.Booster()
    bst.load_model(str(model_file))

    with psycopg.connect(os.environ["AEGIS_APP_URL"]) as conn:
        as_of = conn.execute("SELECT clock_timestamp()").fetchone()
        if as_of is None:
            raise SystemExit("no database clock")
        raw = load_transactions(conn, as_of[0])
        feats = build_features(raw)
        t = raw["t"].to_numpy(np.int64)
        in_win = (t >= int(WINDOW[0].timestamp())) & (t < int(WINDOW[1].timestamp()))
        idx = np.flatnonzero(in_win)
        d = xgb.DMatrix(feats.iloc[idx][list(FEATURE_NAMES)], feature_names=list(FEATURE_NAMES))
        score = np.asarray(bst.predict(d), dtype=np.float64)
        order = np.argsort(-score, kind="stable")[:top]
        refs = raw.attrs["account_refs"]
        items = [
            ScoredItem(
                subject_ref=str(raw["ref"].iloc[idx[i]]),
                account_ref=refs[int(raw["origin"].iloc[idx[i]])],
                score=float(score[i]),
                reason_codes=("B1_TOP_SCORE",),
            )
            for i in order
        ]
        res = create_alerts(
            conn, items, signal_type="B1_XGBOOST", as_of=WINDOW[1],
            model_artefact_id=f"b1_xgboost:{digest[:16]}", window=WINDOW,
        )  # fmt: skip
        conn.commit()
    cases = {r.case_id for r in res}
    print(f"scored {len(idx):,} transactions; recorded {len(res)} alerts in {len(cases)} cases")
    print("top score (a score, not a probability):", round(items[0].score, 4))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    main(ap.parse_args().top)
