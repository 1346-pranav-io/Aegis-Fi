"""Whole pipeline on real Postgres: PostgresBackend, call records, run/step/report tables,
LangGraph PostgresSaver checkpoints, kill/resume (T-INV-16), job queue, provenance (T-INV-04)."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from p3_e2e import llm_config
from p3_llm import KEY
from p3_runtime import big_budget

from aegis.eval.phase3.fixture_llm import FixtureLLM
from aegis.evidence.pg_store import PostgresLeafHashes
from aegis.evidence.provenance import walk_case
from aegis.ingestion.adapters import CsvMapping
from aegis.ingestion.writer import ingest_csv
from aegis.llm.gateway import LlmGateway
from aegis.llm.providers import ScriptedProvider
from aegis.llm.pseudonym import Pseudonymiser
from aegis.llm.records import PostgresCallRecorder
from aegis.llm.replay import InMemoryRecordStore
from aegis.llm.types import GatewayMode
from aegis.runtime.checkpoint import install_checkpoint_tables, postgres_checkpointer
from aegis.runtime.pg_services import (
    JobQueue,
    PostgresApprovals,
    PostgresAudit,
    PostgresBackend,
    PostgresCaseReader,
    PostgresSourceReader,
)
from aegis.runtime.policies import FixedPipeline
from aegis.runtime.runner import InvestigationRunner
from aegis.runtime.services import FakeERLinkReader, RuntimeServices
from aegis.runtime.state import RunStatus
from aegis.runtime.wiring import default_prompts, default_registry, default_schemas
from aegis.services import cases as svc

pytestmark = pytest.mark.integration

MAPPING = CsvMapping(
    adapter_name="t", adapter_version="1", ref_column="id", time_column="ts",
    time_format="%Y-%m-%d %H:%M", origin_columns=("o",), destination_columns=("d",),
    amount_column="a", fixed_currency="USD",
)  # fmt: skip


class Crash(Exception):
    pass


def seed(ingest_url: str, app_url: str, tmp_path: Path) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    rows = [("t1", "A", "B", 30, 100), ("t2", "B", "A", 28, 250), ("t3", "A", "C", 20, 100),
            ("t4", "C", "D", 10, 75)]  # fmt: skip
    csv = "id,ts,o,d,a\n" + "\n".join(
        f"{i},{(now - timedelta(hours=h)).strftime('%Y-%m-%d %H:%M')},{o},{d},{a}"
        for i, o, d, h, a in rows
    )
    f = tmp_path / "a.csv"
    f.write_text(csv + "\n")
    with psycopg.connect(ingest_url) as ic:
        ingest_csv(ic, f, MAPPING, "d1", "v1")
        as_of = ic.execute("SELECT clock_timestamp()").fetchone()[0]  # type: ignore[index]
    with psycopg.connect(app_url) as c:
        r = svc.create_alerts(
            c, [svc.ScoredItem("t1", "A", 0.9, ("CYCLE",))], signal_type="S", as_of=as_of,
            model_artefact_id="m",
        )  # fmt: skip
        c.commit()
    return r[0].case_id, as_of


class Env:
    """One 'process': its own connections, gateway, runner. Make a new one to simulate a restart."""

    def __init__(self, app_url: str, saver: Any, fault: Any = None) -> None:
        self.conn = psycopg.connect(app_url, autocommit=True)
        leaves = PostgresLeafHashes(self.conn)
        self.backend = PostgresBackend(self.conn, leaves)
        self.llm = FixtureLLM()
        self.provider = ScriptedProvider(self.llm)
        gw = LlmGateway(
            config=llm_config(), prompts=default_prompts(), pseudonymiser=Pseudonymiser(KEY),
            mode=GatewayMode.RECORD, provider=self.provider, store=InMemoryRecordStore(),
            recorder=PostgresCallRecorder(self.conn), sleep=lambda _s: None,
        )  # fmt: skip
        self.services = RuntimeServices(
            backend=self.backend, leaves=leaves, source=PostgresSourceReader(self.conn),
            cases=PostgresCaseReader(self.conn), er=FakeERLinkReader(),
            approvals=PostgresApprovals(self.conn), audit=PostgresAudit(self.conn), llm=gw,
            schemas=default_schemas(), fault=fault,
        )  # fmt: skip
        self.runner = InvestigationRunner(self.services, default_registry(), FixedPipeline(), saver)

    def close(self) -> None:
        self.conn.close()


def install(scratch_db_url: str) -> None:
    install_checkpoint_tables(scratch_db_url)


def test_full_run_on_postgres(
    scratch_db_url: str, ingest_url: str, app_url: str, tmp_path: Path
) -> None:
    case, as_of = seed(ingest_url, app_url, tmp_path)
    install(scratch_db_url)
    with postgres_checkpointer(app_url) as saver:
        env = Env(app_url, saver)
        rid = env.runner.start(case_id=case, as_of=as_of, budget=big_budget())
        st = env.runner.run(rid)
        assert st.status is RunStatus.COMPLETE and len(st.steps) == 6, _debug(st, env)
        c = env.conn
        assert c.execute(
            "SELECT status FROM runtime.run_current WHERE run_id=%s", (rid,)
        ).fetchone() == ("COMPLETE",)
        assert [
            r[0]
            for r in c.execute(
                "SELECT status FROM runtime.run_status_event WHERE run_id=%s ORDER BY event_id",
                (rid,),
            )
        ] == ["QUEUED", "RUNNING", "COMPLETE"]
        steps = c.execute(
            "SELECT agent, status, tool_calls FROM runtime.agent_step WHERE run_id=%s ORDER BY seq",
            (rid,),
        ).fetchall()
        assert [s[0] for s in steps] == [
            "TRANSACTION",
            "GRAPH",
            "BEHAVIOUR",
            "INVESTIGATION",
            "VERIFICATION",
            "REPORTING",
        ]
        assert all(s[1] == "OK" and s[2] for s in steps)  # every step logged its tool calls
        # LLM calls recorded, pseudonymised only
        n_calls = c.execute(
            "SELECT count(*) FROM llm.call_record WHERE run_id=%s", (rid,)
        ).fetchone()[0]  # type: ignore[index]
        assert n_calls >= 2
        texts = " ".join(r[0] or "" for r in c.execute("SELECT response_text FROM llm.call_record"))
        assert "ACCT_" in texts and not re.search(r"\bA\b\|", texts)
        sent = " ".join(r.user for r in env.provider.requests)
        assert "ACCT_" in sent
        # claims verified, report stored, provenance intact down to real source rows
        env.backend.evidence.list_claims(case)
        claims = env.backend.evidence.list_claims(case)
        assert claims and all(x.status and x.status.value == "VERIFIED" for x in claims)
        reps = walk_case(env.backend.evidence, PostgresLeafHashes(c), case)
        assert reps and all(r.ok for r in reps)
        assert {n.kind for r in reps for n in r.leaves} <= {"SOURCE", "REFERENCE"}
        body = c.execute("SELECT body FROM runtime.report WHERE case_id=%s", (case,)).fetchone()[0]  # type: ignore[index]
        assert len(body["findings"]["items"]) == len(claims) and body["open_contradictions"] == []
        # a second run of the same case reuses the same evidence (idempotent, content-addressed)
        n_ev = c.execute(
            "SELECT count(*) FROM evidence.evidence_item WHERE case_id=%s", (case,)
        ).fetchone()[0]  # type: ignore[index]
        rid2 = env.runner.start(case_id=case, as_of=as_of, budget=big_budget())
        env.runner.run(rid2)
        assert (
            c.execute(
                "SELECT count(*) FROM evidence.evidence_item WHERE case_id=%s", (case,)
            ).fetchone()[0]
            == n_ev
        )  # type: ignore[index]
        assert [v for _, v in env.backend.reports.versions(case)] == [1, 2]  # new immutable version
        env.close()


@pytest.mark.parametrize("point", ["before_commit", "after_dispatch"])
def test_killed_run_resumes_from_postgres_checkpoint(
    point: str, scratch_db_url: str, ingest_url: str, app_url: str, tmp_path: Path
) -> None:
    """T-INV-16 on Postgres: process 'dies' mid-run; a NEW process (new connections, new saver)
    resumes it and reaches the same final state as an uninterrupted run."""
    case, as_of = seed(ingest_url, app_url, tmp_path)
    install(scratch_db_url)
    with postgres_checkpointer(app_url) as saver:  # reference: uninterrupted
        ref = Env(app_url, saver)
        ref_id = ref.runner.start(case_id=case, as_of=as_of, budget=big_budget())
        ref_state = ref.runner.run(ref_id)
        ref.close()

    seen = {"n": 0}

    def fault(p: str) -> None:
        if p == point:
            seen["n"] += 1
            if seen["n"] == 4:  # 4th step: Investigation, the one that creates claims
                raise Crash(p)

    with postgres_checkpointer(app_url) as saver1:
        e1 = Env(app_url, saver1, fault)
        rid = e1.runner.start(case_id=case, as_of=as_of, budget=big_budget())
        with pytest.raises(Crash):
            e1.runner.run(rid)
        mid = e1.conn.execute(
            "SELECT status FROM runtime.run_current WHERE run_id=%s", (rid,)
        ).fetchone()
        assert mid == ("RUNNING",)
        n_steps_mid = e1.conn.execute(
            "SELECT count(*) FROM runtime.agent_step WHERE run_id=%s", (rid,)
        ).fetchone()[0]  # type: ignore[index]
        assert n_steps_mid == (3 if point == "before_commit" else 4)
        e1.close()

    with postgres_checkpointer(app_url) as saver2:  # a fresh process
        e2 = Env(app_url, saver2)
        st = e2.runner.run(rid)
        c = e2.conn
        assert st.status is RunStatus.COMPLETE == ref_state.status
        assert [s.agent for s in st.steps] == [s.agent for s in ref_state.steps]
        assert c.execute(
            "SELECT count(*) FROM runtime.agent_step WHERE run_id=%s", (rid,)
        ).fetchone() == (6,)

        # no duplicates: the killed run owns exactly the claims the reference run owns
        def claims(run: str) -> list[tuple[str, str]]:
            return sorted(c.execute(
                "SELECT c.text, coalesce(v.status,'DRAFT') FROM evidence.claim c "
                "LEFT JOIN evidence.claim_current_verification v USING (claim_id) "
                "WHERE c.run_id=%s",
                (run,)).fetchall())  # fmt: skip

        assert claims(rid) == claims(ref_id) and claims(rid)

        # one report version per run, with the same content apart from ids
        def report(run: str) -> str:
            body = c.execute("SELECT body FROM runtime.report WHERE run_id=%s", (run,)).fetchone()[
                0
            ]  # type: ignore[index]
            return re.sub(
                r"(clm|ev|ctr|run)_[0-9A-Za-z]{26}", r"\1_ID", json.dumps(body, sort_keys=True)
            )

        import difflib

        a, b = report(rid), report(ref_id)
        d = [x for x in difflib.ndiff(a.split(", "), b.split(", ")) if x[0] in "+-"]
        assert a == b, d[:6]
        assert any(s.reused for s in st.steps) == (point == "after_dispatch")
        e2.close()


def test_job_queue_claims_each_job_once_and_leases_expire(
    scratch_db_url: str, ingest_url: str, app_url: str, tmp_path: Path
) -> None:
    case, as_of = seed(ingest_url, app_url, tmp_path)
    install(scratch_db_url)
    with postgres_checkpointer(app_url) as saver:
        env = Env(app_url, saver)
        rid = env.runner.start(case_id=case, as_of=as_of, budget=big_budget())
        q = JobQueue(env.conn)
        job = q.enqueue(rid)
        other = psycopg.connect(app_url, autocommit=True)
        assert q.claim("w1", lease_s=60) == (job, rid)
        assert JobQueue(other).claim("w2", lease_s=60) is None  # already claimed, lease valid
        env.conn.execute("UPDATE runtime.job SET locked_until = now() - interval '1 second'")
        assert JobQueue(other).claim("w2", lease_s=60) == (job, rid)  # dead worker: re-claimable
        env.runner.run(rid)
        q.finish(job, ok=True)
        assert env.conn.execute("SELECT status, attempts FROM runtime.job").fetchone() == (
            "DONE",
            2,
        )
        assert q.claim("w3") is None
        other.close()
        env.close()


def _debug(st: Any, env: "Env") -> str:  # helper used while diagnosing; harmless
    out = []
    for s in st.steps:
        rec = env.backend.runs.get_step(st.run_id, s.task_id)
        out.append(f"{s.agent.value}:{s.status.value}:{rec.error if rec else None}")
    return "; ".join(out)
