"""Composition root helpers: the only place the runtime imports concrete capabilities.

Reconciliation hook (docs/PHASE3_4_PLAN.md section 5): register Phase 4's Contradiction
capability HERE (`reg.register(ContradictionCapability(...))`) and merge its SCHEMAS/PROMPTS_DIR
into `default_schemas` / `default_prompts`."""

from pathlib import Path

from pydantic import BaseModel

from aegis.capabilities.behaviour import BehaviourCapability
from aegis.capabilities.graph import GraphCapability
from aegis.capabilities.investigation import PROMPTS_DIR as INV_PROMPTS
from aegis.capabilities.investigation import SCHEMAS as INV_SCHEMAS
from aegis.capabilities.investigation import InvestigationCapability
from aegis.capabilities.reporting import PROMPTS_DIR as REP_PROMPTS
from aegis.capabilities.reporting import SCHEMAS as REP_SCHEMAS
from aegis.capabilities.reporting import ReportingCapability
from aegis.capabilities.transaction import TransactionCapability
from aegis.capabilities.verification import PROMPTS_DIR as VER_PROMPTS
from aegis.capabilities.verification import SCHEMAS as VER_SCHEMAS
from aegis.capabilities.verification import VerificationCapability
from aegis.llm.prompts import PromptRegistry
from aegis.runtime.registry import CapabilityRegistry

PROMPT_DIRS: tuple[Path, ...] = (INV_PROMPTS, VER_PROMPTS, REP_PROMPTS)


def default_registry(*, use_llm: bool = True) -> CapabilityRegistry:
    reg = CapabilityRegistry()
    for cap in (
        TransactionCapability(),
        GraphCapability(),
        BehaviourCapability(),
        InvestigationCapability(),
        VerificationCapability(use_llm=use_llm),
        ReportingCapability(use_llm=use_llm),
    ):
        reg.register(cap)
    return reg


def default_prompts() -> PromptRegistry:
    reg = PromptRegistry()
    for d in PROMPT_DIRS:
        reg.load_dir(d)
    return reg


def default_schemas() -> dict[str, type[BaseModel]]:
    return {**INV_SCHEMAS, **VER_SCHEMAS, **REP_SCHEMAS}
