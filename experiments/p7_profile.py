"""Phase 7 hardening: measure before changing anything (D-32).

Stages timed (wall clock, one run each unless stated; this is a laptop with other load, so treat
differences under ~20% as noise):
  * load_transactions split into SQL COPY, CSV parse, and factorisation
  * build_features
  * API read endpoints through the in-process test client: GET /queue and GET /cases/{id},
    50 calls each, p50 / p95 (needs cases in the database and the AEGIS_API_* settings)
Writes experiments/results/p7_profile_<stamp>.json.

Run from backend/:
  AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis uv run python ../experiments/p7_profile.py
"""

import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg

from aegis.analytics.features import build_features
from aegis.store.frames import _SQL, load_transactions

ROOT = Path(__file__).resolve().parents[1]


def timed(fn: Any) -> tuple[float, Any]:
    t0 = time.perf_counter()
    out = fn()
    return time.perf_counter() - t0, out


def main() -> None:
    url = os.environ["AEGIS_APP_URL"]
    res: dict[str, Any] = {"started": datetime.now(UTC).isoformat()}
    with psycopg.connect(url) as conn:
        as_of = conn.execute("SELECT clock_timestamp()").fetchone()
        assert as_of is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "txn.csv"

            def copy_out() -> None:
                with conn.cursor() as cur, cur.copy(_SQL.replace("%(as_of)s", "%s"), (as_of[0],)) as cp:
                    with path.open("wb") as f:
                        for chunk in cp:
                            f.write(bytes(chunk))

            res["sql_copy_s"], _ = timed(copy_out)
            res["csv_bytes"] = path.stat().st_size
            res["csv_parse_s"], raw = timed(
                lambda: pd.read_csv(path, dtype={"ref": str, "origin_ref": str, "dest_ref": str})
            )
            res["rows"] = len(raw)
            res["factorize_s"], _ = timed(
                lambda: pd.factorize(pd.concat([raw["origin_ref"], raw["dest_ref"]], ignore_index=True))
            )
        res["load_transactions_total_s"], frame = timed(lambda: load_transactions(conn, as_of[0]))
    res["build_features_s"], feats = timed(lambda: build_features(frame))
    res["feature_columns"] = int(feats.shape[1])
    res["feature_memory_mb"] = float(feats.memory_usage(deep=True).sum() / 1e6)
    res["frame_memory_mb"] = float(frame.memory_usage(deep=True).sum() / 1e6)

    # ---- API reads
    try:
        from fastapi.testclient import TestClient

        from aegis.api.app import create_app
        from aegis.api.auth import Role, issue_dev_token
        from aegis.api.settings import ApiSettings

        app = create_app(ApiSettings())
        s = ApiSettings()
        tok = issue_dev_token(
            secret=s.jwt_key, user_id="profiler", roles=[Role.AUDITOR], issuer=s.jwt_issuer,
            audience=s.jwt_audience,
        )  # fmt: skip
        h = {"Authorization": f"Bearer {tok}"}
        client = TestClient(app)
        q = client.get("/queue?limit=500", headers=h)
        res["queue_status"] = q.status_code
        cases = q.json() if q.status_code == 200 else []
        res["queue_items"] = len(cases)

        def lat(path: str, n: int = 50) -> dict[str, float]:
            xs = []
            for _ in range(n):
                t0 = time.perf_counter()
                client.get(path, headers=h)
                xs.append((time.perf_counter() - t0) * 1000)
            return {"p50_ms": float(np.percentile(xs, 50)), "p95_ms": float(np.percentile(xs, 95))}

        res["api_queue_limit500"] = lat("/queue?limit=500")
        res["api_queue_limit100"] = lat("/queue?limit=100")
        if cases:
            res["api_case_detail"] = lat(f"/cases/{cases[0]['case_id']}")
            res["api_audit"] = lat("/audit?limit=100")
    except Exception as exc:  # noqa: BLE001 - profiling must report, not crash
        res["api_error"] = f"{type(exc).__name__}: {exc}"

    path_out = ROOT / "experiments" / "results" / f"p7_profile_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path_out.write_text(json.dumps(res, indent=2, default=float), encoding="utf-8")
    print(json.dumps(res, indent=2, default=float))
    print("->", path_out)


if __name__ == "__main__":
    main()
