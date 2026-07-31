from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from .knowledge import Citation, EntityRef


class AnswerModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class AnswerEvidence(AnswerModel):
    id: str
    entity: EntityRef
    revision_id: str
    kind: Literal["section", "claim"]
    label: str
    text: str
    citation_ids: list[str] = Field(min_length=1)
    confidence: Annotated[float, Field(ge=0, le=1)]


class AnswerModelProvenance(AnswerModel):
    gateway_id: str
    model: str
    request_id: str
    status: Literal["succeeded", "failed"]
    error_code: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class KnowledgeAnswer(AnswerModel):
    id: str
    question: str
    locale: str
    status: Literal["answered", "insufficient-evidence"]
    mode: Literal[
        "retrieval-synthesis",
        "model-proxy",
        "retrieval-fallback",
    ]
    answer: str
    entities: list[EntityRef] = Field(default_factory=list)
    evidence: list[AnswerEvidence] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    data_versions: list[str] = Field(default_factory=list)
    model_provenance: AnswerModelProvenance | None = None
    fallback_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
