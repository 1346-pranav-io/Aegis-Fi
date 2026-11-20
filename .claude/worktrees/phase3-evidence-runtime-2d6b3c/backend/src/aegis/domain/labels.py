"""Ground-truth label contract. Isolated so feature/analytics code cannot import it (T-INV-14)."""

from aegis.domain.source import Label

__all__ = ["Label"]
