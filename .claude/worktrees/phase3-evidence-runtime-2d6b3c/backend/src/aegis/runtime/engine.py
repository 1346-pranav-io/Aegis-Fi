"""Step execution: one capability task, persisted atomically and idempotently.

A step is the unit of resumability. `execute` first looks for a recorded step for
(run_id, task_id); if one exists (the run crashed after the step committed but before its
checkpoint) the recorded result is reused and the capability is NOT run again. Otherwise the
capability runs inside a database transaction together with the persistence of its evidence,
contradictions and the `agent_step` row, so a crash leaves either everything or nothing.
"""

from typing import Any

from pydantic import ValidationError
from ulid import ULID

from aegis.common.errors import AegisError, BudgetExceeded
from aegis.common.hashing import content_hash
from aegis.common.logging import get_logger
from aegis.domain.agents import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    Budget,
    Ref,
    ResultStatus,
)
from aegis.evidence.canon import hashable
from aegis.llm.budget import BudgetTracker
from aegis.runtime.registry import CapabilityRegistry
from aegis.runtime.services import RuntimeServices, StepRecord
from aegis.runtime.state import InvestigationState, PlannedTask, StepSummary
from aegis.runtime.tools import RuntimeToolGateway, ToolContext, ToolDenied

log = get_logger("aegis.engine")

_CAPABILITY_ERRORS = (AegisError, ValidationError, ValueError, KeyError, TypeError)


def bind_inputs(agent: AgentName, state: InvestigationState) -> tuple[Ref, ...]:
    ev = tuple(Ref(kind="evidence", id=i) for i in state.evidence_ids)
    cl = tuple(Ref(kind="claim", id=i) for i in state.claim_ids)
    if agent is AgentName.INVESTIGATION:
        return ev
    if agent is AgentName.VERIFICATION:
        return cl
    if agent in (AgentName.CONTRADICTION, AgentName.REPORTING):
        return ev + cl
    return ()


def run_tracker(state: InvestigationState) -> BudgetTracker:
    """Run-level LLM budget rebuilt from persisted state counters (so resume keeps the totals)."""
    return BudgetTracker(
        max_tokens=state.budget.max_tokens,
        max_cost_usd=state.budget.max_cost_usd,
        tokens=state.tokens_used,
        cost_usd=state.cost_usd,
    )


