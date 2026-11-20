from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Verdict(StrEnum):
    ENTAILS = "ENTAILS"
    PARTIAL = "PARTIAL"
    NOT_ENTAILED = "NOT_ENTAILED"
    CONTRADICTS = "CONTRADICTS"


class EntailmentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    quoted_span: str = Field(max_length=500)
    rationale: str = Field(max_length=1000)


SCHEMAS: dict[str, type[BaseModel]] = {"entailment": EntailmentOut}
