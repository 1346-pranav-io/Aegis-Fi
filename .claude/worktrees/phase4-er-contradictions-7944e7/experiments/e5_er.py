"""E5: entity resolution - proposal-only vs auto-merge under seeded name perturbation.

Procedure is pre-registered in docs/PHASE4_NOTES.md section 0 (written before the first run).
Names: 2,000 distinct IBM `Entity Name` values (random.Random(0) sample, read-only). Mentions are
seeded perturbations of those names with ground truth (eval/perturbation.py, D-23). The auto-merge
arm is a SIMULATION: production code and the database refuse to auto-confirm a fuzzy link.

Run from backend/:
  AEGIS_IBM_ACCOUNTS=<path to HI-Small_accounts.csv> uv run python ../experiments/e5_er.py
Writes experiments/results/e5_er.json (git-ignored) and prints a table.
"""

import csv
import json
import os
import random
import statistics
import sys
from pathlib import Path
from typing import Any

from aegis.common.hashing import file_sha256
from aegis.entities.pipeline import (
    DEFAULT_PROPOSE_THRESHOLD,
    EntityIndex,
    EntityRecord,
    MentionRecord,
    propose_links,
)
from aegis.entities.similarity import NORMALISER_VERSION, SCORER_VERSION, normalise_name
from aegis.eval.perturbation import PERTURBATION_VERSION, generate_er_corpus

N_NAMES = 2000
RATES = (0.0, 0.1, 0.3, 0.5, 0.8)
SEEDS = (1, 2, 3, 4, 5)
TAUS: tuple[float | None, ...] = (None, 0.85, 0.90, 0.95)  # None = proposal-only (production)
if os.environ.get("AEGIS_E5_TAUS"):  # post-hoc sensitivity only
    TAUS = (None, *(float(x) for x in os.environ["AEGIS_E5_TAUS"].split(",")))
MENTIONS_PER_ENTITY = 2
DISTRACTOR_FRACTION = 0.2
# Pre-registered run uses the defaults. The two overrides exist only for the clearly labelled
# post-hoc sensitivity run (docs/PHASE4_NOTES.md); they change the output file name.
PROPOSE_THRESHOLD = float(os.environ.get("AEGIS_E5_PROPOSE_THRESHOLD", DEFAULT_PROPOSE_THRESHOLD))
# Post-hoc variant: the registry lacks the near-collision distractor entities, so a mention of a
# distractor has NO correct entity and any link for it is wrong (a new entity wrongly absorbed).
INCOMPLETE_REGISTRY = os.environ.get("AEGIS_E5_INCOMPLETE_REGISTRY") == "1"
OUT = Path(__file__).parent / "results" / os.environ.get("AEGIS_E5_OUT", "e5_er.json")


