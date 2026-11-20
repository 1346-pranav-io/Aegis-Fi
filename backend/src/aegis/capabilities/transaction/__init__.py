"""Transaction agent (AG-02): deterministic transaction-level facts for the case subject.

Pure computation over rows obtained through the tool gateway (`get_transactions`, which enforces
`as_of`). No LLM. Every number is derived from, and cites, the source transaction ids used.
NOTE: AGENT_SPECIFICATIONS names `analytics.transactions`, which does not exist in Phase 2; the
computation lives in `compute.py` here and can be promoted to analytics later.
"""

from aegis.capabilities.transaction.agent import TransactionCapability

__all__ = ["TransactionCapability"]
