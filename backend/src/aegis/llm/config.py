"""Gateway configuration. Model ids live here (loaded from a file or the environment), never in
code (D-16). There is deliberately no default model id: an unconfigured gateway refuses to run."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from aegis.common.errors import AegisValidationError


class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1)
    price_in_per_1k_usd: float = Field(ge=0, default=0.0)
    price_out_per_1k_usd: float = Field(ge=0, default=0.0)


class LlmConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    models: dict[str, ModelSpec]  # role -> model (e.g. investigation, verification, reporting)
    timeout_s: float = Field(gt=0, default=60.0)
    max_retries: int = Field(ge=0, le=5, default=2)  # transient provider errors only
    max_repair_rounds: int = Field(ge=0, le=3, default=2)
    max_output_tokens: int = Field(gt=0, default=2048)
    backoff_base_s: float = Field(ge=0, default=0.5)
    pseudonym_key_env: str = "AEGIS_PSEUDONYM_KEY"

    def model_for(self, role: str) -> ModelSpec:
        try:
            return self.models[role]
        except KeyError:
            raise AegisValidationError(f"no model configured for role {role!r}") from None

    @classmethod
    def from_file(cls, path: Path) -> "LlmConfig":
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
