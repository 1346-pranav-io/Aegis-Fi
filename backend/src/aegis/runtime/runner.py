"""Start, run and resume investigation runs (FR-ORC-01..05)."""

import uuid
from datetime import datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from aegis.common.errors import IntegrityError, NotFound
from aegis.domain.agents import Budget
from aegis.runtime.graph import build_graph
from aegis.runtime.policies import OrchestrationPolicy
from aegis.runtime.registry import CapabilityRegistry
from aegis.runtime.services import RuntimeServices
from aegis.runtime.state import TERMINAL, InvestigationState, Signal


class InvestigationRunner:
    def __init__(
        self,
        services: RuntimeServices,
        registry: CapabilityRegistry,
        policy: OrchestrationPolicy,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        self._s = services
        self._policy = policy
        self._cp = checkpointer
        self._graph = build_graph(
            services=services, registry=registry, policy=policy, checkpointer=checkpointer
        )

    def start(
        self,
        *,
        case_id: str,
        as_of: datetime,
        budget: Budget,
        run_id: str | None = None,
        reverify_claim_ids: list[str] | None = None,
    ) -> str:
        """Create a QUEUED run. Typed signals come from the alert's reason codes (Phase 2)."""
        summary = self._s.cases.summary(case_id)
        run_id = run_id or f"run_{uuid.uuid4().hex[:26]}"
        signals = [Signal(code=c) for c in summary.reason_codes]
        with self._s.backend.transaction():
            self._s.backend.runs.create_run(
                run_id=run_id,
                case_id=case_id,
                as_of=as_of,
                policy_name=self._policy.name,
                policy_version=self._policy.version,
                budget=budget.model_dump(mode="json"),
                config={
                    "subject_ref": summary.subject_ref,
                    "signals": [s.model_dump(mode="json") for s in signals],
                    **(
                        {"reverify_claim_ids": list(reverify_claim_ids)}
                        if reverify_claim_ids
                        else {}
                    ),
                },
            )
        return run_id

    def _initial(self, run_id: str) -> InvestigationState:
        info = self._s.backend.runs.run_info(run_id)
        cfg = info["config"]
        claim_ids: list[str] = list(cfg.get("reverify_claim_ids", []))
        evidence_ids: list[str] = []
        for (
            cid
        ) in claim_ids:  # a re-verification run starts from existing claims and their evidence
            for cit in self._s.backend.evidence.get_claim(cid).draft.citations:
                if cit.evidence_id not in evidence_ids:
                    evidence_ids.append(cit.evidence_id)
        return InvestigationState(
            claim_ids=claim_ids,
            evidence_ids=evidence_ids,
            run_id=run_id,
            case_id=info["case_id"],
            as_of=info["as_of"],
            subject_ref=cfg["subject_ref"],
            policy_name=info["policy_name"],
            policy_version=info["policy_version"],
            budget=Budget.model_validate(info["budget"]),
            signals=[Signal.model_validate(s) for s in cfg["signals"]],
        )

    def run(self, run_id: str) -> InvestigationState:
        """Run to a terminal status, or resume a run that was killed part-way. Resuming reuses
        recorded steps, so nothing is executed (or paid for) twice."""
        try:
            status = self._s.backend.runs.status(run_id)
        except KeyError:
            raise NotFound(f"run {run_id}") from None
        config: Any = {"configurable": {"thread_id": run_id}}
        if status in TERMINAL:
            return self._final(run_id, config)
        resume = self._cp is not None and bool(self._graph.get_state(config).values)
        if resume:
            self._check_resume_state(config)
            self._graph.invoke(None, config)
        else:
            self._graph.invoke({"s": self._initial(run_id).model_dump(mode="json")}, config)
        return self._final(run_id, config)

    def _check_resume_state(self, config: Any) -> None:
        """Checkpoint rows are writable by the application login (migration 0024 cannot make them
        immutable), so a resumed state is not trusted: every evidence and claim id in it must exist
        and belong to this case, or the run refuses to continue."""
        st = InvestigationState.model_validate(self._graph.get_state(config).values["s"])
        store = self._s.backend.evidence
        try:
            for eid in st.evidence_ids:
                if store.get_evidence(eid).draft.case_id != st.case_id:
                    raise IntegrityError(f"checkpoint names evidence {eid} of another case")
            for cid in st.claim_ids:
                if store.get_claim(cid).draft.case_id != st.case_id:
                    raise IntegrityError(f"checkpoint names claim {cid} of another case")
        except NotFound as exc:
            raise IntegrityError(f"checkpoint names a record that does not exist: {exc}") from exc

    def _final(self, run_id: str, config: Any) -> InvestigationState:
        if self._cp is not None:
            vals = self._graph.get_state(config).values
            if vals:
                return InvestigationState.model_validate(vals["s"])
        st = self._initial(run_id)
        st.status = self._s.backend.runs.status(run_id)
        return st
