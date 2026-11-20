"""Behaviour agent (AG-04): self-history and peer deviation over `aegis.analytics.behaviour`.

No LLM. Insufficient history is reported as evidence with completeness INSUFFICIENT - never a
guess. BEHAV_MODEL_SCORE is NOT produced in Phase 3: it needs the registered-model tool
(`get_model`, Postgres ml.model_artefact registry), which does not exist yet; unregistered
models are refused by design (AG-04)."""

from aegis.capabilities.behaviour.agent import BehaviourCapability

__all__ = ["BehaviourCapability"]