def load_names(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as f:
        names = sorted({row["Entity Name"] for row in csv.DictReader(f)})
    return random.Random(0).sample(names, N_NAMES)


def run_cell(names: list[str], seed: int, rate: float) -> dict[str, Any]:
    corpus = generate_er_corpus(
        names,
        seed=seed,
        perturbation_rate=rate,
        mentions_per_entity=MENTIONS_PER_ENTITY,
        distractor_fraction=DISTRACTOR_FRACTION,
    )
    entities = [
        EntityRecord(f"e{i}", normalise_name(n))
        for i, n in enumerate(corpus.entities)
        if not (INCOMPLETE_REGISTRY and i in corpus.distractor_of)
    ]
    index = EntityIndex(entities)
    top1: list[tuple[int, str, float] | None] = []  # (mention idx, entity id, score)
    blocking_hit = 0
    for m in corpus.mentions:
        norm = normalise_name(m.text)
        if f"e{m.true_entity_idx}" in index.candidates(norm) or (
            INCOMPLETE_REGISTRY and m.true_entity_idx in corpus.distractor_of
        ):
            blocking_hit += 1
        props = propose_links(
            MentionRecord(f"m{m.mention_idx}", norm),
            index,
            top_k=1,
            propose_threshold=PROPOSE_THRESHOLD,
        )
        top1.append((m.mention_idx, props[0].entity_id, props[0].match_score) if props else None)
    n = len(corpus.mentions)
    resolved = [(m, t) for m, t in zip(corpus.mentions, top1, strict=True) if t is not None]
    wrong = [(m, t) for m, t in resolved if t[1] != f"e{m.true_entity_idx}"]
    out: dict[str, Any] = {
        "seed": seed,
        "rate": rate,
        "mentions": n,
        "entities": len(corpus.entities),
        "changed_mentions": sum(m.changed for m in corpus.mentions),
        "blocking_recall": blocking_hit / n,
        "unresolved_share": (n - len(resolved)) / n,
        "top1_accuracy": (len(resolved) - len(wrong)) / n,
        "wrong_link_rate_of_resolved": len(wrong) / len(resolved) if resolved else 0.0,
        "arms": {},
    }
    for tau in TAUS:
        confirmed = (
            set()
            if tau is None
            else {m.mention_idx for m, t in resolved if t is not None and t[2] >= tau}
        )
        wrong_ids = {m.mention_idx for m, _ in wrong}
        flagged = {m.mention_idx for m, _ in resolved if m.mention_idx not in confirmed}
        silent = wrong_ids - flagged
        correct_flagged = flagged - wrong_ids
        out["arms"]["proposal_only" if tau is None else f"auto_{tau:.2f}"] = {
            "silent_error_propagation": len(silent) / n,
            "silent_wrong_claims": len(silent),
            "auto_confirmed_share": len(confirmed) / n,
            "flagged_share_of_claims": len(flagged) / len(resolved) if resolved else 0.0,
            "flagged_but_correct_share_of_claims": len(correct_flagged) / len(resolved)
            if resolved
            else 0.0,
        }
    return out


def summarise(cells: list[dict[str, Any]]) -> dict[str, Any]:
    def ms(vals: list[float]) -> list[float]:
        return [statistics.fmean(vals), statistics.stdev(vals) if len(vals) > 1 else 0.0]

    summary: dict[str, Any] = {}
    for rate in RATES:
        group = [c for c in cells if c["rate"] == rate]
        row: dict[str, Any] = {
            k: ms([c[k] for c in group])
            for k in (
                "blocking_recall",
                "unresolved_share",
                "top1_accuracy",
                "wrong_link_rate_of_resolved",
            )
        }
        row["arms"] = {
            arm: {k: ms([c["arms"][arm][k] for c in group]) for k in group[0]["arms"][arm]}
            for arm in group[0]["arms"]
        }
        summary[str(rate)] = row
    return summary


def main() -> int:
    src = os.environ.get("AEGIS_IBM_ACCOUNTS")
    if not src or not Path(src).exists():
        print("set AEGIS_IBM_ACCOUNTS to HI-Small_accounts.csv", file=sys.stderr)
        return 2
    names = load_names(Path(src))
    cells = [run_cell(names, seed, rate) for rate in RATES for seed in SEEDS]
    result = {
        "experiment": "E5",
        "accounts_file_sha256": file_sha256(src),
        "n_names": N_NAMES,
        "rates": RATES,
        "seeds": SEEDS,
        "taus": [t for t in TAUS],
        "mentions_per_entity": MENTIONS_PER_ENTITY,
        "distractor_fraction": DISTRACTOR_FRACTION,
        "versions": {
            "perturbation": PERTURBATION_VERSION,
            "scorer": SCORER_VERSION,
            "normaliser": NORMALISER_VERSION,
            "propose_threshold": PROPOSE_THRESHOLD,
            "incomplete_registry": INCOMPLETE_REGISTRY,
        },
        "cells": cells,
        "summary": summarise(cells),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    hdr = "rate  block_rec unres  top1   wrong|res | arm            silent    autoconf flagged  flag_correct"
    print(hdr)
    for rate, row in result["summary"].items():
        first = True
        for arm, a in row["arms"].items():
            lead = (
                f"{rate:<5} {row['blocking_recall'][0]:.3f}    {row['unresolved_share'][0]:.3f}  "
                f"{row['top1_accuracy'][0]:.3f}  {row['wrong_link_rate_of_resolved'][0]:.3f}    "
                if first
                else " " * 42
            )
            first = False
            print(
                f"{lead}| {arm:<14} {a['silent_error_propagation'][0]:.4f}"
                f"+-{a['silent_error_propagation'][1]:.4f} {a['auto_confirmed_share'][0]:.3f}    "
                f"{a['flagged_share_of_claims'][0]:.3f}    "
                f"{a['flagged_but_correct_share_of_claims'][0]:.3f}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
