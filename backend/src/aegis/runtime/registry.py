"""Capability registry and agent catalog. Capabilities never import each other; the runtime looks
them up by name here (AGENT_ARCHITECTURE 3)."""

from dataclasses import dataclass

from aegis.common.errors import AegisValidationError, NotFound
from aegis.domain.agents import AgentName, Capability

# Relative cost estimates (abstract units) used by the cost-aware allocator. They are planning
# weights, not measurements.
DEFAULT_COST_UNITS: dict[AgentName, int] = {
    AgentName.TRANSACTION: 1,
    AgentName.GRAPH: 2,
    AgentName.BEHAVIOUR: 2,
    AgentName.INTELLIGENCE: 2,
    AgentName.INVESTIGATION: 10,
    AgentName.VERIFICATION: 8,
    AgentName.CONTRADICTION: 2,
    AgentName.REPORTING: 6,
}


@dataclass(frozen=True)
class CatalogEntry:
    agent: AgentName
    version: str
    cost_units: int


class AgentCatalog:
    def __init__(self, entries: dict[AgentName, CatalogEntry]) -> None:
        self._e = entries

    def has(self, agent: AgentName) -> bool:
        return agent in self._e

    def cost(self, agent: AgentName) -> int:
        return self._e[agent].cost_units

    def agents(self) -> list[AgentName]:
        return sorted(self._e, key=lambda a: a.value)


class CapabilityRegistry:
    def __init__(self) -> None:
        self._c: dict[AgentName, Capability] = {}

    def register(self, cap: Capability) -> None:
        if cap.name is AgentName.ORCHESTRATOR:
            raise AegisValidationError("the orchestrator is a policy, not a capability")
        if cap.name in self._c:
            raise AegisValidationError(f"capability {cap.name.value} already registered")
        self._c[cap.name] = cap

    def get(self, name: AgentName) -> Capability:
        try:
            return self._c[name]
        except KeyError:
            raise NotFound(f"no capability registered for {name.value}") from None

    def catalog(self, costs: dict[AgentName, int] | None = None) -> AgentCatalog:
        costs = costs or DEFAULT_COST_UNITS
        return AgentCatalog(
            {n: CatalogEntry(n, c.version, costs.get(n, 1)) for n, c in self._c.items()}
        )
