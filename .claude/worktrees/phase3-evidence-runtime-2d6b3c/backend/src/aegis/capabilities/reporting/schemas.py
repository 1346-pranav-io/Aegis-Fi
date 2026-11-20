from pydantic import BaseModel, ConfigDict, Field


class NarrativeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=800)
    claim_ids: list[str] = Field(default_factory=list, max_length=20)


class ReportNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NarrativeItem] = Field(max_length=30)


SCHEMAS: dict[str, type[BaseModel]] = {"report_narrative": ReportNarrative}
