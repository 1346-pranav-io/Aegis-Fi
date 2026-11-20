"""Check that every reported number traces to a stored run (Phase 6 exit criterion).

`docs/research/RESULTS_INDEX.json` lists each reported number: the document it appears in, how it
is printed there, the stored result file and the JSON path it comes from, and the number itself.
This script fails when any of these is false:
  * the result file exists and its SHA-256 matches the one recorded in the index
  * the JSON path resolves in that file and equals the indexed value (within the stated rounding)
  * the printed text appears in the document

Run from backend/: uv run python scripts/check_results_trace.py [--rehash]
`--rehash` rewrites the recorded file hashes (only to be used when a result file is deliberately
replaced by a new run; the old run should stay in experiments/results/).
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "docs" / "research" / "RESULTS_INDEX.json"


def resolve(doc: Any, path: str) -> Any:
    """Dotted path; list items by integer index; `[key=value]` selects a list item by field."""
    cur = doc
    for part in path.split("."):
        if part.startswith("[") and part.endswith("]"):
            key, _, val = part[1:-1].partition("=")
            matches = [x for x in cur if str(x.get(key)) == val]
            if len(matches) != 1:
                raise KeyError(f"{part} matched {len(matches)} items")
            cur = matches[0]
        elif isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def sha(path: Path) -> str:
    """SHA-256 with line endings normalised, so a checkout with CRLF conversion hashes the same."""
    data = path.read_bytes().replace(bytes([13, 10]), bytes([10]))
    return hashlib.sha256(data).hexdigest()


def main(rehash: bool) -> int:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    errors: list[str] = []
    for fname, rec in index["result_files"].items():
        p = ROOT / "experiments" / "results" / fname
        if not p.exists():
            errors.append(f"missing result file {fname}")
        elif rehash:
            rec["sha256"] = sha(p)
        elif sha(p) != rec["sha256"]:
            errors.append(f"{fname}: file hash differs from the index")
    for e in index["numbers"]:
        label = f"{e['doc']}: {e['label']}"
        p = ROOT / "experiments" / "results" / e["file"]
        try:
            value = resolve(json.loads(p.read_text(encoding="utf-8")), e["path"])
        except (OSError, KeyError, IndexError, ValueError) as exc:
            errors.append(f"{label}: cannot resolve {e['file']}#{e['path']} ({exc})")
            continue
        printed = e["printed"]
        if isinstance(value, float):
            decimals = int(e.get("decimals", 4))
            if f"{value:.{decimals}f}" != printed:
                errors.append(f"{label}: stored {value:.{decimals}f} but index prints {printed}")
        elif str(value) != printed:
            errors.append(f"{label}: stored {value} but index prints {printed}")
        text = (ROOT / e["doc"]).read_text(encoding="utf-8")
        if printed not in text:
            errors.append(f"{label}: {printed!r} does not appear in {e['doc']}")
    if rehash:
        INDEX.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
        print("rehashed", len(index["result_files"]), "files")
        return 0
    for err in errors:
        print("FAIL", err)
    n_num, n_files = len(index["numbers"]), len(index["result_files"])
    print(f"{n_num} numbers, {n_files} files, {len(errors)} problems")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main("--rehash" in sys.argv))
