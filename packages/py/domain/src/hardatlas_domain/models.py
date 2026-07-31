from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class CompatibilityStatus(StrEnum):
    COMPATIBLE = "compatible"
    CONDITIONAL = "conditional"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class ProductRef(ContractModel):
    model_id: str
    sku_id: str | None = None
    revision_id: str | None = None


class VersionContext(ContractModel):
    data_version: str
    rule_version: str


class EvidenceRef(ContractModel):
    id: str
    title: str
    source_level: Literal["A", "B", "C", "D"]
    url: str | None = None
    retrieved_at: datetime
    applies_to: ProductRef


class SpecificationValue(ContractModel):
    definition_id: str
    original_value: str
    normalized_value: str | float | int | bool | None = None
    display_value: str
    unit: str | None = None
    applies_to: ProductRef
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: Annotated[float, Field(ge=0, le=1)]


class ScanComponent(ContractModel):
    local_id: str
    reported_name: str
    mapped_product: ProductRef | None = None
    confidence: Annotated[float, Field(ge=0, le=1)]
    state: Literal["confirmed", "candidate", "unknown", "risk"]
    signals: list[str] = Field(default_factory=list)


class ScanSnapshot(ContractModel):
    id: str
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    platform: Literal["windows", "macos", "linux"]
    components: list[ScanComponent]
    approved_fields: list[str]


class CompatibilityIssue(ContractModel):
    code: str
    status: Literal[
        CompatibilityStatus.CONDITIONAL,
        CompatibilityStatus.INCOMPATIBLE,
        CompatibilityStatus.UNKNOWN,
    ]
    title: str
    explanation: str
    required_actions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class CompatibilityReport(ContractModel):
    id: str
    status: CompatibilityStatus
    subject: list[ProductRef]
    issues: list[CompatibilityIssue] = Field(default_factory=list)
    versions: VersionContext
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
