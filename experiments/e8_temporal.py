"""E8: rolling-origin short-horizon temporal robustness on IBM HI-Small (RQ5, D-22, D-31).

Model-free (no LLM). Day-level windows over Sep 1-10 only (the 59%-positive stress slice, Sep 11-18,
is NOT used). For each origin day o: train on days < o, validate (early stopping, isotonic fit) on
day o, evaluate on every later day up to day 9. Systems: B0 rules v1 (thresholds from unlabeled
train features), B1 XGBoost (3 seeds), B1-intrinsic (seed 11). Nothing is tuned on an eval day.

Reported per cell (origin, system, eval day): PR-AUC with a row-bootstrap interval, recall and
precision at k, investigation-level proxies (pattern-block hit@k; account-level AP), calibration
error, drift (PSI). Analysis: horizon trend net of day effects vs window noise, drift vs
performance change, rank agreement of detection-level and investigation-level metrics (H5a).

Run from backend/:
  AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis \
  uv run python ../experiments/e8_temporal.py
"""

import csv
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e1_detection import INTRINSIC, code_hash, predict, train_xgb  # noqa: E402

from aegis.analytics.features import FEATURE_NAMES, FEATURE_SET_VERSION, build_features  # noqa: E402
from aegis.analytics.rules import apply_rules, fit_thresholds  # noqa: E402
from aegis.common.hashing import content_hash  # noqa: E402
from aegis.eval.metrics import (  # noqa: E402
    expected_calibration_error,
    precision_at_k,
    recall_at_k,
)
from aegis.eval.temporal import (  # noqa: E402
    block_hit_at_k,
    bootstrap_ap,
    group_max,
    horizon_trend,
    psi,
    rank_correlation,
)
from aegis.store.frames import load_transactions  # noqa: E402
from aegis.store.labels import fetch_label_series  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ibm-aml"
DATASET = "IBM-AML-HI-Small"
DAY0 = int(datetime(2022, 9, 1, tzinfo=UTC).timestamp())
LAST_DAY = 9  # Sep 10 inclusive
ORIGINS = [2, 3, 4, 5, 6]
SEEDS = [11, 22, 33]
KS = [500, 1000]
N_BOOT = 100


