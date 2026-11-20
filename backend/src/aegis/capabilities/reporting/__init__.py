"""Reporting agent (AG-09): template assembly plus LLM narrative under a citation constraint.

Findings contain ONLY claims whose latest verification is VERIFIED; PARTIALLY_SUPPORTED claims go
to a labelled section; everything else is listed as "not findings". Open contradictions and
limitations are always present. Narrative text is accepted only if every item cites verified
claim ids; otherwise it is dropped and the deterministic claim list stands alone."""

from pathlib import Path

from aegis.capabilities.reporting.agent import ReportingCapability, build_report_body
from aegis.capabilities.reporting.schemas import SCHEMAS

PROMPTS_DIR = Path(__file__).parent / "prompts"

__all__ = ["PROMPTS_DIR", "SCHEMAS", "ReportingCapability", "build_report_body"]
