"""Investigation agent (AG-06): LLM synthesis of evidence into structured, cited claims.

Reasons ONLY over evidence supplied in its task (no READ_SOURCE), calls the model only through
the tool gateway, and can only create claims citing evidence ids in its own context. Claims that
cite anything else are rejected before storage and reported (step status PARTIAL)."""

from pathlib import Path

from aegis.capabilities.investigation.agent import InvestigationCapability
from aegis.capabilities.investigation.schemas import SCHEMAS

PROMPTS_DIR = Path(__file__).parent / "prompts"

__all__ = ["PROMPTS_DIR", "SCHEMAS", "InvestigationCapability"]
