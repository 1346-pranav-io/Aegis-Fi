"""Graph agent (AG-03): deterministic ego-network characterisation over `aegis.analytics.graph`.
No LLM. Truncation by size limits is never silent: it sets completeness TRUNCATED and the step
status PARTIAL."""

from aegis.capabilities.graph.agent import GraphCapability

__all__ = ["GraphCapability"]
