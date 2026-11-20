"""Run the Phase 3 fixture experiments (G1, V1, O1) with the stub LLM.

Usage (from backend/):  uv run python ../experiments/p3_fixture_experiments.py
Writes experiments/results/p3_<run_id>.json (git-ignored) and
docs/research/P3_FIXTURE_EXPERIMENTS.md.
The run id is the first 12 hex chars of SHA-256(config file bytes + code commit [+ marker if the
working tree had uncommitted changes]), so a different config or different code gives a new id.
"""

import hashlib
import json
import subprocess
from pathlib import Path

from aegis.eval.phase3 import experiments as ex

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "experiments" / "configs" / "p3_fixture_experiments.json"


def main() -> None:
    raw = CFG.read_bytes()
    cfg = json.loads(raw)
    commit = (
        subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        ).stdout.strip()
        or "unknown"
    )  # noqa: S607
    dirty = subprocess.run(  # noqa: S603
        ["git", "status", "--porcelain", "--untracked-files=no"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    ).stdout.strip()
    if dirty:
        commit += "+uncommitted-changes"
    run_id = hashlib.sha256(raw + commit.encode()).hexdigest()[:12]
    res = {
        "g1": ex.g1_grounding({**cfg["g1"], "seeds_per_kind": cfg["g1"]["seeds_per_kind"]}),
        "v1": ex.v1_verification(cfg["v1"]),
        "o1": ex.o1_orchestration(cfg["o1"]),
    }
    out = ROOT / "experiments" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"p3_{run_id}.json").write_text(
        json.dumps({"run_id": run_id, "commit": commit, "config": cfg, "results": res}, indent=1)
    )
    md = ex.render_markdown(run_id, commit, {"seeds_per_kind": cfg["g1"]["seeds_per_kind"]}, res)
    (ROOT / "docs" / "research" / "P3_FIXTURE_EXPERIMENTS.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
