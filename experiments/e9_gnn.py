"""E9: GNN versus the XGBoost baseline on IBM HI-Small (D-20, D-32). Needs `uv sync --group gnn`.

Design (fixed before the run):
* Same D-24 windows as E1 by day: train days 0-5 (Sep 1-6), validation days 6-7, test days 8-9.
  The Sep 11-18 stress slice is not used.
* Causal snapshots: a transaction on day d is scored with node embeddings computed on the graph of
  transactions strictly before the START of day d (`aegis.ml.graph_snapshot`). So the GNN does not
  see earlier transactions of the same day, which B1's point-in-time features do. This handicaps
  the GNN relative to B1; the comparison is reported with that caveat.
* Model: 2-layer GraphSAGE (mean) on 5 node features -> 64-d node embeddings; edge head MLP on
  [h_src, h_dst, transaction features]. Loss: BCE on all positives plus a fresh random sample of
  negatives per day and epoch, positive weight = sampled negatives / positives (balanced). Early stopping on validation PR-AUC.
* Variants (3 seeds each): G0 / G0i = head on all / intrinsic transaction features, NO graph
  (isolate what the graph adds); G1 = GNN + intrinsic features only; G2 = GNN + all features.
  Max 100 epochs, patience 8 (a run that hits the cap may be under-trained; epochs are stored).
* B1 reference numbers are read from the stored E1 result (not re-run). Nothing is tuned on test.
* No model artefact is saved or activated (invariant 5).

Run from backend/:
  AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis \
  uv run python ../experiments/e9_gnn.py
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
import torch
from sklearn.metrics import average_precision_score
from torch import nn
from torch_geometric.nn import SAGEConv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e1_detection import INTRINSIC, code_hash  # noqa: E402

from aegis.analytics.features import FEATURE_NAMES, FEATURE_SET_VERSION, build_features  # noqa: E402
from aegis.common.hashing import content_hash  # noqa: E402
from aegis.eval.metrics import ranking_metrics  # noqa: E402
from aegis.ml.graph_snapshot import NODE_FEATURES, build_snapshot  # noqa: E402
from aegis.store.frames import load_transactions  # noqa: E402
from aegis.store.labels import fetch_label_series  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATASET = "IBM-AML-HI-Small"
DAY0 = int(datetime(2022, 9, 1, tzinfo=UTC).timestamp())
TRAIN_DAYS, VAL_DAYS, TEST_DAYS = [1, 2, 3, 4, 5], [6, 7], [8, 9]  # day 0 has an empty snapshot
SEEDS = [11, 22, 33]
MAX_EPOCHS, PATIENCE, NEG_PER_DAY, HIDDEN = 100, 8, 60_000, 64
KS = [100, 500, 1000, 5000]
DEV = torch.device("cuda" if torch.cuda.is_available() else "cpu")
CATEGORICAL = ["fmt", "currency"]


def edge_matrix(feats: pd.DataFrame, cols: list[str]) -> np.ndarray:
    """Signed-log transform, one-hot for the categorical codes; standardised later on train rows."""
    num = [c for c in cols if c not in CATEGORICAL]
    x = feats[num].to_numpy(np.float64)
    x = np.where(np.isinf(x), np.nan, x)
    missing = np.isnan(x)
    flag_cols = np.flatnonzero(missing.any(axis=0))  # columns with undefined values (no history)
    x = np.nan_to_num(x, nan=0.0)
    x = np.sign(x) * np.log1p(np.abs(x))
    parts = [x, missing[:, flag_cols].astype(np.float64)]
    for c in cols:
        if c in CATEGORICAL:
            parts.append(pd.get_dummies(feats[c].astype("int64"), dtype=np.float64).to_numpy())
    return np.concatenate(parts, axis=1)


class Net(nn.Module):
    def __init__(self, n_edge_feat: int, use_graph: bool) -> None:
        super().__init__()
        self.use_graph = use_graph
        self.c1 = SAGEConv(len(NODE_FEATURES), HIDDEN)
        self.c2 = SAGEConv(HIDDEN, HIDDEN)
        d_in = n_edge_feat + (2 * HIDDEN if use_graph else 0)
        self.head = nn.Sequential(
            nn.Linear(d_in, 128), nn.ReLU(), nn.Dropout(0.1), nn.Linear(128, 64), nn.ReLU(),
            nn.Linear(64, 1),
        )  # fmt: skip

    def embed(self, x: torch.Tensor, ei: torch.Tensor) -> torch.Tensor:
        h = torch.relu(self.c1(x, ei))
        return torch.relu(self.c2(h, ei))

    def score(self, h: torch.Tensor | None, s: torch.Tensor, d: torch.Tensor, ef: torch.Tensor):  # noqa: ANN201
        z = torch.cat([h[s], h[d], ef], dim=1) if self.use_graph and h is not None else ef
        return self.head(z).squeeze(1)


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
    t = raw["t"].to_numpy(np.int64)
    day = (t - DAY0) // 86400
    keep = np.flatnonzero((day >= 0) & (day <= max(TEST_DAYS)))
    n_nodes = int(max(raw["origin"].max(), raw["dest"].max())) + 1
    src, dst = raw["origin"].to_numpy(np.int64), raw["dest"].to_numpy(np.int64)
    amt = raw["amount"].to_numpy(np.float64)
    print(f"loaded {time.monotonic() - t0:.0f}s nodes {n_nodes:,}", flush=True)

    # causal snapshots for each scored day, moved to the device once
    snaps: dict[int, tuple[torch.Tensor, torch.Tensor]] = {}
    snap_edges: dict[int, int] = {}
    for d in TRAIN_DAYS + VAL_DAYS + TEST_DAYS:
        sn = build_snapshot(t[keep], src[keep], dst[keep], amt[keep], n_nodes, DAY0 + 86400 * d)
        snaps[d] = (
            torch.from_numpy(sn.x).to(DEV),
            torch.from_numpy(np.stack([sn.src, sn.dst])).to(DEV),
        )
        snap_edges[d] = sn.n_edges
    print("snapshot edges:", snap_edges, flush=True)

    rows_of = {d: np.flatnonzero(day == d) for d in TRAIN_DAYS + VAL_DAYS + TEST_DAYS}
    train_rows = np.concatenate([rows_of[d] for d in TRAIN_DAYS])
    results: dict[str, Any] = {
        "experiment": "E9-gnn-vs-baseline", "dataset": DATASET, "as_of": as_of[0].isoformat(),
        "dataset_manifest_hash": manifest_hash, "feature_set_version": FEATURE_SET_VERSION,
        "code_hash": code_hash(), "device": str(DEV), "torch": torch.__version__,
        "train_days": TRAIN_DAYS, "val_days": VAL_DAYS, "test_days": TEST_DAYS,
        "snapshot_edges": snap_edges, "seeds": SEEDS, "variants": {},
        "notes": [
            "snapshots exclude same-day history: stricter than B1's point-in-time features",
            "GPU scatter ops are not bit-deterministic; seeds fix initialisation and sampling only",
            "single dataset, synthetic; no model artefact saved or activated",
        ],
    }  # fmt: skip

    variants = [
        ("G0_head_all_features_no_graph", list(FEATURE_NAMES), False),
        ("G0i_head_intrinsic_features_no_graph", INTRINSIC, False),
        ("G1_gnn_intrinsic_features", INTRINSIC, True),
        ("G2_gnn_all_features", list(FEATURE_NAMES), True),
    ]
    for name, cols, use_graph in variants:
        em = edge_matrix(feats, cols)
        mu, sd = em[train_rows].mean(0), np.maximum(em[train_rows].std(0), 1e-3)
        ef_np = np.clip((em - mu) / sd, -10.0, 10.0).astype(np.float32)  # no extreme inputs
        if not np.isfinite(ef_np).all():
            raise RuntimeError(f'{name}: non-finite transaction features')
        ef_all = torch.from_numpy(ef_np)
        ysrc = torch.from_numpy(y_all)
        runs = []
        for seed in SEEDS:
            torch.manual_seed(seed)
            rng = np.random.default_rng(seed)
            net = Net(ef_all.shape[1], use_graph).to(DEV)
            opt = torch.optim.Adam(net.parameters(), lr=3e-3, weight_decay=1e-5)

            def score_day(d: int, rows: np.ndarray, train: bool) -> torch.Tensor:
                h = net.embed(*snaps[d]) if use_graph else None
                idx = torch.from_numpy(rows)
                s = torch.from_numpy(src[rows]).to(DEV)
                dd = torch.from_numpy(dst[rows]).to(DEV)
                return net.score(h, s, dd, ef_all[idx].to(DEV))

            def evaluate(days: list[int]) -> tuple[np.ndarray, np.ndarray]:
                net.eval()
                ys, ps = [], []
                with torch.no_grad():
                    for d in days:
                        rows = rows_of[d]
                        h = net.embed(*snaps[d]) if use_graph else None
                        for a in range(0, len(rows), 200_000):
                            r = rows[a : a + 200_000]
                            idx = torch.from_numpy(r)
                            p = net.score(
                                h,
                                torch.from_numpy(src[r]).to(DEV),
                                torch.from_numpy(dst[r]).to(DEV),
                                ef_all[idx].to(DEV),
                            )
                            ps.append(p.cpu().numpy())
                            ys.append(y_all[r])
                return np.concatenate(ys), np.concatenate(ps)

            best, best_state, bad, epochs = -1.0, None, 0, 0
            for ep in range(MAX_EPOCHS):
                net.train()
                tot = 0.0
                for d in TRAIN_DAYS:
                    rows = rows_of[d]
                    pos = rows[y_all[rows] == 1]
                    neg_pool = rows[y_all[rows] == 0]
                    neg = rng.choice(neg_pool, size=min(NEG_PER_DAY, len(neg_pool)), replace=False)
                    sel = np.concatenate([pos, neg])
                    logit = score_day(d, sel, True)
                    target = ysrc[torch.from_numpy(sel)].float().to(DEV)
                    pw = torch.tensor(len(neg) / max(len(pos), 1))
                    loss = nn.functional.binary_cross_entropy_with_logits(
                        logit, target, pos_weight=pw.to(DEV)
                    )
                    opt.zero_grad()
                    loss.backward()
                    nn.utils.clip_grad_norm_(net.parameters(), 5.0)
                    opt.step()
                    if not np.isfinite(loss.item()):
                        raise RuntimeError(f'{name} seed {seed}: non-finite loss at epoch {ep}')
                    tot += loss.item()
                yv, pv = evaluate(VAL_DAYS)
                ap = float(average_precision_score(yv, pv))
                epochs = ep + 1
                if ap > best:
                    best, bad = ap, 0
                    best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
                else:
                    bad += 1
                    if bad >= PATIENCE:
                        break
            assert best_state is not None
            net.load_state_dict(best_state)
            yv, pv = evaluate(VAL_DAYS)
            yt, pt = evaluate(TEST_DAYS)
            mv, mt = ranking_metrics(yv, pv, KS), ranking_metrics(yt, pt, KS)
            runs.append(
                {
                    "seed": seed, "epochs_run": epochs, "best_val_pr_auc": best,
                    "validation": {"pr_auc": mv.pr_auc, "roc_auc": mv.roc_auc,
                                   "recall_at_k": {str(k): v for k, v in mv.recall_at_k.items()}},
                    "test": {"n": mt.n, "positives": mt.positives, "pr_auc": mt.pr_auc,
                             "roc_auc": mt.roc_auc,
                             "recall_at_k": {str(k): v for k, v in mt.recall_at_k.items()},
                             "precision_at_k": {str(k): v for k, v in mt.precision_at_k.items()}},
                }  # fmt: skip
            )
            print(
                f"{name} seed {seed}: epochs {epochs} val AP {best:.4f} test AP {mt.pr_auc:.4f} "
                f"({time.monotonic() - t0:.0f}s)",
                flush=True,
            )
            del net, opt, best_state
            torch.cuda.empty_cache()
        tests = [r["test"]["pr_auc"] for r in runs]
        results["variants"][name] = {
            "features": cols, "uses_graph": use_graph, "runs": runs,
            "test_pr_auc_mean": float(np.mean(tests)), "test_pr_auc_sd": float(np.std(tests, ddof=1)),
        }  # fmt: skip

    # reference: B1 from the stored E1 run (computed on the same test window, same-day history allowed)
    e1 = json.loads(
        (ROOT / "experiments" / "results" / "e1_20261111T170703Z.json").read_text(encoding="utf-8")
    )
    b1_tests = [r["test"]["pr_auc"] for r in e1["systems"]["B1_xgboost"]["runs"]]
    results["reference_e1"] = {
        "file": "e1_20261111T170703Z.json",
        "b1_test_pr_auc_mean": float(np.mean(b1_tests)),
        "b1_test_pr_auc_sd": float(np.std(b1_tests, ddof=1)),
        "b1_intrinsic_test_pr_auc": e1["systems"]["B1_intrinsic_only"]["test"]["pr_auc"],
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = ROOT / "experiments" / "results" / f"e9_{stamp}.json"
    path.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    summary = {k: (v["test_pr_auc_mean"], v["test_pr_auc_sd"]) for k, v in results["variants"].items()}
    print(json.dumps({"summary_test_pr_auc": summary, "reference": results["reference_e1"]}, indent=2))
    print(f"done {time.monotonic() - t0:.0f}s -> {path}", flush=True)


if __name__ == "__main__":
    main()
