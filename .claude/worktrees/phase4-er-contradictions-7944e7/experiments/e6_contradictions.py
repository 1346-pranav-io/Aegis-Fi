"""E6: contradiction detection - deterministic detectors vs a naive baseline on seeded contradictions.

Procedure is pre-registered in docs/PHASE4_NOTES.md section 0 (written before the first run).
The LLM-only arm is NOT run (no live Claude call before Q-02 is confirmed).
Limits: detectors and generator share an author and content conventions, so high recall on
seeded data is largely by construction; the decoy mix is the generator's choice.

Run from backend/:  uv run python ../experiments/e6_contradictions.py
Writes experiments/results/e6_contradictions.json (git-ignored) and prints tables.
"""

import json
import statistics
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from aegis.analytics.contradictions import DETECTOR_VERSION, DetectionInput, detect_all
from aegis.domain.evidence import (
    ContradictionDraft,
    ContradictionMember,
    ContradictionType,
    MemberType,
    Severity,
)
from aegis.eval.perturbation import (
    PERTURBATION_VERSION,
    ContradictionCorpus,
    generate_contradiction_corpus,
    to_detection_input,
)

SEEDS = (1, 2, 3, 4, 5)
N_PER_TYPE = 40
N_DECOYS = 40
OUT = Path(__file__).parent / "results" / "e6_contradictions.json"
TYPES = [t for t in ContradictionType if t is not ContradictionType.SOURCE_DISAGREEMENT]


def naive_baseline(inp: DetectionInput, case_id: str) -> list[ContradictionDraft]:
    """Same (subject, predicate), different raw value => contradiction. Nothing else."""
    facts: dict[tuple[str, str], list[tuple[MemberType, str, str]]] = defaultdict(list)
    for e in inp.evidence:
        for f in e.draft.content.get("facts", []) or []:
            if isinstance(f, dict) and f.get("value") is not None:
                key = (str(f["subject_ref"]), str(f["predicate"]))
                facts[key].append((MemberType.EVIDENCE, e.evidence_id, str(f["value"])))
    for c in inp.claims:
        s = c.draft.structure
        if s.object_value is not None:
            facts[(s.subject_ref, s.predicate)].append(
                (MemberType.CLAIM, c.claim_id, str(s.object_value))
            )
    out: list[ContradictionDraft] = []
    for key in sorted(facts):
        g = sorted(facts[key])
        for i, (ta, ia, va) in enumerate(g):
            for tb, ib, vb in g[i + 1 :]:
                if ia == ib or va == vb:
                    continue
                numeric = _is_num(va) and _is_num(vb)
                out.append(
                    ContradictionDraft(
                        case_id=case_id,
                        type=ContradictionType.AMOUNT_MISMATCH
                        if numeric
                        else ContradictionType.ATTRIBUTE_CONFLICT,
                        severity=Severity.MEDIUM,
                        detector_name="naive_baseline",
                        detector_version="1",
                        members=(
                            ContradictionMember(member_type=ta, member_id=ia),
                            ContradictionMember(member_type=tb, member_id=ib),
                        ),
                    )
                )
    return out


def _is_num(v: str) -> bool:
    try:
        return Decimal(v).is_finite()
    except InvalidOperation:
        return False


def score(
    corpus: ContradictionCorpus, drafts: list[ContradictionDraft], strict: bool
) -> dict[str, Any]:
    truth = {(s.type, s.members) for s in corpus.seeded}
    flagged = [(d.type, frozenset(m.member_id for m in d.members)) for d in drafts]

    def match(f: tuple[ContradictionType, frozenset[str]]) -> bool:
        if strict:
            return f in truth
        return any(f[1] == t[1] for t in truth)

    per: dict[str, dict[str, float]] = {}
    for t in [None, *TYPES]:
        sel = [f for f in flagged if t is None or f[0] is t]
        seeded = [x for x in truth if t is None or x[0] is t]
        tp = sum(match(f) for f in sel)
        hit = {
            x
            for x in seeded
            if (x in set(flagged) if strict else any(f[1] == x[1] for f in flagged))
        }
        p = tp / len(sel) if sel else 0.0
        r = len(hit) / len(seeded) if seeded else 0.0
        per["ALL" if t is None else t.value] = {
            "flags": len(sel),
            "true_flags": tp,
            "seeded": len(seeded),
            "found": len(hit),
            "precision": p,
            "recall": r,
            "f1": 2 * p * r / (p + r) if p + r else 0.0,
        }
    return per


def main() -> int:
    runs: list[dict[str, Any]] = []
    for seed in SEEDS:
        corpus = generate_contradiction_corpus(
            seed=seed, n_per_type=N_PER_TYPE, n_decoys_per_type=N_DECOYS
        )
        inp = to_detection_input(corpus)
        det = detect_all(inp, case_id=corpus.case_id)
        base = naive_baseline(inp, corpus.case_id)
        runs.append(
            {
                "seed": seed,
                "seeded": len(corpus.seeded),
                "decoys": len(corpus.decoy_pairs),
                "detectors_strict": score(corpus, det, True),
                "baseline_strict": score(corpus, base, True),
                "baseline_loose": score(corpus, base, False),
            }
        )
    summary: dict[str, Any] = {}
    for system in ("detectors_strict", "baseline_strict", "baseline_loose"):
        summary[system] = {}
        for key in ["ALL", *[t.value for t in TYPES]]:
            summary[system][key] = {
                m: [
                    statistics.fmean(r[system][key][m] for r in runs),
                    statistics.stdev(r[system][key][m] for r in runs),
                ]
                for m in ("precision", "recall", "f1")
            }
    result = {
        "experiment": "E6",
        "seeds": SEEDS,
        "n_per_type": N_PER_TYPE,
        "n_decoys_per_type": N_DECOYS,
        "versions": {"perturbation": PERTURBATION_VERSION, "detectors": DETECTOR_VERSION},
        "runs": runs,
        "summary": summary,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    for system, rows in summary.items():
        print(f"\n{system}  (mean +- sd over seeds {SEEDS})")
        print(f"{'type':<22}{'precision':>16}{'recall':>16}{'f1':>16}")
        for key, m in rows.items():
            cells = "".join(
                f"{m[k][0]:>10.3f}+-{m[k][1]:.3f}" for k in ("precision", "recall", "f1")
            )
            print(f"{key:<22}{cells}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
