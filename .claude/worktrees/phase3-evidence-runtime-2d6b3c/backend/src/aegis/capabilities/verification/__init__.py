"""Verification agent (AG-07, D-08): independent verification of claims against cited evidence.

Order: deterministic checks first (existence, hash intactness, entity/number/date/as_of/role
checks), then ONE semantic entailment call that sees only the claim and its cited evidence.
It never sees the investigator's reasoning, other claims, or other verification results."""

from pathlib import Path

from aegis.capabilities.verification.agent import VerificationCapability, derive_status
from aegis.capabilities.verification.schemas import SCHEMAS

PROMPTS_DIR = Path(__file__).parent / "prompts"

__all__ = ["PROMPTS_DIR", "SCHEMAS", "VerificationCapability", "derive_status"]
