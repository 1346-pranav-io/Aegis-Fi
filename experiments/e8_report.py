"""E8 post-analysis: derived quantities used in docs/research/E8_TEMPORAL.md, computed from a stored
E8 result file and written next to it, so every number in the document traces to a stored file.

Run from backend/: uv run python ../experiments/e8_report.py ../experiments/results/e8_<stamp>.json
"""

import json
import sys
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr


def main(path: Path) -> None:
    d = json.loads(path.read_text(encoding="utf-8"))
    df = pd.DataFrame(
        [
            {k: v for k, v in c.items() if not isinstance(v, dict)}
            | {"block_hit_1000": c["block_hit_at_k"]["1000"], "recall_1000": c["recall_at_k"]["1000"]}
            for c in d["cells"]
        ]
    )
    b1 = df[df.system == "B1_xgboost"]
    matrix = b1.groupby(["origin", "eval_day"]).pr_auc.mean()
    out: dict[str, object] = {"source_file": path.name}

    out["b1_pr_auc_by_origin_and_day"] = {
        f"o{o}_d{day}": float(v) for (o, day), v in matrix.items()
    }
    val = b1.groupby("origin").val_pr_auc.mean()
    out["b1_validation_pr_auc_by_origin"] = {f"o{o}": float(v) for o, v in val.items()}
    # within one origin the training set is fixed: the spread over later days is day-to-day variation
    spread = {}
    for o, g in b1.groupby("origin"):
        m = g.groupby("eval_day").pr_auc.mean()
        spread[f"o{o}"] = {"min": float(m.min()), "max": float(m.max()), "range": float(m.max() - m.min())}
    out["b1_within_origin_pr_auc_spread"] = spread
    out["b1_mean_ci_width"] = float((b1.pr_auc_hi - b1.pr_auc_lo).mean())
    # day 8 (Sep 9) is lower for every origin: a day effect, not a horizon effect
    day8 = [float(matrix[(o, 8)] - matrix[(o, 7)]) for o in (2, 3, 4, 5, 6) if (o, 8) in matrix.index]
    out["b1_day8_minus_day7_by_origin"] = day8
    out["b1_day8_lower_for_all_origins"] = all(x < 0 for x in day8)
    out["b1_mean_pr_auc_by_horizon"] = {
        str(h): float(v) for h, v in b1.groupby("horizon").pr_auc.mean().items()
    }
    # horizon = eval_day - origin, so origin, day and horizon effects cannot be separated additively
    out["b1_mean_training_days_by_horizon"] = {
        str(h): float(g.origin.mean()) for h, g in b1.groupby("horizon")
    }

    ok = df.dropna(subset=["account_ap", "block_hit_1000"])
    out["within_system_spearman_prauc_vs_block_hit_1000"] = {
        s: float(spearmanr(g.pr_auc, g.block_hit_1000).statistic) for s, g in ok.groupby("system")
    }
    out["within_system_spearman_prauc_vs_account_ap"] = {
        s: float(spearmanr(g.pr_auc, g.account_ap).statistic) for s, g in ok.groupby("system")
    }
    # does every eval day order the three systems the same way under all three metrics?
    consistent = True
    for _, g in ok.groupby("eval_day"):
        m = g.groupby("system")[["pr_auc", "block_hit_1000", "account_ap"]].mean()
        orders = {tuple(m[c].sort_values().index) for c in m.columns}
        consistent &= len(orders) == 1
    out["system_order_same_under_all_metrics_every_day"] = bool(consistent)

    # calibration: mean calibrated score against the true base rate (ECE alone is dominated by negatives)
    ratio = {}
    for h, g in b1.groupby("horizon"):
        ratio[str(h)] = float((g.mean_calibrated / g.base_rate).mean())
    out["b1_mean_calibrated_over_base_rate_by_horizon"] = ratio
    out["b1_block_hit_1000_blocks_present_median"] = float(b1.blocks_present.median())

    target = path.with_name(path.name.replace("e8_", "e8_analysis_", 1))
    target.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    print("->", target)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