class StepEngine:
    def __init__(self, services: RuntimeServices, registry: CapabilityRegistry) -> None:
        self._s = services
        self._reg = registry

    def execute(self, state: InvestigationState, planned: PlannedTask, seq: int) -> StepSummary:
        s = self._s
        task_id = state.task_id(seq)
        existing = s.backend.runs.get_step(state.run_id, task_id)
        if existing is not None:
            return self._summary(existing, seq, state, reused=True)

        cap = self._reg.get(planned.agent)
        refs = bind_inputs(planned.agent, state)
        remaining_tokens = max(0, state.budget.max_tokens - state.tokens_used)
        task = AgentTask(
            task_id=task_id,
            run_id=state.run_id,
            case_id=state.case_id,
            agent=planned.agent,
            as_of=state.as_of,
            task_type=planned.task_type,
            params=planned.params,
            input_refs=refs,
            budget=Budget(
                max_steps=state.budget.max_steps,
                max_seconds=max(1.0, state.budget.max_seconds - state.seconds_used),
                max_tokens=remaining_tokens,
                max_cost_usd=max(0.0, state.budget.max_cost_usd - state.cost_usd),
            ),
        )
        step_id = f"step_{ULID()}"
        run_budget = run_tracker(state)
        ctx = ToolContext(
            task=task,
            capability=cap,
            services=s,
            step_id=step_id,
            llm_budget=run_budget.child(remaining_tokens, task.budget.max_cost_usd),
        )
        gw = RuntimeToolGateway(ctx)
        t0 = s.clock()
        with s.backend.transaction():
            try:
                result = cap.run(task, gw)
            except ToolDenied as e:
                result = self._fail(task, cap.version, ResultStatus.DENIED, "TOOL_DENIED", str(e))
            except BudgetExceeded as e:
                result = AgentResult(
                    task_id=task.task_id, status=ResultStatus.BUDGET_EXHAUSTED, version=cap.version,
                    error=AgentError(code="BUDGET", message=str(e)),
                )  # fmt: skip
            except _CAPABILITY_ERRORS as e:
                result = self._fail(
                    task, cap.version, ResultStatus.FAILED, type(e).__name__, str(e)[:500]
                )
            s.checkpoint("after_capability")
            ev_ids: list[str] = []
            new_ids: list[str] = []
            ctr_ids: list[str] = []
            try:
                with s.backend.transaction():  # savepoint: bad output must not half-persist
                    if result.status not in (ResultStatus.FAILED, ResultStatus.DENIED):
                        for d in result.evidence:
                            if d.case_id != task.case_id:
                                raise AegisError("evidence for another case")
                            if d.as_of > task.as_of:
                                raise AegisError("evidence as_of is later than the task as_of")
                            stamped = d.model_copy(
                                update={"run_id": task.run_id, "step_id": step_id}
                            )
                            ev, created = s.backend.evidence.create_evidence(stamped)
                            if ev.evidence_id not in ev_ids:
                                ev_ids.append(ev.evidence_id)
                                if created or ev.evidence_id not in state.evidence_ids:
                                    new_ids.append(ev.evidence_id)
                        for cd in result.contradictions:
                            ctr_ids.append(
                                s.backend.evidence.create_contradiction(cd).contradiction_id
                            )
            except AegisError as e:
                log.warning("step.output_rejected", task=task.task_id, error=str(e)[:200])
                result = self._fail(
                    task, cap.version, ResultStatus.FAILED, "OUTPUT_REJECTED", str(e)[:500]
                )
                ev_ids, new_ids, ctr_ids = [], [], []
            ms = int((s.clock() - t0) * 1000)
            output: dict[str, Any] = {
                "evidence_ids": ev_ids,
                "new_evidence_ids": new_ids,
                "claim_ids": list(ctx.created_claims),
                "verification_ids": list(ctx.created_verifications),
                "contradiction_ids": ctr_ids,
                "report_ids": list(ctx.created_reports),
                "proposals": [p.model_dump(mode="json") for p in result.proposals],
            }
            rec = StepRecord(
                step_id=step_id,
                run_id=task.run_id,
                task_id=task.task_id,
                seq=seq,
                agent=task.agent,
                task_type=task.task_type,
                capability_version=cap.version,
                status=result.status,
                input_hash=content_hash(
                    hashable(
                        {
                            "agent": task.agent.value,
                            "type": task.task_type,
                            "params": task.params,
                            "refs": [r.model_dump() for r in refs],
                            "as_of": task.as_of,
                        }
                    )  # fmt: skip
                ),
                output=output,
                tool_calls=[c.as_dict() for c in gw.calls],
                llm_call_ids=list(ctx.llm_call_ids),
                tokens_in=ctx.tokens_in,
                tokens_out=ctx.tokens_out,
                cost_usd=ctx.cost_usd,
                duration_ms=ms,
                error=result.error.model_dump(mode="json") if result.error else None,
            )
            s.backend.runs.add_step(rec)
            s.checkpoint("before_commit")
        return self._summary(rec, seq, state, reused=False)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _fail(
        task: AgentTask, version: str, status: ResultStatus, code: str, msg: str
    ) -> AgentResult:
        return AgentResult(
            task_id=task.task_id, status=status, version=version,
            error=AgentError(code=code, message=msg),
        )  # fmt: skip

    @staticmethod
    def _summary(
        rec: StepRecord, seq: int, state: InvestigationState, *, reused: bool
    ) -> StepSummary:
        o = rec.output
        return StepSummary(
            seq=seq,
            task_id=rec.task_id,
            agent=rec.agent,
            task_type=rec.task_type,
            status=rec.status,
            evidence_ids=list(o["evidence_ids"]),
            new_evidence=len([i for i in o["evidence_ids"] if i not in state.evidence_ids]),
            claim_ids=list(o["claim_ids"]),
            contradiction_ids=list(o["contradiction_ids"]),
            report_ids=list(o["report_ids"]),
            reused=reused,
            tokens=rec.tokens_in + rec.tokens_out,
            cost_usd=rec.cost_usd,
        )
