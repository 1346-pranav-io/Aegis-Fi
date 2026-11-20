"""Reproduction preflight (Phase 6 exit criterion): is this checkout and database in the state
the stored results were produced from?

Checks, in order, and stops with a non-zero exit code if any fails:
  1. raw dataset files match the SHA-256 values recorded in docs/research/DATASET_VERIFICATION.md
  2. the split spec hash equals the one stored in every result file that records one
  3. (when AEGIS_APP_URL is set) the database dataset manifest equals the one stored in the results
  4. every reported number traces to a stored run (scripts/check_results_trace.py)

It does NOT re-run the experiments (they take minutes to hours); docs/REPRODUCE.md lists the exact
commands. Run from backend/: uv run python scripts/repro_check.py
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "ibm-aml"
RESULTS = ROOT / "experiments" / "results"


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    problems: list[str] = []
    doc = (ROOT / "docs" / "research" / "DATASET_VERIFICATION.md").read_text(encoding="utf-8")
    for name, digest in re.findall(r"\| (HI-Small_\S+) \|[^|]*\| `([0-9a-f]{64})` \|", doc):
        p = RAW / name
        if not p.exists():
            problems.append(f"raw file missing: {p}")
        elif file_sha(p) != digest:
            problems.append(f"raw file hash differs from the recorded value: {name}")
        else:
            print("ok raw", name)

    from aegis.eval.splits import load_split

    split_hash = load_split(
        ROOT / "experiments" / "configs" / "split_ibm_hi_small_v1.json"
    ).spec_hash()
    manifests: set[str] = set()
    for f in sorted(RESULTS.glob("e*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if "split_hash" in data and data["split_hash"] != split_hash:
            problems.append(f"{f.name}: split hash differs from the current split spec")
        if "dataset_manifest_hash" in data:
            manifests.add(data["dataset_manifest_hash"])
    print("ok split spec checked against", len(list(RESULTS.glob("e*.json"))), "result files")

    url = os.environ.get("AEGIS_APP_URL")
    if url and manifests:
        import psycopg

        from aegis.common.hashing import content_hash

        with psycopg.connect(url) as conn:
            batches = conn.execute(
                "SELECT batch_id, file_hashes FROM source.ingestion_batch "
                "WHERE dataset_name=%s AND status='COMPLETE' ORDER BY batch_id",
                ("IBM-AML-HI-Small",),
            ).fetchall()
            n = conn.execute("SELECT count(*) FROM source.transaction").fetchone()
        current = content_hash({"batches": [[b, h] for b, h in batches], "rows": n[0] if n else 0})
        if current not in manifests:
            problems.append("database dataset manifest matches none of the stored results")
        else:
            print("ok database manifest")
    elif not url:
        print("skipped database manifest check (AEGIS_APP_URL not set)")

    rc = subprocess.run(  # noqa: S603 - fixed argv: this interpreter and a sibling script
        [sys.executable, str(Path(__file__).with_name("check_results_trace.py"))], check=False
    ).returncode
    if rc:
        problems.append("result traceability check failed")
    for p in problems:
        print("FAIL", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