def parse_blocks() -> list[tuple[str, list[tuple[str, ...]]]]:
    blocks: list[tuple[str, list[tuple[str, ...]]]] = []
    cur: list[tuple[str, ...]] | None = None
    kind = ""
    for line in (RAW / "HI-Small_Patterns.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("BEGIN"):
            kind = re.sub(r":.*", "", line.replace("BEGIN LAUNDERING ATTEMPT - ", ""))
            cur = []
        elif line.startswith("END"):
            if cur is None:
                raise ValueError("END without BEGIN")
            blocks.append((kind, cur))
            cur = None
        elif line.strip() and cur is not None:
            cur.append(tuple(next(csv.reader([line]))))
    return blocks


def assign_blocks(raw: pd.DataFrame, y: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Block id per transaction row (-1 = none) by exact (time, origin, dest, amount) match on
    laundering rows. Returns (ids, block lines matched, block lines total)."""
    code = {r: i for i, r in enumerate(raw.attrs["account_refs"])}
    t, o, d = (raw[c].to_numpy(np.int64) for c in ("t", "origin", "dest"))
    amt = np.round(raw["amount"].to_numpy(np.float64), 2)
    index: dict[tuple[int, int, int, float], list[int]] = {}
    for i in np.flatnonzero(y == 1):
        index.setdefault((int(t[i]), int(o[i]), int(d[i]), float(amt[i])), []).append(int(i))
    ids = np.full(len(raw), -1, dtype=np.int64)
    matched = total = 0
    for bi, (_, lines) in enumerate(parse_blocks()):
        for rw in lines:
            total += 1
            ts = int(datetime.strptime(rw[0], "%Y/%m/%d %H:%M").replace(tzinfo=UTC).timestamp())
            key = (
                ts,
                code.get(f"{rw[1]}|{rw[2]}", -1),
                code.get(f"{rw[3]}|{rw[4]}", -1),
                round(float(rw[7]), 2),
            )
            cand = [i for i in index.get(key, []) if ids[i] < 0]
            if cand:
                ids[cand[0]] = bi
                matched += 1
    return ids, matched, total


def cell(
    y: np.ndarray, s: np.ndarray, block: np.ndarray, acct: np.ndarray, seed: int
) -> dict[str, Any]:
    ap, lo, hi = bootstrap_ap(y, s, N_BOOT, seed)
    out: dict[str, Any] = {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "pr_auc": ap,
        "pr_auc_lo": lo,
        "pr_auc_hi": hi,
        "recall_at_k": {str(k): recall_at_k(y, s, k) for k in KS},
        "precision_at_k": {str(k): precision_at_k(y, s, k) for k in KS},
    }
    hits = {k: block_hit_at_k(s, block, k) for k in KS}
    out["blocks_present"] = hits[KS[0]][1]
    out["block_hit_at_k"] = {str(k): (h / n if n else None) for k, (h, n) in hits.items()}
    gs, gy = group_max(acct, s, y)
    out["account_ap"] = float(average_precision_score(gy, gs)) if gy.sum() > 0 else None
    out["account_positives"] = int(gy.sum())
    return out


def main() -> None:
    t0 = time.monotonic()
    with psycopg.connect(os.environ["AEGIS_APP_URL"]) as conn:
        as_of = conn.execute("SELECT clock_timestamp()").fetchone()
        assert as_of is not None
        batches = conn.execute(
            "SELECT batch_id, file_hashes FROM source.ingestion_batch "
            "WHERE dataset_name=%s AND status='COMPLETE' ORDER BY batch_id",
            (DATASET,),
        ).fetchall()
        raw = load_transactions(conn, as_of[0])
        labels = fetch_label_series(conn, DATASET)
    manifest_hash = content_hash({"batches": [[b, h] for b, h in batches], "rows": len(raw)})
    feats = build_features(raw)
    y_all = labels.reindex(raw["ref"]).to_numpy().astype(np.int64)
    day = (raw["t"].to_numpy(np.int64) - DAY0) // 86400
    block_all, matched, total = assign_blocks(raw, y_all)
    print(
        f"loaded+features {time.monotonic() - t0:.0f}s; block lines matched {matched}/{total}",
        flush=True,
    )

    keep = np.flatnonzero((day >= 0) & (day <= LAST_DAY))
    feats = feats.iloc[keep].reset_index(drop=True)
    y, day, block = y_all[keep], day[keep], block_all[keep]
    acct = raw["origin"].to_numpy(np.int64)[keep]
    day_n = {
        int(d): {"n": int((day == d).sum()), "pos": int(y[day == d].sum())}
        for d in range(LAST_DAY + 1)
    }
    print("days:", day_n, flush=True)

    cells: list[dict[str, Any]] = []
    rng = np.random.default_rng(0)

    def add(
        system: str,
        o: int,
        seed: int | None,
        val_ap: float,
        scores: dict[int, np.ndarray],
        extra: dict[int, dict[str, Any]],
    ) -> None:
        for d, s in scores.items():
            m = day == d
            c = cell(y[m], s, block[m], acct[m], 1000 * o + d + (seed or 0))
            c.update(
                {
                    "system": system,
                    "origin": o,
                    "eval_day": d,
                    "horizon": d - o,
                    "seed": seed,
                    "val_pr_auc": val_ap,
                    **extra.get(d, {}),
                }
            )
            cells.append(c)

    for o in ORIGINS:
        tr = np.flatnonzero(day < o)
        va = np.flatnonzero(day == o)
        ev_idx = {d: np.flatnonzero(day == d) for d in range(o + 1, LAST_DAY + 1)}
        ref_sample = rng.choice(tr, size=min(len(tr), 200_000), replace=False)
        # ---- B0 rules (no labels used)
        pack = fit_thresholds(feats.iloc[tr])
        b0 = {
            d: apply_rules(feats.iloc[i], pack)["rules_fired"].to_numpy(np.float64)
            for d, i in ev_idx.items()
        }
        b0v = apply_rules(feats.iloc[va], pack)["rules_fired"].to_numpy(np.float64)
        b0_val = float(average_precision_score(y[va], b0v)) if y[va].sum() else float("nan")
        add("B0_rules", o, None, b0_val, b0, {})
        # ---- B1 and the intrinsic-feature ablation
        for name, cols, seeds in [
            ("B1_xgboost", list(FEATURE_NAMES), SEEDS),
            ("B1_intrinsic", INTRINSIC, [11]),
        ]:
            for seed in seeds:
                bst = train_xgb(feats.iloc[tr][cols], y[tr], feats.iloc[va][cols], y[va], seed)
                sv = predict(bst, feats.iloc[va][cols])
                val_ap = float(average_precision_score(y[va], sv))
                iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(
                    sv, y[va]
                )
                scores: dict[int, np.ndarray] = {}
                extra: dict[int, dict[str, Any]] = {}
                for d, i in ev_idx.items():
                    s = predict(bst, feats.iloc[i][cols])
                    cs = np.asarray(iso.predict(s), dtype=np.float64)
                    scores[d] = s
                    ps = [
                        psi(feats.iloc[ref_sample][c].to_numpy(), feats.iloc[i][c].to_numpy())
                        for c in cols
                    ]
                    extra[d] = {
                        "psi_score": psi(sv, s),
                        "psi_feature_mean": float(np.mean(ps)),
                        "psi_feature_max": float(np.max(ps)),
                        "ece_raw_NOT_A_PROBABILITY": expected_calibration_error(y[i], s),
                        "ece_isotonic": expected_calibration_error(y[i], cs),
                        "mean_calibrated": float(cs.mean()),
                        "base_rate": float(y[i].mean()),
                    }
                add(name, o, seed, val_ap, scores, extra)
                print(f"origin {o} {name} seed {seed} done {time.monotonic() - t0:.0f}s", flush=True)

    # ------------------------------------------------------------------ analysis
    df = pd.DataFrame(
        [
            {k: v for k, v in c.items() if not isinstance(v, dict)}
            | {"recall_1000": c["recall_at_k"]["1000"], "block_hit_1000": c["block_hit_at_k"]["1000"]}
            for c in cells
        ]
    )
    analysis: dict[str, Any] = {}
    for system, g in df.groupby("system"):
        m = g.groupby(["origin", "eval_day"])["pr_auc"].mean()
        trend = horizon_trend({(int(o), int(d)): float(v) for (o, d), v in m.items()})
        spread = (
            float(g.groupby(["origin", "eval_day"])["pr_auc"].std(ddof=1).mean())
            if g["seed"].nunique() > 1
            else None
        )
        analysis[str(system)] = {
            "horizon_trend": trend,
            "mean_bootstrap_ci_width": float((g["pr_auc_hi"] - g["pr_auc_lo"]).mean()),
            "sd_of_day_means": float(g.groupby("eval_day")["pr_auc"].mean().std(ddof=1)),
            "mean_pr_auc": float(g["pr_auc"].mean()),
            "seed_sd_pr_auc_mean": spread,
            "n_cells": int(len(g)),
        }
    b1 = df[df["system"] == "B1_xgboost"].copy()
    b1["delta_vs_val"] = b1["pr_auc"] - b1["val_pr_auc"]
    drift = {
        "spearman_psi_score_vs_delta": rank_correlation(b1["psi_score"], b1["delta_vs_val"]),
        "spearman_psi_feature_mean_vs_delta": rank_correlation(
            b1["psi_feature_mean"], b1["delta_vs_val"]
        ),
        "spearman_horizon_vs_delta": rank_correlation(b1["horizon"], b1["delta_vs_val"]),
        "n": int(len(b1)),
    }
    cal = {
        "ece_isotonic_by_horizon": {
            int(h): float(v) for h, v in b1.groupby("horizon")["ece_isotonic"].mean().items()
        },
        "ece_raw_by_horizon": {
            int(h): float(v)
            for h, v in b1.groupby("horizon")["ece_raw_NOT_A_PROBABILITY"].mean().items()
        },
    }
    ok = df.dropna(subset=["account_ap", "block_hit_1000"])
    h5a = {
        "spearman_txn_prauc_vs_block_hit_1000": rank_correlation(
            ok["pr_auc"], ok["block_hit_1000"]
        ),
        "spearman_txn_prauc_vs_account_ap": rank_correlation(ok["pr_auc"], ok["account_ap"]),
        "spearman_txn_recall1000_vs_block_hit_1000": rank_correlation(
            ok["recall_1000"], ok["block_hit_1000"]
        ),
        "n_cells": int(len(ok)),
        "system_means": {
            str(s): {
                "txn_pr_auc": float(g["pr_auc"].mean()),
                "block_hit_1000": float(g["block_hit_1000"].mean()),
                "account_ap": float(g["account_ap"].mean()),
            }
            for s, g in ok.groupby("system")
        },
    }
    results = {
        "experiment": "E8-temporal-rolling-origin",
        "dataset": DATASET,
        "as_of": as_of[0].isoformat(),
        "dataset_manifest_hash": manifest_hash,
        "feature_set_version": FEATURE_SET_VERSION,
        "code_hash": code_hash(),
        "days": day_n,
        "origins": ORIGINS,
        "seeds": SEEDS,
        "n_boot": N_BOOT,
        "block_lines_matched": matched,
        "block_lines_total": total,
        "cells": cells,
        "analysis": analysis,
        "drift_vs_performance": drift,
        "calibration_by_horizon": cal,
        "h5a_rank_agreement": h5a,
        "notes": [
            "bootstrap resamples rows independently: intervals are lower bounds on noise",
            "horizon is confounded with training size (earlier origin = less history)",
            "only in-block laundering has an investigation-level proxy (62% of laundering rows)",
            "synthetic data; stress slice Sep 11-18 not used",
        ],
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = ROOT / "experiments" / "results" / f"e8_{stamp}.json"
    path.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    summary = {"analysis": analysis, "drift": drift, "h5a": h5a, "cal": cal}
    print(json.dumps(summary, indent=2, default=float))
    print(f"done {time.monotonic() - t0:.0f}s -> {path}", flush=True)


if __name__ == "__main__":
    main()
