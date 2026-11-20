"""Token / cost / call budgets for the gateway (FR-ORC-02, T-INV-15). Budgets nest: a step budget
charges its parent run budget too, so a step cannot spend what the run no longer has."""

from dataclasses import dataclass, field

from aegis.common.errors import BudgetExceeded


@dataclass
class BudgetTracker:
    max_tokens: int
    max_cost_usd: float
    max_calls: int = 1_000_000
    parent: "BudgetTracker | None" = None
    tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    _children: list["BudgetTracker"] = field(default_factory=list, repr=False)

    def child(
        self, max_tokens: int, max_cost_usd: float, max_calls: int = 1_000_000
    ) -> "BudgetTracker":
        return BudgetTracker(max_tokens, max_cost_usd, max_calls, parent=self)

    def remaining_tokens(self) -> int:
        r = self.max_tokens - self.tokens
        return min(r, self.parent.remaining_tokens()) if self.parent else r

    def check(self, tokens_needed: int) -> None:
        """Refuse BEFORE a call that cannot fit (estimated tokens), or once cost/calls are spent."""
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"call budget exhausted ({self.max_calls})")
        if self.cost_usd > self.max_cost_usd:
            raise BudgetExceeded(f"cost budget exhausted ({self.max_cost_usd} USD)")
        if self.tokens + tokens_needed > self.max_tokens:
            raise BudgetExceeded(
                f"token budget would be exceeded ({self.tokens}+{tokens_needed}>{self.max_tokens})"
            )
        if self.parent:
            self.parent.check(tokens_needed)

    def charge(self, tokens: int, cost_usd: float) -> None:
        self.tokens += tokens
        self.cost_usd += cost_usd
        self.calls += 1
        if self.parent:
            self.parent.charge(tokens, cost_usd)
