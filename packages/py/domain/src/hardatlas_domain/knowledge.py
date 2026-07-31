from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class LocalizedText(KnowledgeModel):
    locale: str
    value: str
    machine_generated: bool = False


class KnowledgeSpace(KnowledgeModel):
    id: str
    slug: str
    name: list[LocalizedText]
    description: list[LocalizedText]
    root_taxonomy_node_ids: list[str]
    icon_key: str
    status: Literal["draft", "published", "archived"]


class TaxonomyNode(KnowledgeModel):
    id: str
    space_id: str
    slug: str
    name: list[LocalizedText]
    parent_ids: list[str]
    child_count: int = Field(ge=0)
    entity_count: int = Field(ge=0)
    path_keys: list[str]


class AttributeDataType(StrEnum):
    TEXT = "text"
    RICH_TEXT = "rich-text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATE_RANGE = "date-range"
    MEASUREMENT = "measurement"
    ENUM = "enum"
    ENTITY_REF = "entity-ref"
    MEDIA_REF = "media-ref"
    GEO = "geo"
    JSON = "json"


class AttributeDefinition(KnowledgeModel):
    id: str
    key: str
    name: list[LocalizedText]
    data_type: AttributeDataType
    cardinality: Literal["one", "many"]
    required: bool
    unit_family: str | None = None
    enum_values: list[str] | None = None
    validation: dict[str, Any] = Field(default_factory=dict)
    schema_version: str


class EntityType(KnowledgeModel):
    id: str
    space_id: str
    key: str
    name: list[LocalizedText]
    description: list[LocalizedText]
    allowed_taxonomy_node_ids: list[str] = Field(default_factory=list)
    attribute_definition_ids: list[str]
    allowed_relationship_type_ids: list[str]
    default_view_definition_id: str
    schema_version: str


class EntityRef(KnowledgeModel):
    id: str
    slug: str
    type_id: str
    canonical_name: str


class Citation(KnowledgeModel):
    id: str
    source_id: str
    source_title: str
    source_url: str | None = None
    source_tier: Literal["primary", "authoritative", "secondary", "community"]
    retrieved_at: datetime
    locator: str | None = None
    quote_hash: str | None = None


class ClaimValue(KnowledgeModel):
    id: str
    attribute_definition_id: str
    original_value: Any
    normalized_value: Any | None = None
    display_value: list[LocalizedText]
    unit: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    confidence: Annotated[float, Field(ge=0, le=1)]
    citation_ids: list[str]
    revision_id: str


class ContentSection(KnowledgeModel):
    id: str
    key: str
    heading: list[LocalizedText]
    body: list[LocalizedText]
    citation_ids: list[str]
    order: int


class RelationshipType(KnowledgeModel):
    id: str
    key: str
    name: list[LocalizedText]
    inverse_name: list[LocalizedText]
    directed: bool
    source_entity_type_ids: list[str] = Field(default_factory=list)
    target_entity_type_ids: list[str] = Field(default_factory=list)
    source_cardinality: Literal["one", "many"] = "many"
    target_cardinality: Literal["one", "many"] = "many"
    qualifier_schema: dict[str, Any] = Field(default_factory=dict)
    evidence_required: bool = True
    schema_version: str


class Relationship(KnowledgeModel):
    id: str
    type_id: str
    source: EntityRef
    target: EntityRef
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    confidence: Annotated[float, Field(ge=0, le=1)]
    citation_ids: list[str]
    revision_id: str


class ViewBlock(KnowledgeModel):
    id: str
    type: Literal[
        "hero",
        "summary",
        "classification",
        "attribute-table",
        "relationship-list",
        "timeline",
        "map",
        "media-gallery",
        "citations",
    ]
    title: list[LocalizedText] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    visible_when: dict[str, Any] | None = None


class ViewDefinition(KnowledgeModel):
    id: str
    entity_type_id: str
    schema_version: str
    blocks: list[ViewBlock]


class RevisionContext(KnowledgeModel):
    revision_id: str
    data_version: str
    schema_version: str
    policy_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnowledgeEntity(KnowledgeModel):
    ref: EntityRef
    names: list[LocalizedText]
    aliases: list[LocalizedText]
    description: list[LocalizedText]
    taxonomy_node_ids: list[str]
    claims: list[ClaimValue]
    sections: list[ContentSection]
    relationships: list[Relationship]
    citations: list[Citation]
    revision: RevisionContext
    publication_status: Literal["draft", "review", "published", "archived"]


class AgentRole(StrEnum):
    SOURCE_MONITOR = "source-monitor"
    ACQUISITION = "acquisition"
    EXTRACTION = "extraction"
    ENTITY_RESOLUTION = "entity-resolution"
    NORMALIZATION = "normalization"
    TRANSLATION = "translation"
    EVIDENCE_VERIFICATION = "evidence-verification"
    CONFLICT_ANALYSIS = "conflict-analysis"
    QUALITY_ASSURANCE = "quality-assurance"


class ChangeOperation(KnowledgeModel):
    operation: Literal["add", "replace", "remove", "merge"]
    path: str
    before: Any | None = None
    after: Any | None = None
    citation_ids: list[str]
    confidence: Annotated[float, Field(ge=0, le=1)]
    machine_generated: bool = False


class AgentProposal(KnowledgeModel):
    id: str
    entity_id: str | None = None
    proposal_type: Literal["content", "relation", "schema", "translation", "merge"]
    operations: list[ChangeOperation]
    risk: Literal["low", "medium", "high", "critical"]
    status: Literal[
        "proposed",
        "policy-approved",
        "policy-blocked",
        "human-review",
        "accepted",
        "rejected",
        "superseded",
        "released",
    ]
    agent_run_id: str
    impact: dict[str, int]


class AgentRun(KnowledgeModel):
    id: str
    role: AgentRole
    status: Literal["queued", "running", "waiting-review", "completed", "failed"]
    model_version: str
    prompt_version: str
    policy_version: str
    input_source_ids: list[str]
    proposal_ids: list[str]
    started_at: datetime | None = None
    completed_at: datetime | None = None
