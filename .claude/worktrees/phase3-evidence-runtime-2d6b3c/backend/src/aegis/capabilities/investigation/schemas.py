from pydantic import BaseModel, ConfigDict, Field

from aegis.domain.evidence import ClaimKind, SelfReportedConfidence, SupportRole


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CitationOut(_Strict):
    evidence_id: str = Field(min_length=1, max_length=80)
    support_role: SupportRole


class ClaimOut(_Strict):
    subject_ref: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=100)
    object_value: str | int | bool | None = None  # decimals as strings: no floats in claims
    unit: str | None = Field(default=None, max_length=40)
    time_scope: str | None = Field(default=None, max_length=100)
    text: str = Field(min_length=1, max_length=1000)
    claim_kind: ClaimKind
    cited: list[CitationOut] = Field(min_length=1, max_length=20)
    confidence: SelfReportedConfidence = SelfReportedConfidence.NONE  # self-reported, ordinal
    er_link_ids: list[str] = Field(default_factory=list, max_length=20)


class InvestigationOutput(_Strict):
    claims: list[ClaimOut] = Field(max_length=60)
    open_questions: list[str] = Field(default_factory=list, max_length=10)


SCHEMAS: dict[str, type[BaseModel]] = {"investigation_output": InvestigationOutput}
