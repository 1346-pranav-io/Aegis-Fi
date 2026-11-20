"""One entry point for local dev tasks (no make/just on this platform).

Usage (from backend/): uv run python scripts/dev.py <task>  (see TASKS below)
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DB_URL = "postgresql://aegis_migrator:dev-only-change-me@127.0.0.1:5432/aegis"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "docker-compose.yml")]

TASKS: dict[str, list[list[str]]] = {
    "lint": [["uv", "run", "ruff", "check", "."], ["uv", "run", "ruff", "format", "--check", "."]],
    "type": [["uv", "run", "mypy"]],
    "arch": [["uv", "run", "lint-imports"]],
    "test": [["uv", "run", "pytest", "-q"]],
    "test-int": [["uv", "run", "pytest", "-q", "-m", "integration"]],
    "up": [[*COMPOSE, "up", "-d", "--wait"]],
    "down": [[*COMPOSE, "down"]],
    "migrate": [["uv", "run", "python", "scripts/migrate.py"]],
}
TASKS["check"] = TASKS["lint"] + TASKS["type"] + TASKS["arch"] + TASKS["test"]


def main(name: str) -> int:
    env = {
        **os.environ,
        "AEGIS_TEST_DATABASE_URL": os.environ.get("AEGIS_TEST_DATABASE_URL", DB_URL),
    }
    for cmd in TASKS[name]:
        print("+", " ".join(cmd), flush=True)
        rc = subprocess.run(cmd, cwd=BACKEND, env=env).returncode  # noqa: S603
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in TASKS:
        sys.exit(f"usage: dev.py <{'|'.join(TASKS)}>")
    sys.exit(main(sys.argv[1]))
