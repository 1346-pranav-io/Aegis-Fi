"""Command line: `uv run python -m aegis.cli <command>` (or the `aegis` script).

  dev-token   print a LOCAL-DEVELOPMENT JWT (needs AEGIS_API_JWT_KEY; refuses non-dev use)
  serve       run the API with uvicorn on 127.0.0.1
  worker      process queued investigation runs (--once to stop when the queue is empty)
  report      print or save a case's latest report as JSON or Markdown (read-only)

Configuration comes from the environment (AEGIS_API_*; AEGIS_APP_URL for the worker and report).
Live Claude calls are disabled (D-29): `worker --llm stub` uses the labelled fixture stub, whose
outputs are canned and are NOT model output; the default is no model at all.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg

from aegis.api.auth import Role, issue_dev_token
from aegis.common.logging import configure_logging


def _stub_gateway(conn: psycopg.Connection) -> object:
    """The fixture stub (canned outputs) wrapped in the real gateway, so pseudonymisation, budgets,
    schema checks and call records all run. Labelled STUB in every model id."""
    from aegis.eval.phase3.fixture_llm import FixtureLLM
    from aegis.llm.config import LlmConfig, ModelSpec
    from aegis.llm.gateway import LlmGateway
    from aegis.llm.providers import ScriptedProvider
    from aegis.llm.pseudonym import Pseudonymiser
    from aegis.llm.records import PostgresCallRecorder
    from aegis.llm.replay import InMemoryRecordStore
    from aegis.llm.types import GatewayMode
    from aegis.runtime.wiring import default_prompts

    key = os.environ["AEGIS_PSEUDONYM_KEY"].encode()  # required: no default key
    cfg = LlmConfig(
        models={
            r: ModelSpec(model_id=f"STUB-{r}")
            for r in ("investigation", "verification", "reporting")
        }
    )
    return LlmGateway(
        config=cfg, prompts=default_prompts(), pseudonymiser=Pseudonymiser(key),
        mode=GatewayMode.RECORD, provider=ScriptedProvider(FixtureLLM()),
        store=InMemoryRecordStore(), recorder=PostgresCallRecorder(conn),
    )  # fmt: skip


def cmd_dev_token(a: argparse.Namespace) -> int:
    secret = os.environ["AEGIS_API_JWT_KEY"]
    issuer = os.environ["AEGIS_API_JWT_ISSUER"]
    audience = os.environ.get("AEGIS_API_JWT_AUDIENCE", "aegis-api")
    if os.environ.get("AEGIS_API_JWT_ALGORITHM", "HS256") != "HS256":
        print(
            "dev-token only signs HS256 tokens; use your identity provider for RS256",
            file=sys.stderr,
        )
        return 2
    print(
        issue_dev_token(
            secret=secret,
            user_id=a.user,
            roles=[Role(r) for r in a.roles],
            issuer=issuer,
            audience=audience,
            ttl_s=a.ttl,
        )
    )
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn

    from aegis.api.app import create_app
    from aegis.api.settings import ApiSettings

    configure_logging("INFO")
    uvicorn.run(create_app(ApiSettings()), host="127.0.0.1", port=a.port)
    return 0


def cmd_worker(a: argparse.Namespace) -> int:
    from aegis.services.runs import work_once

    configure_logging("INFO")
    url = os.environ["AEGIS_APP_URL"]
    factory = _stub_gateway if a.llm == "stub" else None
    wid = f"worker-{os.getpid()}"
    while True:
        done = work_once(url, wid, llm_factory=factory)
        if done is None:
            if a.once:
                return 0
            time.sleep(a.poll)
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    from aegis.services.case_views import latest_report
    from aegis.services.report_export import report_to_markdown

    with psycopg.connect(os.environ["AEGIS_APP_URL"]) as conn:
        rep = latest_report(conn, a.case_id)
    text = (
        report_to_markdown(rep)
        if a.format == "markdown"
        else json.dumps(rep, indent=2, sort_keys=True, default=str)
    )
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")  # export is a file write, never a send
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aegis")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("dev-token", help="LOCAL DEVELOPMENT token")
    t.add_argument("--user", required=True)
    t.add_argument("--roles", nargs="+", required=True, choices=[r.value for r in Role])
    t.add_argument("--ttl", type=int, default=3600)
    t.set_defaults(fn=cmd_dev_token)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    w = sub.add_parser("worker")
    w.add_argument("--once", action="store_true")
    w.add_argument("--poll", type=float, default=2.0)
    w.add_argument("--llm", choices=["none", "stub"], default="none")
    w.set_defaults(fn=cmd_worker)
    r = sub.add_parser("report")
    r.add_argument("case_id")
    r.add_argument("--format", choices=["json", "markdown"], default="markdown")
    r.add_argument("--out")
    r.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
