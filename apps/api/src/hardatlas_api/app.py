import asyncio
import hashlib
import glob
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from hardatlas_ai import (
    AgentDefinition,
    AgentGraphRunner,
    AgentGraphSpec,
    AgentRegistry,
    GraphRun,
    GraphRunSchedule,
    ModelGateway,
    ModelGatewayConfig,
    OpenAICompatibleModelGateway,
    core_handlers,
    load_agent_pack,
)
from hardatlas_data import (
    KnowledgeAnsweringService,
    KnowledgeRepository,
    LexicalSearchBackend,
    OpenSearchBackend,
    ProposalConcurrencyError,
    QualityMaintenanceService,
    QualityScanResult,
    ReleaseOrchestrationError,
    ReleaseOrchestrator,
    ReleaseVerificationError,
    ReleaseVerifier,
    SearchBackend,
)
from hardatlas_domain import (
    AgentProposal,
    AgentRun,
    AgentRuntime,
    AgentRuntimeState,
    AttributeDefinition,
    AuditEvent,
    AuthoringDraftRecord,
    ChangeOperation,
    Citation,
    CompatibilityReport,
    CompatibilityStatus,
    ConflictMergeError,
    DomainPackDiff,
    DomainPackRelease,
    DomainPackRollbackSnapshot,
    DomainPackValidation,
    DomainRegistry,
    DraftIssue,
    DraftValidation,
    EntityDraft,
    EntityRef,
    EntityType,
    ExtractionBatch,
    ExtractionCandidate,
    ExtractionParserDefinition,
    GovernanceError,
    GovernanceService,
    GovernedProposal,
    KnowledgeAnswer,
    KnowledgeEntity,
    KnowledgeSpace,
    MaintenanceEvidenceLink,
    MaintenanceOutputLink,
    MaintenanceTask,
    MaintenanceWorkItem,
    Permission,
    Principal,
    ProductRef,
    ProposalConflict,
    ProposalConflictResolution,
    QualityAssessment,
    QualityIssue,
    QualityProfile,
    Relationship,
    RelationshipType,
    ReleaseManifest,
    RevisionContext,
    SavedCollection,
    SavedCollectionItem,
    SchemaChangeAnalysis,
    SchemaMigrationManifest,
    SchemaMigrationOperation,
    SourceAcquisitionJob,
    SourceDefinition,
    SourcePolicyDecision,
    SourceSnapshot,
    TaxonomyNode,
    ViewDefinition,
    analyze_schema_change,
    apply_schema_migration_to_entity,
    build_conflict_merge_proposal,
    build_creation_proposal,
    build_draft_from_entity,
    build_evidence_backed_maintenance_proposal,
    build_revision_proposal,
    build_taxonomy_work_proposal,
    build_translation_work_proposal,
    compare_domain_packs,
    detect_proposal_conflicts,
    evaluate_source_policy,
    load_domain_registry,
    maintenance_route_capabilities,
    read_entity_operation_value,
    validate_domain_pack,
    validate_entity_draft,
    validate_relationship,
)
from hardatlas_ingestion import (
    AcquisitionError,
    ExtractionError,
    validate_acquisition_url,
    ParserRegistry,
    load_parser_registry,
)
from hardatlas_rules import CompatibilityRule, Predicate, RuleEngine
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from .auth import ApiAuthenticator, AuthenticationError
from .config import Settings, get_settings
from .fixtures import EVIDENCE
from .knowledge_fixtures import (
    AGENT_RUNS,
    ENTITIES,
    PROPOSALS,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class HealthResponse(ApiModel):
    status: str
    service: str


class ReadinessResponse(ApiModel):
    status: Literal["ready", "not-ready"]
    checks: dict[str, bool]
    data_version: str


class BuildResponse(ApiModel):
    build_sha: str
    environment: str
    data_version: str
    schema_version: str
    policy_version: str


class SearchItem(ApiModel):
    entity: KnowledgeEntity
    matched_text: str
    matched_field: str
    score: float
    fixture: bool = False


class SearchResponse(ApiModel):
    query: str
    items: list[SearchItem]
    total: int
    offset: int
    limit: int
    facets: "SearchFacets"


class SearchFacetValue(ApiModel):
    value: str
    label: str
    count: int


class SearchFacets(ApiModel):
    spaces: list[SearchFacetValue]
    entity_types: list[SearchFacetValue]
    taxonomy_nodes: list[SearchFacetValue]
    locales: list[SearchFacetValue]
    source_tiers: list[SearchFacetValue]


class SearchStatusResponse(ApiModel):
    backend: str
    ready: bool
    index_alias: str | None = None


class KnowledgeAnswerRequest(ApiModel):
    question: str = Field(min_length=2, max_length=1_000)
    locale: str = Field(default="zh-CN", min_length=2, max_length=40)
    mode: Literal["auto", "retrieval-only"] = "auto"


class HardwareCompatibilityRequest(ApiModel):
    subjects: list[ProductRef]
    facts: dict[str, object]


class ReviewRequest(ApiModel):
    decision: Literal["approve", "reject"]
    comment: str


ProposalStatus = Literal[
    "proposed",
    "policy-approved",
    "policy-blocked",
    "human-review",
    "accepted",
    "rejected",
    "superseded",
    "released",
]
ProposalRisk = Literal["low", "medium", "high", "critical"]
ProposalType = Literal["content", "relation", "schema", "translation", "merge"]


class BulkEvaluationRequest(ApiModel):
    proposal_ids: list[str] = Field(min_length=1, max_length=100)


class BulkEvaluationResponse(ApiModel):
    proposals: list[GovernedProposal]


class BulkReviewRequest(ReviewRequest):
    proposal_ids: list[str] = Field(min_length=1, max_length=100)


class BulkReviewResponse(ApiModel):
    proposals: list[GovernedProposal]


class ProposalQueueItem(ApiModel):
    governed: GovernedProposal
    conflict_ids: list[str] = Field(default_factory=list)


class ProposalQueueResponse(ApiModel):
    items: list[ProposalQueueItem]
    conflicts: list[ProposalConflict]
    total: int
    offset: int
    limit: int
    has_more: bool
    total_conflicts: int
    status_counts: dict[str, int]


class ProposalMergeRequest(ApiModel):
    proposal_id: str = Field(min_length=1, max_length=160)
    agent_run_id: str = Field(min_length=1, max_length=240)
    resolutions: list[ProposalConflictResolution] = Field(
        min_length=1,
        max_length=100,
    )
    comment: str = Field(min_length=1, max_length=2_000)


class ProposalMergeResponse(ApiModel):
    proposal: GovernedProposal
    superseded_proposals: list[GovernedProposal]


class ReleaseRequest(ApiModel):
    id: str
    proposal_ids: list[str]
    data_version: str
    schema_versions: list[str]
    previous_release_id: str


class AgentGraphRunRequest(ApiModel):
    run_id: str
    source_id: str
    snapshot_hash: str
    entity_id: str
    label: str
    field_path: str
    proposed_value: object
    citation_id: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    risk: Literal["low", "medium", "high", "critical"] = "medium"
    source_excerpt: str | None = Field(default=None, max_length=50_000)
    candidate_entity_ids: list[str] = Field(default_factory=list, max_length=100)


class AgentGraphScheduleRequest(ApiModel):
    id: str
    trigger_type: Literal["manual", "source-change", "schedule", "schema-change"]
    input: dict[str, object]
    idempotency_key: str = Field(min_length=8, max_length=240)


class SchemaRegistryResponse(ApiModel):
    entity_types: list[EntityType]
    attribute_definitions: list[AttributeDefinition]
    relationship_types: list[RelationshipType]
    view_definitions: list[ViewDefinition]
    quality_profiles: list[QualityProfile]


class DomainPackDraftRequest(ApiModel):
    id: str
    name: str
    version: str
    kernel_version: str
    locales: list[str] = Field(min_length=1)
    spaces: list[KnowledgeSpace] = Field(default_factory=list)
    taxonomy_nodes: list[TaxonomyNode] = Field(default_factory=list)
    entity_types: list[EntityType] = Field(default_factory=list)
    attributes: list[AttributeDefinition] = Field(default_factory=list)
    relationship_types: list[RelationshipType] = Field(default_factory=list)
    views: list[ViewDefinition] = Field(default_factory=list)
    quality_profiles: list[QualityProfile] = Field(default_factory=list)
    citation_ids: list[str] = Field(min_length=1)


class DomainPackRollbackBlocker(ApiModel):
    code: str
    resource_id: str
    dependent_ids: list[str] = Field(default_factory=list)
    message: str


class DomainPackRollbackAnalysis(ApiModel):
    pack_id: str
    version: str
    restore_version: str | None = None
    safe: bool
    blockers: list[DomainPackRollbackBlocker]
    manifest_diff: DomainPackDiff


class DomainPackRollbackRequest(ApiModel):
    expected_published_at: datetime
    comment: str = Field(min_length=1, max_length=2_000)


class DomainPackRollbackResponse(ApiModel):
    rolled_back: DomainPackRelease
    restored: DomainPackRelease | None = None
    analysis: DomainPackRollbackAnalysis


class RelationshipTraversalItem(ApiModel):
    relationship: Relationship
    relationship_type: RelationshipType
    direction: Literal["outgoing", "incoming"]
    neighbor: EntityRef


class EntityRevisionSummary(ApiModel):
    revision: RevisionContext
    current: bool


class SchemaChangeAnalysisRequest(ApiModel):
    schema_kind: Literal[
        "attribute-definition",
        "entity-type",
        "relationship-type",
        "view-definition",
    ]
    schema_id: str
    proposed_document: dict[str, object]


class SchemaMigrationPlanRequest(SchemaChangeAnalysisRequest):
    id: str
    proposal_id: str
    operations: list[SchemaMigrationOperation]
    citation_ids: list[str] = Field(min_length=1)
    confidence: Annotated[float, Field(ge=0.85, le=1)] = 0.95
    data_version: str


class EntityDraftProposalRequest(ApiModel):
    proposal_id: str
    agent_run_id: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    risk: Literal["low", "medium", "high", "critical"] = "medium"
    draft: EntityDraft


class EntityRevisionProposalRequest(EntityDraftProposalRequest):
    base_revision_id: str


class EntityAuthoringDraftResponse(ApiModel):
    draft: EntityDraft
    base_revision_id: str
    entity_type: EntityType
    attribute_definitions: list[AttributeDefinition]
    view_definition: ViewDefinition


class AuthoringDraftCreateRequest(ApiModel):
    id: str = Field(min_length=1, max_length=160)
    mode: Literal["create", "revise"]
    draft: EntityDraft
    base_revision_id: str | None = None


class AuthoringDraftUpdateRequest(ApiModel):
    expected_version: int = Field(ge=1)
    draft: EntityDraft


class AuthoringDraftSubmitRequest(ApiModel):
    expected_version: int = Field(ge=1)
    proposal_id: str = Field(min_length=1, max_length=160)
    agent_run_id: str = Field(min_length=1, max_length=240)
    confidence: Annotated[float, Field(ge=0, le=1)]
    risk: Literal["low", "medium", "high", "critical"] = "medium"


class AuthoringDraftAbandonRequest(ApiModel):
    expected_version: int = Field(ge=1)


class AuthoringDraftSubmissionResponse(ApiModel):
    draft: AuthoringDraftRecord
    proposal: GovernedProposal


class SourceRegistryRecord(ApiModel):
    source: SourceDefinition
    policy: SourcePolicyDecision


class SourceAcquisitionRequest(ApiModel):
    id: str
    url: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=240)


class SourcePipelineItem(ApiModel):
    stage: Literal[
        "acquisition",
        "snapshot",
        "extraction",
        "candidate",
        "agent-schedule",
    ]
    id: str
    parent_id: str | None = None
    status: str
    replayable: bool = False
    error: str | None = None
    graph_id: str | None = None
    updated_at: datetime


class SourcePipelineResponse(ApiModel):
    source_id: str
    items: list[SourcePipelineItem]
    pending_event_count: int
    stalled_count: int


class PipelineReplayResponse(ApiModel):
    accepted: bool
    stage: Literal["acquisition", "extraction", "finalization", "agent-schedule"]
    resource_id: str
    outbox_event_id: str


class CollectionCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class CollectionItemSaveRequest(ApiModel):
    entity_id: str = Field(min_length=1, max_length=160)
    entity_revision_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )
    note: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class CollectionRemoveResponse(ApiModel):
    removed: bool


class AuditStatusResponse(ApiModel):
    valid: bool
    event_count: int
    latest_hash: str | None = None


class OperationsAgentItem(ApiModel):
    id: str
    name: str
    role: str
    model_policy: str
    version: str


class OperationsProposalItem(ApiModel):
    id: str
    entity_id: str | None = None
    entity_name: str
    proposal_type: str
    risk: str
    status: str
    operation_count: int
    conflict_count: int = 0
    impact: dict[str, object]


class OperationsScheduleItem(ApiModel):
    id: str
    graph_id: str
    graph_version: str
    trigger_type: str
    status: str
    requested_by: str
    created_at: datetime


class OperationsSummaryResponse(ApiModel):
    generated_at: datetime
    data_version: str
    agent_definition_count: int
    active_schedule_count: int
    proposal_count: int
    review_queue_count: int
    high_risk_review_count: int
    proposal_conflict_count: int
    evidence_coverage_percent: float
    source_count: int
    active_source_count: int
    pending_acquisition_count: int
    pending_outbox_count: int
    agents: list[OperationsAgentItem]
    proposals: list[OperationsProposalItem]
    schedules: list[OperationsScheduleItem]
    latest_release: ReleaseManifest | None = None


class QualityScanRequest(ApiModel):
    entity_ids: list[str] = Field(default_factory=list, max_length=500)


class QualitySummaryResponse(ApiModel):
    generated_at: datetime
    assessed_entity_count: int
    healthy_count: int
    attention_count: int
    critical_count: int
    average_score: float
    open_task_count: int
    scheduled_task_count: int
    tasks_by_action: dict[str, int]


class MaintenanceWorkItemView(ApiModel):
    id: str
    maintenance_task_id: str
    assessment_id: str
    entity: EntityRef
    revision_id: str
    action: str
    issue: QualityIssue
    route: Literal[
        "source-acquisition",
        "translation-evidence",
        "taxonomy-review",
    ]
    required_capabilities: list[str]
    requires_evidence: bool
    proposal_eligible: bool
    status: Literal[
        "queued",
        "ready",
        "claimed",
        "blocked",
        "completed",
        "superseded",
    ]
    priority: Literal["low", "medium", "high", "critical"]
    triage_schedule_id: str
    triage_run_id: str
    assignee_id: str | None = None
    lease_expires_at: datetime | None = None
    attempt: int
    blocked_reason: str | None = None
    evidence_refs: list[MaintenanceEvidenceLink]
    output_refs: list[MaintenanceOutputLink]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class MaintenanceWorkClaimRequest(ApiModel):
    lease_seconds: int = Field(default=300, ge=30, le=3600)


class MaintenanceWorkClaimResponse(ApiModel):
    work_item: MaintenanceWorkItemView
    lease_token: str


class MaintenanceWorkHeartbeatRequest(ApiModel):
    lease_token: str = Field(min_length=1, max_length=160)
    lease_seconds: int = Field(default=300, ge=30, le=3600)


class MaintenanceWorkReleaseRequest(ApiModel):
    lease_token: str = Field(min_length=1, max_length=160)


class MaintenanceWorkCompleteRequest(ApiModel):
    lease_token: str = Field(min_length=1, max_length=160)
    evidence_refs: list[MaintenanceEvidenceLink] = Field(
        min_length=1,
        max_length=100,
    )
    output_refs: list[MaintenanceOutputLink] = Field(
        default_factory=list,
        max_length=100,
    )


class MaintenanceWorkBlockRequest(ApiModel):
    lease_token: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2_000)


class MaintenanceTranslationProposalRequest(ApiModel):
    lease_token: str = Field(min_length=1, max_length=160)
    target_locale: str = Field(min_length=2, max_length=40)
    translated_name: str | None = Field(default=None, min_length=1, max_length=500)
    translated_description: str | None = Field(
        default=None,
        min_length=1,
        max_length=10_000,
    )
    citation_ids: list[str] = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)


class MaintenanceTaxonomyProposalRequest(ApiModel):
    lease_token: str = Field(min_length=1, max_length=160)
    taxonomy_node_id: str = Field(min_length=1, max_length=160)
    citation_ids: list[str] = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)


class MaintenanceProposalSubmissionResponse(ApiModel):
    work_item: MaintenanceWorkItemView
    proposal: GovernedProposal


class AgentRuntimeRegistrationRequest(ApiModel):
    definition_id: str = Field(min_length=1, max_length=160)
    definition_version: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(min_length=1, max_length=100)
    supported_routes: list[
        Literal[
            "source-acquisition",
            "translation-evidence",
            "taxonomy-review",
        ]
    ] = Field(min_length=1, max_length=3)
    status: Literal["online", "draining"] = "online"
    max_concurrency: int = Field(default=1, ge=1, le=100)
    heartbeat_ttl_seconds: int = Field(default=90, ge=30, le=600)
    labels: dict[str, str] = Field(default_factory=dict)


class AgentRuntimeHeartbeatRequest(ApiModel):
    status: Literal["online", "draining"] | None = None


HARDWARE_RULES = [
    CompatibilityRule(
        id="hardware.socket.match",
        version="hardware-rules-2026.07",
        when=[Predicate(field="socket_match", operator="eq", value=True)],
        status=CompatibilityStatus.COMPATIBLE,
        title="插槽匹配",
        explanation="硬件扩展确认处理器与主板插槽相同。",
        evidence=[EVIDENCE],
    )
]


class RepositoryCheckpointStore:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    def load(self, run_id: str) -> GraphRun | None:
        document = self.repository.get_agent_graph_run(run_id)
        return GraphRun.model_validate(document) if document else None

    def save(self, run: GraphRun) -> None:
        self.repository.save_agent_graph_run(
            run_id=run.id,
            graph_id=run.graph_id,
            status=run.status,
            document=run.model_dump(mode="json", by_alias=True),
        )


def build_repository(settings: Settings) -> KnowledgeRepository:
    database_url = settings.database_url
    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        engine_options["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            engine_options["poolclass"] = StaticPool
        elif "///" in database_url:
            database_path = Path(database_url.split("///", 1)[1])
            if not database_path.is_absolute():
                database_path = Path.cwd() / database_path
            database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, **engine_options)
    return KnowledgeRepository(engine)


def build_search_backend(
    settings: Settings,
    repository: KnowledgeRepository,
    domain_registry: DomainRegistry | None = None,
) -> SearchBackend:
    entity_type_spaces = (
        {
            entity_type.id: entity_type.space_id
            for entity_type in domain_registry.entity_types.values()
        }
        if domain_registry is not None
        else {}
    )
    if settings.search_backend == "memory":
        return LexicalSearchBackend(
            repository.list_entities(),
            entity_type_spaces=entity_type_spaces,
        )
    if settings.search_backend == "opensearch":
        return OpenSearchBackend(
            base_url=settings.opensearch_url,
            index_alias=settings.opensearch_index_alias,
            entity_type_spaces=entity_type_spaces,
        )
    raise ValueError(f"unsupported search backend: {settings.search_backend}")


def build_model_gateway(settings: Settings) -> ModelGateway | None:
    if not settings.model_gateway_url:
        return None
    return OpenAICompatibleModelGateway(
        ModelGatewayConfig(
            base_url=settings.model_gateway_url,
            model=settings.model_gateway_model,
            api_key=settings.model_gateway_api_key.get_secret_value(),
            endpoint_path=settings.model_gateway_endpoint_path,
            timeout_seconds=settings.model_gateway_timeout_seconds,
            gateway_id=settings.model_gateway_id,
            allow_direct_provider=settings.model_allow_direct_provider,
        )
    )


def _resolve_pack_directories(configured_paths: str, kind: str) -> list[Path]:
    roots: list[Path] = []
    for configured_path in configured_paths.split(","):
        value = configured_path.strip()
        if not value:
            continue
        is_glob = any(token in value for token in ("*", "?", "["))
        if is_glob:
            candidates = sorted(glob.glob(value, recursive=True))
        elif Path(value).is_absolute():
            candidates = [value]
        else:
            candidates = [str(Path.cwd() / Path(value))]
        if not candidates:
            raise ValueError(f"{kind} pack path has no matches: {configured_path}")
        for candidate in candidates:
            candidate_path = Path(candidate)
            if not candidate_path.exists():
                raise ValueError(f"{kind} pack path does not exist: {candidate_path}")
            if not candidate_path.is_dir():
                raise ValueError(
                    f"{kind} pack path must be a directory: {candidate_path}"
                )
            roots.append(candidate_path)
    if not roots:
        raise ValueError(f"at least one {kind} pack path must be configured")
    return roots


def build_agent_registry(settings: Settings) -> AgentRegistry:
    registry = AgentRegistry()
    try:
        roots = _resolve_pack_directories(settings.agent_pack_paths, kind="agent")
    except Exception as error:
        raise ValueError(
            f"failed to resolve agent pack paths from {settings.agent_pack_paths}: "
            f"{error}"
        ) from error
    for root in roots:
        try:
            loaded = load_agent_pack(root)
        except Exception as error:
            raise ValueError(f"failed to load agent pack {root}: {error}") from error
        for definition in loaded.definitions:
            registry.register_definition(definition)
        for graph in loaded.graphs:
            registry.register_graph(graph)
    if not registry.graphs:
        raise ValueError("at least one agent graph must be registered")
    return registry


def build_domain_registry(settings: Settings) -> DomainRegistry:
    try:
        return load_domain_registry(
            _resolve_pack_directories(settings.domain_pack_paths, kind="domain")
        )
    except Exception as error:
        raise ValueError(
            f"failed to load domain packs from {settings.domain_pack_paths}: {error}"
        ) from error


def build_parser_registry(settings: Settings) -> ParserRegistry:
    try:
        registry = load_parser_registry(settings.parser_paths)
    except Exception as error:
        raise ValueError(
            "failed to build parser registry from "
            f"HARDATLAS_PARSER_PATHS={settings.parser_paths}: {error}"
        ) from error
    if not registry.list():
        raise ValueError("at least one extraction parser must be registered")
    return registry


def seed_repository(
    repository: KnowledgeRepository,
    domain_registry: DomainRegistry,
    *,
    include_fixture_content: bool,
) -> None:
    for space in domain_registry.spaces.values():
        repository.save_space(space)
    for node in domain_registry.taxonomy_nodes.values():
        repository.save_taxonomy_node(node)
    for schema in domain_registry.entity_types.values():
        repository.save_schema_document(
            document_id=schema.id,
            schema_version=schema.schema_version,
            kind="entity-type",
            document=schema.model_dump(mode="json", by_alias=True),
            preserve_activation=True,
        )
    for attribute in domain_registry.attributes.values():
        repository.save_schema_document(
            document_id=attribute.id,
            schema_version=attribute.schema_version,
            kind="attribute-definition",
            document=attribute.model_dump(mode="json", by_alias=True),
            preserve_activation=True,
        )
    for relationship_type in domain_registry.relationship_types.values():
        repository.save_schema_document(
            document_id=relationship_type.id,
            schema_version=relationship_type.schema_version,
            kind="relationship-type",
            document=relationship_type.model_dump(mode="json", by_alias=True),
            preserve_activation=True,
        )
    for view in domain_registry.views.values():
        repository.save_schema_document(
            document_id=view.id,
            schema_version=view.schema_version,
            kind="view-definition",
            document=view.model_dump(mode="json", by_alias=True),
            preserve_activation=True,
        )
    for profile in domain_registry.quality_profiles.values():
        repository.save_schema_document(
            document_id=profile.id,
            schema_version=profile.schema_version,
            kind="quality-profile",
            document=profile.model_dump(mode="json", by_alias=True),
            preserve_activation=True,
        )
    if include_fixture_content:
        for entity in ENTITIES:
            if repository.get_entity_by_id(entity.ref.id) is None:
                repository.save_entity(entity)
        if not repository.list_governed_proposals():
            governance = GovernanceService(PROPOSALS, "policy-1.0.0")
            for governed in governance.list_proposals():
                repository.save_governed_proposal(governed)


def create_app(
    repository: KnowledgeRepository | None = None,
    search_backend: SearchBackend | None = None,
    authenticator: ApiAuthenticator | None = None,
    model_gateway: ModelGateway | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    production = settings.environment.casefold() in {"prod", "production"}
    if production and settings.seed_fixture_content:
        raise ValueError("fixture content seeding is forbidden in production")
    if production and settings.auth_mode != "oidc":
        raise ValueError("production requires HARDATLAS_AUTH_MODE=oidc")
    if production and not settings.database_url.startswith("postgresql"):
        raise ValueError("production requires a PostgreSQL database URL")
    if production and settings.search_backend != "opensearch":
        raise ValueError("production requires HARDATLAS_SEARCH_BACKEND=opensearch")
    if production and settings.auto_create_schema is not False:
        raise ValueError(
            "production requires HARDATLAS_AUTO_CREATE_SCHEMA=false and Alembic migrations"
        )
    if production and settings.enable_hardware_fixture_extension:
        raise ValueError("production must disable the fixture-backed hardware extension")
    cors_origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    if production and any(
        origin.startswith(("http://localhost", "http://127.0.0.1")) for origin in cors_origins
    ):
        raise ValueError("production CORS origins must not include localhost HTTP")
    repository = repository or build_repository(settings)
    auto_create_schema = (
        settings.auto_create_schema if settings.auto_create_schema is not None else not production
    )
    if auto_create_schema:
        repository.create_schema()
    domain_registry = build_domain_registry(settings)
    seed_repository(
        repository,
        domain_registry,
        include_fixture_content=settings.seed_fixture_content,
    )
    search_backend = search_backend or build_search_backend(
        settings,
        repository,
        domain_registry,
    )
    agent_registry = build_agent_registry(settings)
    model_gateway = model_gateway or build_model_gateway(settings)
    parser_registry = build_parser_registry(settings)
    for definition in agent_registry.definitions.values():
        repository.save_schema_document(
            document_id=definition.id,
            schema_version=definition.version,
            kind="agent-definition",
            document=definition.model_dump(mode="json", by_alias=True),
            preserve_activation=True,
        )
    for graph in agent_registry.graphs.values():
        repository.save_schema_document(
            document_id=graph.id,
            schema_version=graph.version,
            kind="agent-graph",
            document=graph.model_dump(mode="json", by_alias=True),
            preserve_activation=True,
        )
    app = FastAPI(
        title="Atlas Universal Encyclopedia API",
        version="0.2.0",
        description="Schema-driven encyclopedia kernel with agent-maintained proposals.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "content-type",
            "authorization",
            "idempotency-key",
            "x-request-id",
            "x-hardatlas-dev-principal",
            "x-hardatlas-dev-roles",
            "x-hardatlas-dev-display-name",
            "x-hardatlas-dev-email",
            "x-hardatlas-dev-workspace-id",
        ],
        expose_headers=["x-request-id"],
    )
    persisted_proposals = repository.list_governed_proposals()
    governance = GovernanceService([], "policy-1.0.0")
    governance.proposals = {
        item.proposal.id: item.model_copy(deep=True) for item in persisted_proposals
    }
    governance.releases = {
        item.id: item.model_copy(deep=True) for item in repository.list_releases()
    }
    app.state.governance = governance
    app.state.repository = repository
    app.state.search_backend = search_backend
    answering_service = KnowledgeAnsweringService(
        repository=repository,
        search_backend=search_backend,
        model_gateway=model_gateway,
    )
    app.state.answering_service = answering_service
    app.state.authenticator = authenticator or ApiAuthenticator(settings)
    app.state.agent_registry = agent_registry
    app.state.model_gateway = model_gateway
    app.state.domain_registry = domain_registry
    graph_runner = AgentGraphRunner(
        checkpoints=RepositoryCheckpointStore(repository),
        handlers=core_handlers(model_gateway),
        registry=agent_registry,
    )
    app.state.graph_runner = graph_runner

    @app.exception_handler(ProposalConcurrencyError)
    async def proposal_concurrency_error(
        _request: Request,
        error: ProposalConcurrencyError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "proposal-version-conflict",
                    "proposalId": error.proposal_id,
                    "expectedVersion": error.expected_version,
                    "actualVersion": error.actual_version,
                }
            },
        )

    def synchronize_governance() -> None:
        governance.proposals = {
            item.proposal.id: item for item in repository.list_governed_proposals()
        }
        governance.releases = {item.id: item for item in repository.list_releases()}

    def governed_agent_input(
        input_document: dict[str, object],
    ) -> dict[str, object]:
        governed_input = dict(input_document)
        source_id = governed_input.get("sourceId")
        source_version = governed_input.get("sourceVersion")
        source = (
            repository.get_source_definition(
                str(source_id),
                version=(str(source_version) if isinstance(source_version, str) else None),
            )
            if isinstance(source_id, str)
            else None
        )
        governed_input["modelProcessingAllowed"] = (
            evaluate_source_policy(source).model_processing_allowed if source is not None else False
        )
        entity_id = governed_input.get("entityId")
        field_path = governed_input.get("fieldPath")
        citation_id = governed_input.get("citationId")
        if (
            isinstance(entity_id, str)
            and isinstance(field_path, str)
            and isinstance(citation_id, str)
        ):
            entity = repository.get_entity_by_id(entity_id)
            if entity is None:
                raise ValueError(f"agent input entity is not published: {entity_id}")
            current_value_present, current_value = read_entity_operation_value(entity, field_path)
            governed_input.update(
                {
                    "currentRevisionId": entity.revision.revision_id,
                    "currentValuePresent": current_value_present,
                    "currentValue": current_value,
                }
            )
            existing_citation = next(
                (citation for citation in entity.citations if citation.id == citation_id),
                None,
            )
            embedded_citation = governed_input.get("citation")
            if existing_citation is not None:
                governed_input["citationAlreadyPresent"] = True
            elif isinstance(embedded_citation, dict):
                citation = Citation.model_validate(embedded_citation)
                if citation.id != citation_id:
                    raise ValueError("agent input citation id does not match citation")
                governed_input["citationAlreadyPresent"] = False
            else:
                raise ValueError(f"agent input citation is not registered on entity: {citation_id}")
        return governed_input

    anonymous_principal = Principal(
        subject="anonymous",
        display_name="Anonymous",
        roles=[],
        authentication_method="system",
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    def audit(
        request: Request,
        *,
        actor: Principal,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: Literal["success", "denied", "failed"],
        metadata: dict[str, object] | None = None,
    ) -> AuditEvent:
        return repository.append_audit_event(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            request_id=request.state.request_id,
            metadata=metadata,
        )

    def require_permission(
        permission: Permission,
        *,
        action: str,
    ):
        async def dependency(request: Request) -> Principal:
            try:
                principal = app.state.authenticator.authenticate(request)
            except AuthenticationError as error:
                audit(
                    request,
                    actor=anonymous_principal,
                    action=action,
                    resource_type="http-route",
                    resource_id=request.url.path,
                    outcome="denied",
                    metadata={"reason": error.detail, "method": request.method},
                )
                raise HTTPException(
                    status_code=error.status_code,
                    detail=error.detail,
                    headers={"WWW-Authenticate": "Bearer"},
                ) from error
            if principal is None:
                audit(
                    request,
                    actor=anonymous_principal,
                    action=action,
                    resource_type="http-route",
                    resource_id=request.url.path,
                    outcome="denied",
                    metadata={"reason": "authentication required", "method": request.method},
                )
                raise HTTPException(
                    status_code=401,
                    detail="authentication required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not principal.can(permission):
                audit(
                    request,
                    actor=principal,
                    action=action,
                    resource_type="http-route",
                    resource_id=request.url.path,
                    outcome="denied",
                    metadata={
                        "reason": f"missing permission {permission.value}",
                        "method": request.method,
                    },
                )
                raise HTTPException(status_code=403, detail="insufficient permission")
            request.state.principal = principal
            return principal

        return dependency

    async def require_authenticated(request: Request) -> Principal:
        try:
            principal = app.state.authenticator.authenticate(request)
        except AuthenticationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers={"WWW-Authenticate": "Bearer"},
            ) from error
        if principal is None:
            raise HTTPException(
                status_code=401,
                detail="authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return principal

    def governed_or_404(action):
        synchronize_governance()
        try:
            return action()
        except GovernanceError as error:
            status_code = 404 if "not found" in str(error) else 409
            raise HTTPException(status_code=status_code, detail=str(error)) from error

    def authoring_schema(
        draft: EntityDraft,
    ) -> tuple[EntityType, list[AttributeDefinition], DraftValidation]:
        type_document = repository.get_schema_document(
            document_id=draft.type_id,
            kind="entity-type",
        )
        if type_document is None:
            raise HTTPException(status_code=404, detail="entity type not found")
        entity_type_model = EntityType.model_validate(type_document)
        attributes: list[AttributeDefinition] = []
        for attribute_id in entity_type_model.attribute_definition_ids:
            document = repository.get_schema_document(
                document_id=attribute_id,
                kind="attribute-definition",
            )
            if document is not None:
                attributes.append(AttributeDefinition.model_validate(document))
        taxonomy_nodes = repository.list_taxonomy_nodes()
        validation = validate_entity_draft(
            draft,
            entity_type_model,
            attributes,
            known_taxonomy_node_ids={node.id for node in taxonomy_nodes},
            taxonomy_space_by_id={node.id: node.space_id for node in taxonomy_nodes},
        )
        relationship_types = {
            item.id: item
            for item in (
                RelationshipType.model_validate(document)
                for document in repository.list_schema_documents(kind="relationship-type")
            )
        }
        entity_refs = {entity.ref.id: entity.ref for entity in repository.list_entities()}
        draft_relationship_ids = {relationship.id for relationship in draft.relationships}
        replaced_relationship_ids = {
            relationship.id
            for relationship in repository.list_relationships(
                draft.id,
                direction="outgoing",
            )
        }
        extended_issues = list(validation.issues)
        seen_outgoing: dict[str, int] = {}
        seen_incoming: dict[tuple[str, str], int] = {}
        for index, relationship in enumerate(draft.relationships):
            issue_path = f"/relationships/{index}"
            relationship_type = relationship_types.get(relationship.type_id)
            target_ref = entity_refs.get(relationship.target.id)
            if relationship_type is None:
                extended_issues.append(
                    DraftIssue(
                        severity="error",
                        code="unknown-relationship-type",
                        path=f"{issue_path}/typeId",
                        message="关系类型未注册",
                    )
                )
                continue
            if target_ref is None:
                extended_issues.append(
                    DraftIssue(
                        severity="error",
                        code="unknown-relationship-target",
                        path=f"{issue_path}/target",
                        message="关系目标条目不存在",
                    )
                )
                continue
            if relationship.target != target_ref:
                extended_issues.append(
                    DraftIssue(
                        severity="error",
                        code="relationship-target-reference-mismatch",
                        path=f"{issue_path}/target",
                        message="关系目标引用与当前条目不一致",
                    )
                )
            incoming_count = sum(
                item.id not in draft_relationship_ids | replaced_relationship_ids
                for item in repository.list_relationships(
                    target_ref.id,
                    direction="incoming",
                    relationship_type_id=relationship.type_id,
                )
            )
            incoming_key = (relationship.type_id, target_ref.id)
            relationship_validation = validate_relationship(
                relationship,
                relationship_type,
                entity_type_model,
                known_citation_ids={citation.id for citation in draft.citations},
                outgoing_count=seen_outgoing.get(relationship.type_id, 0),
                incoming_count=(incoming_count + seen_incoming.get(incoming_key, 0)),
                target_entity_type_id=target_ref.type_id,
            )
            extended_issues.extend(
                DraftIssue(
                    severity="error",
                    code=issue.code,
                    path=f"{issue_path}{issue.path}",
                    message=issue.message,
                )
                for issue in relationship_validation.issues
            )
            seen_outgoing[relationship.type_id] = seen_outgoing.get(relationship.type_id, 0) + 1
            seen_incoming[incoming_key] = seen_incoming.get(incoming_key, 0) + 1
        validation = validation.model_copy(
            update={
                "issues": extended_issues,
                "valid": not any(issue.severity == "error" for issue in extended_issues),
            }
        )
        return entity_type_model, attributes, validation

    def validate_authoring_draft_identity(
        *,
        mode: Literal["create", "revise"],
        draft: EntityDraft,
        base_revision_id: str | None,
    ) -> KnowledgeEntity | None:
        if mode == "create":
            if base_revision_id is not None:
                raise HTTPException(
                    status_code=422,
                    detail="creation draft cannot pin a base revision",
                )
            return None
        if not base_revision_id:
            raise HTTPException(
                status_code=422,
                detail="revision draft requires a base revision",
            )
        current = repository.get_entity_by_id(draft.id)
        if current is None:
            raise HTTPException(status_code=404, detail="entity not found")
        if current.revision.revision_id != base_revision_id:
            raise HTTPException(
                status_code=409,
                detail="base entity revision is stale",
            )
        if draft.type_id != current.ref.type_id or draft.slug != current.ref.slug:
            raise HTTPException(
                status_code=409,
                detail="revision draft cannot change entity type or stable slug",
            )
        return current

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="atlas-encyclopedia-api")

    @app.get(
        "/api/v1/ready",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
        tags=["system"],
    )
    async def ready() -> ReadinessResponse | JSONResponse:
        checks = {
            "database": repository.ready(),
            "search": search_backend.ready(),
            "agentRegistry": bool(agent_registry.graphs),
            "domainRegistry": bool(domain_registry.spaces),
            "parserRegistry": bool(parser_registry.list()),
        }
        response = ReadinessResponse(
            status="ready" if all(checks.values()) else "not-ready",
            checks=checks,
            data_version=settings.data_version,
        )
        if response.status == "not-ready":
            return JSONResponse(
                status_code=503,
                content=response.model_dump(mode="json", by_alias=True),
            )
        return response

    @app.get("/api/v1/build", response_model=BuildResponse, tags=["system"])
    async def build() -> BuildResponse:
        return BuildResponse(
            build_sha=settings.build_sha,
            environment=settings.environment,
            data_version=settings.data_version,
            schema_version=settings.schema_version,
            policy_version="policy-1.0.0",
        )

    @app.get("/api/v1/me", response_model=Principal, tags=["identity"])
    async def me(
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> Principal:
        return principal

    def principal_workspace_id(principal: Principal) -> str:
        if principal.workspace_id is None:
            raise HTTPException(
                status_code=403,
                detail="authenticated principal has no workspace context",
            )
        return principal.workspace_id

    @app.get(
        "/api/v1/collections",
        response_model=list[SavedCollection],
        tags=["personal"],
    )
    async def collections(
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> list[SavedCollection]:
        return repository.list_collections(principal_workspace_id(principal))

    @app.post(
        "/api/v1/collections",
        response_model=SavedCollection,
        status_code=201,
        tags=["personal"],
    )
    async def create_collection(
        body: CollectionCreateRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> SavedCollection:
        workspace_id = principal_workspace_id(principal)
        repository.ensure_workspace(
            workspace_id,
            f"{principal.display_name} workspace",
        )
        collection = SavedCollection(
            id=f"collection-{uuid4()}",
            workspace_id=workspace_id,
            name=body.name,
            description=body.description,
            created_by=principal.subject,
        )
        repository.save_collection(collection)
        audit(
            request,
            actor=principal,
            action="collection.create",
            resource_type="saved-collection",
            resource_id=collection.id,
            outcome="success",
        )
        return collection

    @app.delete(
        "/api/v1/collections/{collection_id}",
        response_model=CollectionRemoveResponse,
        tags=["personal"],
    )
    async def remove_collection(
        collection_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> CollectionRemoveResponse:
        removed = repository.remove_collection(
            principal_workspace_id(principal),
            collection_id,
        )
        if removed:
            audit(
                request,
                actor=principal,
                action="collection.remove",
                resource_type="saved-collection",
                resource_id=collection_id,
                outcome="success",
            )
        return CollectionRemoveResponse(removed=removed)

    @app.get(
        "/api/v1/collections/{collection_id}/items",
        response_model=list[SavedCollectionItem],
        tags=["personal"],
    )
    async def collection_items(
        collection_id: str,
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> list[SavedCollectionItem]:
        try:
            return repository.list_collection_items(
                principal_workspace_id(principal),
                collection_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post(
        "/api/v1/collections/{collection_id}/items",
        response_model=SavedCollectionItem,
        tags=["personal"],
    )
    async def save_collection_item(
        collection_id: str,
        body: CollectionItemSaveRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> SavedCollectionItem:
        entity = (
            repository.get_entity_revision(
                body.entity_id,
                body.entity_revision_id,
            )
            if body.entity_revision_id
            else repository.get_entity_by_id(body.entity_id)
        )
        if entity is None:
            raise HTTPException(
                status_code=404,
                detail="entity revision not found",
            )
        item = SavedCollectionItem(
            collection_id=collection_id,
            workspace_id=principal_workspace_id(principal),
            entity_id=entity.ref.id,
            entity_ref=entity.ref,
            entity_revision_id=entity.revision.revision_id,
            data_version=entity.revision.data_version,
            note=body.note,
            tags=sorted(set(body.tags)),
            saved_by=principal.subject,
        )
        try:
            repository.save_collection_item(item)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="collection.item.save",
            resource_type="knowledge-entity",
            resource_id=entity.ref.id,
            outcome="success",
            metadata={
                "collectionId": collection_id,
                "revisionId": entity.revision.revision_id,
            },
        )
        return item

    @app.delete(
        "/api/v1/collections/{collection_id}/items/{entity_id}",
        response_model=CollectionRemoveResponse,
        tags=["personal"],
    )
    async def remove_collection_item(
        collection_id: str,
        entity_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> CollectionRemoveResponse:
        removed = repository.remove_collection_item(
            principal_workspace_id(principal),
            collection_id,
            entity_id,
        )
        if removed:
            audit(
                request,
                actor=principal,
                action="collection.item.remove",
                resource_type="knowledge-entity",
                resource_id=entity_id,
                outcome="success",
                metadata={"collectionId": collection_id},
            )
        return CollectionRemoveResponse(removed=removed)

    @app.get(
        "/api/v1/audit-events",
        response_model=list[AuditEvent],
        tags=["identity"],
    )
    async def audit_events(
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AUDIT_READ,
                    action="audit.read",
                )
            ),
        ],
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[AuditEvent]:
        del principal
        return repository.list_audit_events(limit=limit)

    @app.get(
        "/api/v1/audit-events/status",
        response_model=AuditStatusResponse,
        tags=["identity"],
    )
    async def audit_status(
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AUDIT_READ,
                    action="audit.read",
                )
            ),
        ],
    ) -> AuditStatusResponse:
        del principal
        latest = repository.list_audit_events(limit=1)
        return AuditStatusResponse(
            valid=repository.verify_audit_chain(),
            event_count=repository.audit_event_count(),
            latest_hash=latest[0].event_hash if latest else None,
        )

    @app.get("/api/v1/spaces", response_model=list[KnowledgeSpace], tags=["knowledge"])
    async def spaces() -> list[KnowledgeSpace]:
        return repository.list_spaces()

    @app.get(
        "/api/v1/source-parsers",
        response_model=list[ExtractionParserDefinition],
        tags=["sources"],
    )
    async def source_parsers(
        _: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_READ,
                    action="source.parser.read",
                )
            ),
        ],
    ) -> list[ExtractionParserDefinition]:
        return parser_registry.list()

    @app.get(
        "/api/v1/sources",
        response_model=list[SourceRegistryRecord],
        tags=["sources"],
    )
    async def sources(
        _: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_READ,
                    action="source.read",
                )
            ),
        ],
    ) -> list[SourceRegistryRecord]:
        return [
            SourceRegistryRecord(
                source=source,
                policy=evaluate_source_policy(source),
            )
            for source in repository.list_source_definitions()
        ]

    @app.post(
        "/api/v1/sources",
        response_model=SourceRegistryRecord,
        tags=["sources"],
    )
    async def register_source(
        source: SourceDefinition,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_REGISTER,
                    action="source.register",
                )
            ),
        ],
    ) -> SourceRegistryRecord:
        if source.parser_id and source.parser_version:
            try:
                parser_registry.resolve_for_source(source)
            except ExtractionError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            repository.save_source_definition(source)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        result = SourceRegistryRecord(
            source=source,
            policy=evaluate_source_policy(source),
        )
        audit(
            request,
            actor=principal,
            action="source.register",
            resource_type="source",
            resource_id=f"{source.id}@{source.version}",
            outcome="success",
            metadata={"policyAllowed": result.policy.allowed},
        )
        return result

    @app.get(
        "/api/v1/sources/{source_id}",
        response_model=SourceRegistryRecord,
        tags=["sources"],
    )
    async def source_detail(
        source_id: str,
        _: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_READ,
                    action="source.read",
                )
            ),
        ],
    ) -> SourceRegistryRecord:
        source = repository.get_source_definition(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        return SourceRegistryRecord(
            source=source,
            policy=evaluate_source_policy(source),
        )

    @app.get(
        "/api/v1/sources/{source_id}/snapshots",
        response_model=list[SourceSnapshot],
        tags=["sources"],
    )
    async def source_snapshots(
        source_id: str,
        _: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_READ,
                    action="source.snapshot.read",
                )
            ),
        ],
    ) -> list[SourceSnapshot]:
        if repository.get_source_definition(source_id) is None:
            raise HTTPException(status_code=404, detail="source not found")
        return repository.list_source_snapshots(source_id=source_id)

    @app.get(
        "/api/v1/sources/{source_id}/acquisitions",
        response_model=list[SourceAcquisitionJob],
        tags=["sources"],
    )
    async def source_acquisitions(
        source_id: str,
        _: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_READ,
                    action="source.acquisition.read",
                )
            ),
        ],
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[SourceAcquisitionJob]:
        if repository.get_source_definition(source_id) is None:
            raise HTTPException(status_code=404, detail="source not found")
        return repository.list_source_acquisition_jobs(
            source_id=source_id,
            limit=limit,
        )

    @app.post(
        "/api/v1/sources/{source_id}/acquisitions",
        response_model=SourceAcquisitionJob,
        tags=["sources"],
    )
    async def request_source_acquisition(
        source_id: str,
        payload: SourceAcquisitionRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_REGISTER,
                    action="source.acquisition.request",
                )
            ),
        ],
    ) -> SourceAcquisitionJob:
        source = repository.get_source_definition(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        if payload.url is not None:
            try:
                validate_acquisition_url(source, payload.url)
            except AcquisitionError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        decision = evaluate_source_policy(source)
        if not decision.allowed:
            raise HTTPException(
                status_code=409,
                detail="; ".join(decision.blockers),
            )
        job = SourceAcquisitionJob(
            id=payload.id,
            source_id=source.id,
            source_version=source.version,
            url=payload.url,
            requested_by=principal.subject,
            idempotency_key=payload.idempotency_key,
        )
        try:
            repository.create_source_acquisition_job(job)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="source.acquisition.request",
            resource_type="source-acquisition",
            resource_id=job.id,
            outcome="success",
            metadata={
                "sourceId": source.id,
                "sourceVersion": source.version,
            },
        )
        return job

    @app.get(
        "/api/v1/sources/{source_id}/extractions",
        response_model=list[ExtractionBatch],
        tags=["sources"],
    )
    async def source_extractions(
        source_id: str,
        _: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_READ,
                    action="source.extraction.read",
                )
            ),
        ],
    ) -> list[ExtractionBatch]:
        if repository.get_source_definition(source_id) is None:
            raise HTTPException(status_code=404, detail="source not found")
        return repository.list_extraction_batches(source_id=source_id)

    @app.get(
        "/api/v1/extractions/{batch_id}/candidates",
        response_model=list[ExtractionCandidate],
        tags=["sources"],
    )
    async def extraction_candidates(
        batch_id: str,
        _: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_READ,
                    action="source.extraction.read",
                )
            ),
        ],
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[ExtractionCandidate]:
        if repository.get_extraction_batch(batch_id) is None:
            raise HTTPException(status_code=404, detail="extraction batch not found")
        return repository.list_extraction_candidates(
            batch_id=batch_id,
            limit=limit,
        )

    @app.get(
        "/api/v1/sources/{source_id}/pipeline",
        response_model=SourcePipelineResponse,
        tags=["sources"],
    )
    async def source_pipeline(
        source_id: str,
        _: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_READ,
                    action="source.pipeline.read",
                )
            ),
        ],
    ) -> SourcePipelineResponse:
        source = repository.get_source_definition(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        acquisitions = repository.list_source_acquisition_jobs(
            source_id=source_id,
            limit=1000,
        )
        snapshots = repository.list_source_snapshots(source_id=source_id)
        batches = repository.list_extraction_batches(source_id=source_id)
        batches_by_snapshot: dict[str, list[ExtractionBatch]] = {}
        candidates_by_batch: dict[str, list[ExtractionCandidate]] = {}
        schedule_ids: set[str] = set()
        for batch in batches:
            batches_by_snapshot.setdefault(batch.snapshot_id, []).append(batch)
            candidates = repository.list_extraction_candidates(
                batch_id=batch.id,
                limit=1000,
            )
            candidates_by_batch[batch.id] = candidates
            schedule_ids.update(
                schedule_id for candidate in candidates for schedule_id in candidate.schedule_ids
            )
        schedules = [
            schedule
            for schedule_id in sorted(schedule_ids)
            if (schedule := repository.get_agent_graph_schedule(schedule_id)) is not None
        ]
        items: list[SourcePipelineItem] = [
            SourcePipelineItem(
                stage="acquisition",
                id=job.id,
                status=job.status,
                replayable=job.status == "failed",
                error=job.error,
                updated_at=job.updated_at,
            )
            for job in acquisitions
        ]
        for snapshot in snapshots:
            snapshot_batches = batches_by_snapshot.get(snapshot.id, [])
            items.append(
                SourcePipelineItem(
                    stage="snapshot",
                    id=snapshot.id,
                    parent_id=next(
                        (job.id for job in acquisitions if job.snapshot_id == snapshot.id),
                        None,
                    ),
                    status=(
                        "extracted"
                        if snapshot_batches
                        else (
                            "pending-extraction"
                            if source.parser_id and source.parser_version
                            else "awaiting-parser"
                        )
                    ),
                    replayable=bool(source.parser_id and source.parser_version),
                    updated_at=snapshot.retrieved_at,
                )
            )
        for batch in batches:
            candidates = candidates_by_batch[batch.id]
            candidate_statuses = {candidate.status for candidate in candidates}
            items.append(
                SourcePipelineItem(
                    stage="extraction",
                    id=batch.id,
                    parent_id=batch.snapshot_id,
                    status=(
                        "scheduled"
                        if candidates and candidate_statuses.issubset({"scheduled", "rejected"})
                        else (
                            "needs-resolution" if "parsed" in candidate_statuses else batch.status
                        )
                    ),
                    replayable=True,
                    error=batch.error,
                    updated_at=batch.created_at,
                )
            )
            items.extend(
                SourcePipelineItem(
                    stage="candidate",
                    id=candidate.id,
                    parent_id=batch.id,
                    status=candidate.status,
                    replayable=False,
                    updated_at=candidate.created_at,
                )
                for candidate in candidates
            )
        items.extend(
            SourcePipelineItem(
                stage="agent-schedule",
                id=schedule.id,
                parent_id=str(schedule.input.get("candidateId") or ""),
                status=schedule.status,
                replayable=schedule.status == "failed",
                error=schedule.error,
                graph_id=schedule.graph_id,
                updated_at=schedule.updated_at,
            )
            for schedule in schedules
        )
        resource_ids = {item.id for item in items}
        pending_events = [
            event
            for event in repository.pending_outbox_records(limit=10_000)
            if event.aggregate_id in resource_ids or event.payload.get("sourceId") == source_id
        ]
        stalled_statuses = {
            ("acquisition", "failed"),
            ("extraction", "needs-resolution"),
            ("candidate", "parsed"),
            ("candidate", "resolved"),
            ("agent-schedule", "failed"),
        }
        return SourcePipelineResponse(
            source_id=source_id,
            items=sorted(
                items,
                key=lambda item: (item.updated_at, item.stage, item.id),
            ),
            pending_event_count=len(pending_events),
            stalled_count=sum((item.stage, item.status) in stalled_statuses for item in items),
        )

    @app.post(
        "/api/v1/source-acquisitions/{job_id}/replay",
        response_model=PipelineReplayResponse,
        tags=["sources"],
    )
    async def replay_source_acquisition(
        job_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_REGISTER,
                    action="source.acquisition.replay",
                )
            ),
        ],
    ) -> PipelineReplayResponse:
        job = repository.get_source_acquisition_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="source acquisition not found")
        if job.status != "failed":
            raise HTTPException(
                status_code=409,
                detail="only failed acquisitions can be replayed",
            )
        event_id = repository.requeue_outbox_event(
            topic="source.acquisition.requested",
            aggregate_id=job.id,
            payload={
                "jobId": job.id,
                "sourceId": job.source_id,
                "sourceVersion": job.source_version,
                "url": job.url,
                "maintenanceWorkItemId": job.maintenance_work_item_id,
            },
        )
        audit(
            request,
            actor=principal,
            action="source.acquisition.replay",
            resource_type="source-acquisition",
            resource_id=job.id,
            outcome="success",
        )
        return PipelineReplayResponse(
            accepted=True,
            stage="acquisition",
            resource_id=job.id,
            outbox_event_id=event_id,
        )

    @app.post(
        "/api/v1/source-snapshots/{snapshot_id}/replay-extraction",
        response_model=PipelineReplayResponse,
        tags=["sources"],
    )
    async def replay_snapshot_extraction(
        snapshot_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_REGISTER,
                    action="source.extraction.replay",
                )
            ),
        ],
    ) -> PipelineReplayResponse:
        snapshot = repository.get_source_snapshot(snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="source snapshot not found")
        source = repository.get_source_definition(
            snapshot.source_id,
            version=snapshot.source_version,
        )
        if source is None or not (source.parser_id and source.parser_version):
            raise HTTPException(
                status_code=409,
                detail="snapshot source has no pinned parser",
            )
        event_id = repository.requeue_outbox_event(
            topic="source.snapshot.captured",
            aggregate_id=snapshot.id,
            payload={
                "snapshotId": snapshot.id,
                "sourceId": snapshot.source_id,
                "sourceVersion": snapshot.source_version,
            },
        )
        audit(
            request,
            actor=principal,
            action="source.extraction.replay",
            resource_type="source-snapshot",
            resource_id=snapshot.id,
            outcome="success",
        )
        return PipelineReplayResponse(
            accepted=True,
            stage="extraction",
            resource_id=snapshot.id,
            outbox_event_id=event_id,
        )

    @app.post(
        "/api/v1/extractions/{batch_id}/replay-finalization",
        response_model=PipelineReplayResponse,
        tags=["sources"],
    )
    async def replay_extraction_finalization(
        batch_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.SOURCE_REGISTER,
                    action="source.finalization.replay",
                )
            ),
        ],
    ) -> PipelineReplayResponse:
        batch = repository.get_extraction_batch(batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="extraction batch not found")
        event_id = repository.requeue_outbox_event(
            topic="source.extraction.completed",
            aggregate_id=batch.id,
            payload={
                "batchId": batch.id,
                "snapshotId": batch.snapshot_id,
                "sourceId": batch.source_id,
                "parserId": batch.parser_id,
                "parserVersion": batch.parser_version,
            },
        )
        audit(
            request,
            actor=principal,
            action="source.finalization.replay",
            resource_type="extraction-batch",
            resource_id=batch.id,
            outcome="success",
        )
        return PipelineReplayResponse(
            accepted=True,
            stage="finalization",
            resource_id=batch.id,
            outbox_event_id=event_id,
        )

    @app.get("/api/v1/taxonomy", response_model=list[TaxonomyNode], tags=["knowledge"])
    async def taxonomy(
        space_id: Annotated[str | None, Query(alias="spaceId")] = None,
    ) -> list[TaxonomyNode]:
        return repository.list_taxonomy_nodes(space_id)

    @app.get("/api/v1/search", response_model=SearchResponse, tags=["discovery"])
    async def search(
        q: Annotated[str, Query(max_length=160)] = "",
        type_id: Annotated[str | None, Query(alias="typeId")] = None,
        space_id: Annotated[str | None, Query(alias="spaceId")] = None,
        taxonomy_node_id: Annotated[
            str | None,
            Query(alias="taxonomyNodeId"),
        ] = None,
        locale: Annotated[str | None, Query(max_length=40)] = None,
        display_locale: Annotated[
            str,
            Query(alias="displayLocale", max_length=40),
        ] = "zh-CN",
        source_tier: Annotated[
            Literal["primary", "authoritative", "secondary", "community"] | None,
            Query(alias="sourceTier"),
        ] = None,
        minimum_sources: Annotated[
            int,
            Query(alias="minimumSources", ge=0, le=100),
        ] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> SearchResponse:
        page = search_backend.search_page(
            q,
            type_id=type_id,
            space_id=space_id,
            taxonomy_node_id=taxonomy_node_id,
            locale=locale,
            source_tier=source_tier,
            minimum_sources=minimum_sources,
            limit=limit,
            offset=offset,
        )
        items: list[SearchItem] = []
        for hit in page.hits:
            item = repository.get_entity(hit.slug)
            if item is None:
                continue
            items.append(
                SearchItem(
                    entity=item,
                    matched_text=hit.matched_text,
                    matched_field=hit.matched_field,
                    score=hit.score,
                    fixture=False,
                )
            )

        def localized_label(values: list[object], fallback: str) -> str:
            language = display_locale.split("-", 1)[0].casefold()
            for value in values:
                locale_value = getattr(value, "locale", "")
                if locale_value == display_locale:
                    return str(getattr(value, "value", fallback))
            for value in values:
                locale_value = str(getattr(value, "locale", ""))
                if locale_value.split("-", 1)[0].casefold() == language:
                    return str(getattr(value, "value", fallback))
            for value in values:
                if getattr(value, "locale", "") == "zh-CN":
                    return str(getattr(value, "value", fallback))
            if values:
                return str(getattr(values[0], "value", fallback))
            return fallback

        space_labels = {
            space.id: localized_label(space.name, space.slug)
            for space in domain_registry.spaces.values()
        }
        type_labels = {
            entity_type.id: localized_label(entity_type.name, entity_type.key)
            for entity_type in domain_registry.entity_types.values()
        }
        taxonomy_labels = {
            node.id: localized_label(node.name, node.slug)
            for node in domain_registry.taxonomy_nodes.values()
        }
        source_tier_labels = {
            "primary": "第一手来源",
            "authoritative": "权威来源",
            "secondary": "二级来源",
            "community": "社区来源",
        }

        def facet_values(
            facet_name: str,
            labels: dict[str, str] | None = None,
        ) -> list[SearchFacetValue]:
            counts = page.facets.get(facet_name, {})
            return [
                SearchFacetValue(
                    value=value,
                    label=(labels or {}).get(value, value),
                    count=count,
                )
                for value, count in sorted(
                    counts.items(),
                    key=lambda item: (
                        -item[1],
                        (labels or {}).get(item[0], item[0]),
                    ),
                )
            ]

        return SearchResponse(
            query=q,
            items=items,
            total=page.total,
            offset=offset,
            limit=limit,
            facets=SearchFacets(
                spaces=facet_values("spaceId", space_labels),
                entity_types=facet_values("typeId", type_labels),
                taxonomy_nodes=facet_values("taxonomyNodeId", taxonomy_labels),
                locales=facet_values("locale"),
                source_tiers=facet_values("sourceTier", source_tier_labels),
            ),
        )

    @app.get(
        "/api/v1/search/status",
        response_model=SearchStatusResponse,
        tags=["discovery"],
    )
    async def search_status() -> SearchStatusResponse:
        return SearchStatusResponse(
            backend=search_backend.name,
            ready=search_backend.ready(),
            index_alias=(
                search_backend.index_alias
                if isinstance(search_backend, OpenSearchBackend)
                else None
            ),
        )

    @app.post(
        "/api/v1/answers",
        response_model=KnowledgeAnswer,
        status_code=201,
        tags=["discovery"],
    )
    async def answer_question(
        payload: KnowledgeAnswerRequest,
    ) -> KnowledgeAnswer:
        return answering_service.answer(
            question=payload.question,
            locale=payload.locale,
            allow_model=(payload.mode == "auto" and settings.public_answer_model_enabled),
        )

    @app.get(
        "/api/v1/answers/{answer_id}",
        response_model=KnowledgeAnswer,
        tags=["discovery"],
    )
    async def answer_detail(answer_id: str) -> KnowledgeAnswer:
        answer = repository.get_knowledge_answer(answer_id)
        if answer is None:
            raise HTTPException(status_code=404, detail="knowledge answer not found")
        return answer

    @app.get(
        "/api/v1/entities/{slug}",
        response_model=KnowledgeEntity,
        tags=["knowledge"],
    )
    async def entity_detail(
        slug: str,
        request: Request,
        response: Response,
    ) -> KnowledgeEntity | Response:
        item = repository.get_entity(slug)
        if item is None:
            raise HTTPException(status_code=404, detail="entity not found")
        etag = '"' + hashlib.sha256(item.revision.revision_id.encode()).hexdigest() + '"'
        headers = {
            "etag": etag,
            "cache-control": "public, max-age=60, stale-while-revalidate=300",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        for name, value in headers.items():
            response.headers[name] = value
        return item

    @app.get(
        "/api/v1/entities/{slug}/revisions",
        response_model=list[EntityRevisionSummary],
        tags=["knowledge"],
    )
    async def entity_revisions(
        slug: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[EntityRevisionSummary]:
        current = repository.get_entity(slug)
        if current is None:
            raise HTTPException(status_code=404, detail="entity not found")
        return [
            EntityRevisionSummary(
                revision=item.revision,
                current=(item.revision.revision_id == current.revision.revision_id),
            )
            for item in repository.list_entity_revisions(
                slug,
                limit=limit,
            )
        ]

    @app.get(
        "/api/v1/entities/{slug}/revisions/{revision_id}",
        response_model=KnowledgeEntity,
        tags=["knowledge"],
    )
    async def entity_revision(
        slug: str,
        revision_id: str,
        request: Request,
        response: Response,
    ) -> KnowledgeEntity | Response:
        item = repository.get_entity_revision(slug, revision_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail="entity revision not found",
            )
        etag = '"' + hashlib.sha256(item.revision.revision_id.encode()).hexdigest() + '"'
        headers = {
            "etag": etag,
            "cache-control": "public, max-age=31536000, immutable",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        for name, value in headers.items():
            response.headers[name] = value
        return item

    @app.get(
        "/api/v1/entities",
        response_model=list[KnowledgeEntity],
        tags=["knowledge"],
    )
    async def entities(
        type_id: Annotated[str | None, Query(alias="typeId")] = None,
    ) -> list[KnowledgeEntity]:
        return repository.list_entities(type_id)

    @app.get(
        "/api/v1/relationship-types",
        response_model=list[RelationshipType],
        tags=["schema"],
    )
    async def relationship_types() -> list[RelationshipType]:
        return [
            RelationshipType.model_validate(document)
            for document in repository.list_schema_documents(kind="relationship-type")
        ]

    @app.get(
        "/api/v1/entities/{slug}/relationships",
        response_model=list[RelationshipTraversalItem],
        tags=["knowledge"],
    )
    async def entity_relationships(
        slug: str,
        direction: Literal["outgoing", "incoming", "both"] = "both",
        type_id: Annotated[str | None, Query(alias="typeId")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[RelationshipTraversalItem]:
        entity = repository.get_entity(slug)
        if entity is None:
            raise HTTPException(status_code=404, detail="entity not found")
        relationships = repository.list_relationships(
            entity.ref.id,
            direction=direction,
            relationship_type_id=type_id,
            limit=limit,
        )
        types = {
            item.id: item
            for item in [
                RelationshipType.model_validate(document)
                for document in repository.list_schema_documents(kind="relationship-type")
            ]
        }
        result: list[RelationshipTraversalItem] = []
        for relationship in relationships:
            relationship_type = types.get(relationship.type_id)
            if relationship_type is None:
                raise HTTPException(
                    status_code=500,
                    detail=f"relationship type is not registered: {relationship.type_id}",
                )
            outgoing = relationship.source.id == entity.ref.id
            result.append(
                RelationshipTraversalItem(
                    relationship=relationship,
                    relationship_type=relationship_type,
                    direction="outgoing" if outgoing else "incoming",
                    neighbor=(relationship.target if outgoing else relationship.source),
                )
            )
        return result

    @app.get(
        "/api/v1/schema-registry",
        response_model=SchemaRegistryResponse,
        tags=["schema"],
    )
    async def schema_registry() -> SchemaRegistryResponse:
        return SchemaRegistryResponse(
            entity_types=[
                EntityType.model_validate(document)
                for document in repository.list_schema_documents(kind="entity-type")
            ],
            attribute_definitions=[
                AttributeDefinition.model_validate(document)
                for document in repository.list_schema_documents(kind="attribute-definition")
            ],
            relationship_types=[
                RelationshipType.model_validate(document)
                for document in repository.list_schema_documents(kind="relationship-type")
            ],
            view_definitions=[
                ViewDefinition.model_validate(document)
                for document in repository.list_schema_documents(kind="view-definition")
            ],
            quality_profiles=[
                QualityProfile.model_validate(document)
                for document in repository.list_schema_documents(kind="quality-profile")
            ],
        )

    def build_domain_pack(
        body: DomainPackDraftRequest,
        *,
        created_by: str,
        created_at: datetime | None = None,
    ) -> DomainPackRelease:
        return DomainPackRelease(
            **body.model_dump(),
            proposal_id=f"proposal-domain-pack-{body.id}-{body.version}",
            created_by=created_by,
            created_at=created_at or datetime.now(UTC),
        )

    def analyze_domain_pack(pack: DomainPackRelease) -> DomainPackValidation:
        return validate_domain_pack(
            pack,
            existing_spaces=repository.list_spaces(),
            existing_taxonomy_nodes=repository.list_taxonomy_nodes(),
            existing_entity_types=[
                EntityType.model_validate(document)
                for document in repository.list_schema_documents(kind="entity-type")
            ],
            existing_attributes=[
                AttributeDefinition.model_validate(document)
                for document in repository.list_schema_documents(kind="attribute-definition")
            ],
            existing_relationship_types=[
                RelationshipType.model_validate(document)
                for document in repository.list_schema_documents(kind="relationship-type")
            ],
            existing_views=[
                ViewDefinition.model_validate(document)
                for document in repository.list_schema_documents(kind="view-definition")
            ],
            existing_quality_profiles=[
                QualityProfile.model_validate(document)
                for document in repository.list_schema_documents(kind="quality-profile")
            ],
        )

    def previous_domain_pack(
        pack: DomainPackRelease,
        *,
        statuses: tuple[str, ...] = ("archived",),
    ) -> DomainPackRelease | None:
        candidates = [
            item
            for item in repository.list_domain_packs()
            if item.id == pack.id
            and item.version != pack.version
            and item.status in statuses
            and item.created_at <= pack.created_at
        ]
        return max(candidates, key=lambda item: item.created_at, default=None)

    def domain_pack_rollback_snapshot(
        pack: DomainPackRelease,
    ) -> DomainPackRollbackSnapshot:
        spaces = {item.id: item for item in repository.list_spaces()}
        nodes = {item.id: item for item in repository.list_taxonomy_nodes()}
        references = [
            (kind, document.id)
            for kind, documents in (
                ("attribute-definition", pack.attributes),
                ("relationship-type", pack.relationship_types),
                ("view-definition", pack.views),
                ("entity-type", pack.entity_types),
                ("quality-profile", pack.quality_profiles),
            )
            for document in documents
        ]
        return DomainPackRollbackSnapshot(
            schema_activations=repository.schema_activation_versions(references),
            spaces={space.id: spaces.get(space.id) for space in pack.spaces},
            taxonomy_nodes={node.id: nodes.get(node.id) for node in pack.taxonomy_nodes},
        )

    def analyze_domain_pack_rollback(
        pack: DomainPackRelease,
    ) -> DomainPackRollbackAnalysis:
        previous = previous_domain_pack(pack)
        blockers: list[DomainPackRollbackBlocker] = []
        blocker_keys: set[tuple[str, str, tuple[str, ...]]] = set()

        def block(
            code: str,
            resource_id: str,
            dependent_ids: list[str],
            message: str,
        ) -> None:
            normalized_dependents = sorted(set(dependent_ids))
            key = (code, resource_id, tuple(normalized_dependents))
            if key in blocker_keys:
                return
            blocker_keys.add(key)
            blockers.append(
                DomainPackRollbackBlocker(
                    code=code,
                    resource_id=resource_id,
                    dependent_ids=normalized_dependents,
                    message=message,
                )
            )

        if pack.status != "published":
            block(
                "domain-pack-not-published",
                f"{pack.id}@{pack.version}",
                [],
                "只有当前已发布的 Domain Pack 可以回滚",
            )
        snapshot = pack.rollback_snapshot
        if snapshot is None:
            block(
                "rollback-snapshot-missing",
                f"{pack.id}@{pack.version}",
                [],
                "该版本发布时未记录回滚快照，拒绝推测历史状态",
            )
            return DomainPackRollbackAnalysis(
                pack_id=pack.id,
                version=pack.version,
                restore_version=previous.version if previous else None,
                safe=False,
                blockers=blockers,
                manifest_diff=compare_domain_packs(previous, pack),
            )

        schema_groups = (
            ("attribute-definition", pack.attributes),
            ("relationship-type", pack.relationship_types),
            ("view-definition", pack.views),
            ("entity-type", pack.entity_types),
        )
        references = [
            (kind, document.id) for kind, documents in schema_groups for document in documents
        ]
        active_versions = repository.schema_activation_versions(references)
        for kind, documents in schema_groups:
            for document in documents:
                key = f"{kind}|{document.id}"
                if key not in snapshot.schema_activations:
                    block(
                        "rollback-snapshot-incomplete",
                        document.id,
                        [],
                        "回滚快照缺少 Schema activation",
                    )
                if active_versions.get(key) != document.schema_version:
                    block(
                        "schema-activation-changed",
                        document.id,
                        [active_versions.get(key) or "inactive"],
                        "该 Schema 在 Domain Pack 发布后又发生了激活变更",
                    )

        removing_attributes = {
            item.id
            for item in pack.attributes
            if snapshot.schema_activations.get(f"attribute-definition|{item.id}") is None
        }
        removing_relationships = {
            item.id
            for item in pack.relationship_types
            if snapshot.schema_activations.get(f"relationship-type|{item.id}") is None
        }
        removing_views = {
            item.id
            for item in pack.views
            if snapshot.schema_activations.get(f"view-definition|{item.id}") is None
        }
        removing_types = {
            item.id
            for item in pack.entity_types
            if snapshot.schema_activations.get(f"entity-type|{item.id}") is None
        }
        removing_nodes = {
            item.id for item in pack.taxonomy_nodes if snapshot.taxonomy_nodes.get(item.id) is None
        }
        removing_spaces = {item.id for item in pack.spaces if snapshot.spaces.get(item.id) is None}

        entities = repository.list_entities()
        for entity_type_id in sorted(removing_types):
            dependents = [
                entity.ref.id for entity in entities if entity.ref.type_id == entity_type_id
            ]
            if dependents:
                block(
                    "entity-type-in-use",
                    entity_type_id,
                    dependents,
                    "仍有公开实体使用该实体类型",
                )
        for attribute_id in sorted(removing_attributes):
            dependents = [
                entity.ref.id
                for entity in entities
                if any(claim.attribute_definition_id == attribute_id for claim in entity.claims)
            ]
            if dependents:
                block(
                    "attribute-in-use",
                    attribute_id,
                    dependents,
                    "仍有公开实体声明使用该属性",
                )
        for relationship_id in sorted(removing_relationships):
            dependents = [
                relationship.id
                for entity in entities
                for relationship in entity.relationships
                if relationship.type_id == relationship_id
            ]
            if dependents:
                block(
                    "relationship-type-in-use",
                    relationship_id,
                    dependents,
                    "仍有公开关系边使用该关系类型",
                )
        for node_id in sorted(removing_nodes):
            dependents = [
                entity.ref.id for entity in entities if node_id in entity.taxonomy_node_ids
            ]
            if dependents:
                block(
                    "taxonomy-node-in-use",
                    node_id,
                    dependents,
                    "仍有公开实体位于该分类节点",
                )

        active_types = [
            EntityType.model_validate(document)
            for document in repository.list_schema_documents(kind="entity-type")
        ]
        for entity_type in active_types:
            if entity_type.id in removing_types:
                continue
            attribute_dependencies = sorted(
                set(entity_type.attribute_definition_ids) & removing_attributes
            )
            relationship_dependencies = sorted(
                set(entity_type.allowed_relationship_type_ids) & removing_relationships
            )
            view_dependencies = (
                [entity_type.default_view_definition_id]
                if entity_type.default_view_definition_id in removing_views
                else []
            )
            for code, resources, message in (
                (
                    "entity-type-references-attribute",
                    attribute_dependencies,
                    "其它活动实体类型仍引用待移除属性",
                ),
                (
                    "entity-type-references-relationship",
                    relationship_dependencies,
                    "其它活动实体类型仍引用待移除关系",
                ),
                (
                    "entity-type-references-view",
                    view_dependencies,
                    "其它活动实体类型仍引用待移除视图",
                ),
            ):
                for resource_id in resources:
                    block(
                        code,
                        resource_id,
                        [entity_type.id],
                        message,
                    )

        active_relationships = [
            RelationshipType.model_validate(document)
            for document in repository.list_schema_documents(kind="relationship-type")
        ]
        for relationship in active_relationships:
            if relationship.id in removing_relationships:
                continue
            for type_id in sorted(
                set(relationship.source_entity_type_ids + relationship.target_entity_type_ids)
                & removing_types
            ):
                block(
                    "relationship-references-entity-type",
                    type_id,
                    [relationship.id],
                    "其它活动关系类型仍引用待移除实体类型",
                )

        active_nodes = repository.list_taxonomy_nodes()
        for node in active_nodes:
            if node.id in removing_nodes:
                continue
            for parent_id in sorted(set(node.parent_ids) & removing_nodes):
                block(
                    "taxonomy-child-dependency",
                    parent_id,
                    [node.id],
                    "其它活动分类节点仍引用待移除父节点",
                )
            if node.space_id in removing_spaces:
                block(
                    "taxonomy-space-dependency",
                    node.space_id,
                    [node.id],
                    "其它活动分类节点仍位于待移除知识空间",
                )

        active_spaces = repository.list_spaces()
        processed_space_ids = {item.id for item in pack.spaces}
        for space in active_spaces:
            if space.id in processed_space_ids:
                continue
            for root_id in sorted(set(space.root_taxonomy_node_ids) & removing_nodes):
                block(
                    "space-root-dependency",
                    root_id,
                    [space.id],
                    "其它活动知识空间仍引用待移除根分类",
                )

        return DomainPackRollbackAnalysis(
            pack_id=pack.id,
            version=pack.version,
            restore_version=previous.version if previous else None,
            safe=not blockers,
            blockers=blockers,
            manifest_diff=compare_domain_packs(previous, pack),
        )

    @app.get(
        "/api/v1/domain-packs",
        response_model=list[DomainPackRelease],
        tags=["schema"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="domain-pack.read",
                )
            )
        ],
    )
    async def domain_packs() -> list[DomainPackRelease]:
        return repository.list_domain_packs()

    @app.get(
        "/api/v1/domain-packs/{pack_id}/{version}",
        response_model=DomainPackRelease,
        tags=["schema"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="domain-pack.read",
                )
            )
        ],
    )
    async def domain_pack_detail(
        pack_id: str,
        version: str,
    ) -> DomainPackRelease:
        pack = repository.get_domain_pack(pack_id, version)
        if pack is None:
            raise HTTPException(status_code=404, detail="domain pack not found")
        return pack

    @app.get(
        "/api/v1/domain-packs/{pack_id}/{version}/diff",
        response_model=DomainPackDiff,
        tags=["schema"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="domain-pack.diff",
                )
            )
        ],
    )
    async def domain_pack_diff(
        pack_id: str,
        version: str,
        against_version: Annotated[
            str | None,
            Query(alias="againstVersion", max_length=80),
        ] = None,
    ) -> DomainPackDiff:
        pack = repository.get_domain_pack(pack_id, version)
        if pack is None:
            raise HTTPException(status_code=404, detail="domain pack not found")
        if against_version:
            before = repository.get_domain_pack(pack_id, against_version)
            if before is None:
                raise HTTPException(
                    status_code=404,
                    detail="comparison domain pack version not found",
                )
        else:
            before = previous_domain_pack(
                pack,
                statuses=("published", "archived", "rolled-back"),
            )
        return compare_domain_packs(before, pack)

    @app.post(
        "/api/v1/domain-packs/validate",
        response_model=DomainPackValidation,
        tags=["schema"],
    )
    async def validate_domain_pack_endpoint(
        body: DomainPackDraftRequest,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="domain-pack.validate",
                )
            ),
        ],
    ) -> DomainPackValidation:
        return analyze_domain_pack(build_domain_pack(body, created_by=principal.subject))

    @app.post(
        "/api/v1/domain-packs",
        response_model=DomainPackRelease,
        status_code=201,
        tags=["schema"],
    )
    async def save_domain_pack_draft(
        body: DomainPackDraftRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="domain-pack.create",
                )
            ),
        ],
    ) -> DomainPackRelease:
        existing = repository.get_domain_pack(body.id, body.version)
        pack = build_domain_pack(
            body,
            created_by=existing.created_by if existing else principal.subject,
            created_at=existing.created_at if existing else None,
        )
        validation = analyze_domain_pack(pack)
        if not validation.valid:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "domain pack validation failed",
                    "issues": [
                        item.model_dump(mode="json", by_alias=True) for item in validation.issues
                    ],
                },
            )
        governed: GovernedProposal | None = None
        if existing is None:
            proposal = AgentProposal(
                id=pack.proposal_id,
                entity_id=None,
                proposal_type="schema",
                operations=[
                    ChangeOperation(
                        operation="add",
                        path=f"/domain-packs/{pack.id}/{pack.version}",
                        before=None,
                        after=pack.model_dump(mode="json", by_alias=True),
                        citation_ids=pack.citation_ids,
                        confidence=1,
                    )
                ],
                risk="high",
                status="proposed",
                agent_run_id=f"domain-pack-authoring:{pack.id}@{pack.version}",
                impact=validation.counts,
            )
            governed = governed_or_404(lambda: governance.add_proposal(proposal))
        try:
            repository.save_domain_pack(pack, governed)
        except ValueError as error:
            if governed is not None:
                governance.proposals.pop(governed.proposal.id, None)
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="domain-pack.create",
            resource_type="domain-pack",
            resource_id=f"{pack.id}@{pack.version}",
            outcome="success",
            metadata={
                "counts": validation.counts,
                "proposalId": pack.proposal_id,
            },
        )
        return pack

    @app.post(
        "/api/v1/domain-packs/{pack_id}/{version}/publish",
        response_model=DomainPackRelease,
        tags=["schema"],
    )
    async def publish_domain_pack(
        pack_id: str,
        version: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.RELEASE_PUBLISH,
                    action="domain-pack.publish",
                )
            ),
        ],
    ) -> DomainPackRelease:
        synchronize_governance()
        draft = repository.get_domain_pack(pack_id, version)
        if draft is None:
            raise HTTPException(status_code=404, detail="domain pack not found")
        if draft.status == "published":
            return draft
        governed = governance.proposals.get(draft.proposal_id)
        if governed is None or governed.proposal.status != "accepted":
            raise HTTPException(
                status_code=409,
                detail="domain pack proposal is not accepted",
            )
        validation = analyze_domain_pack(draft)
        if not validation.valid:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "domain pack validation changed before publication",
                    "issues": [
                        item.model_dump(mode="json", by_alias=True) for item in validation.issues
                    ],
                },
            )
        published = draft.model_copy(
            update={
                "status": "published",
                "published_at": datetime.now(UTC),
                "rollback_snapshot": domain_pack_rollback_snapshot(draft),
            }
        )
        released_governed = governed.model_copy(deep=True)
        released_governed.proposal.status = "released"
        try:
            published = repository.publish_domain_pack(
                published,
                released_governed,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        governance.proposals[draft.proposal_id] = released_governed
        audit(
            request,
            actor=principal,
            action="domain-pack.publish",
            resource_type="domain-pack",
            resource_id=f"{published.id}@{published.version}",
            outcome="success",
            metadata={"counts": validation.counts},
        )
        return published

    @app.get(
        "/api/v1/domain-packs/{pack_id}/{version}/rollback-analysis",
        response_model=DomainPackRollbackAnalysis,
        tags=["schema"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="domain-pack.rollback.analyze",
                )
            )
        ],
    )
    async def domain_pack_rollback_analysis(
        pack_id: str,
        version: str,
    ) -> DomainPackRollbackAnalysis:
        pack = repository.get_domain_pack(pack_id, version)
        if pack is None:
            raise HTTPException(status_code=404, detail="domain pack not found")
        return analyze_domain_pack_rollback(pack)

    @app.post(
        "/api/v1/domain-packs/{pack_id}/{version}/rollback",
        response_model=DomainPackRollbackResponse,
        tags=["schema"],
    )
    async def rollback_domain_pack(
        pack_id: str,
        version: str,
        body: DomainPackRollbackRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.RELEASE_ROLLBACK,
                    action="domain-pack.rollback",
                )
            ),
        ],
    ) -> DomainPackRollbackResponse:
        pack = repository.get_domain_pack(pack_id, version)
        if pack is None:
            raise HTTPException(status_code=404, detail="domain pack not found")
        if pack.published_at != body.expected_published_at:
            raise HTTPException(
                status_code=409,
                detail="domain pack publication changed before rollback",
            )
        analysis = analyze_domain_pack_rollback(pack)
        if not analysis.safe:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "domain-pack-rollback-blocked",
                    "blockers": [
                        blocker.model_dump(mode="json", by_alias=True)
                        for blocker in analysis.blockers
                    ],
                },
            )
        restore = (
            repository.get_domain_pack(pack.id, analysis.restore_version)
            if analysis.restore_version
            else None
        )
        rolled_back_at = datetime.now(UTC)
        rolled_back = pack.model_copy(
            update={
                "status": "rolled-back",
                "rolled_back_at": rolled_back_at,
                "rollback_to_version": analysis.restore_version,
            }
        )
        try:
            rolled_back, restored = repository.rollback_domain_pack(
                rolled_back,
                restore,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="domain-pack.rollback",
            resource_type="domain-pack",
            resource_id=f"{pack.id}@{pack.version}",
            outcome="success",
            metadata={
                "restoredVersion": (restored.version if restored is not None else None),
                "comment": body.comment,
                "manifestDiffCounts": analysis.manifest_diff.counts,
            },
        )
        return DomainPackRollbackResponse(
            rolled_back=rolled_back,
            restored=restored,
            analysis=analysis,
        )

    @app.post(
        "/api/v1/schema-changes/analyze",
        response_model=SchemaChangeAnalysis,
        tags=["schema"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="schema.change.analyze",
                )
            )
        ],
    )
    async def analyze_schema_change_endpoint(
        body: SchemaChangeAnalysisRequest,
    ) -> SchemaChangeAnalysis:
        current_document = repository.get_schema_document(
            document_id=body.schema_id,
            kind=body.schema_kind,
        )
        if current_document is None:
            raise HTTPException(status_code=404, detail="schema document not found")
        model_by_kind = {
            "attribute-definition": AttributeDefinition,
            "entity-type": EntityType,
            "relationship-type": RelationshipType,
            "view-definition": ViewDefinition,
        }
        model = model_by_kind[body.schema_kind]
        try:
            before = model.model_validate(current_document)
            after = model.model_validate(body.proposed_document)
            return analyze_schema_change(
                schema_kind=body.schema_kind,
                before=before,
                after=after,
                entities=repository.list_entities(),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get(
        "/api/v1/schema-migrations",
        response_model=list[SchemaMigrationManifest],
        tags=["schema"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="schema.migration.read",
                )
            )
        ],
    )
    async def schema_migrations() -> list[SchemaMigrationManifest]:
        return repository.list_schema_migrations()

    @app.get(
        "/api/v1/schema-migrations/{migration_id}",
        response_model=SchemaMigrationManifest,
        tags=["schema"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="schema.migration.read",
                )
            )
        ],
    )
    async def schema_migration_detail(
        migration_id: str,
    ) -> SchemaMigrationManifest:
        manifest = repository.get_schema_migration(migration_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="schema migration not found")
        return manifest

    @app.post(
        "/api/v1/schema-migrations",
        response_model=SchemaMigrationManifest,
        tags=["schema"],
    )
    async def plan_schema_migration(
        body: SchemaMigrationPlanRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="schema.migration.plan",
                )
            ),
        ],
    ) -> SchemaMigrationManifest:
        if repository.get_schema_migration(body.id):
            raise HTTPException(status_code=409, detail="schema migration already exists")
        current_document = repository.get_schema_document(
            document_id=body.schema_id,
            kind=body.schema_kind,
        )
        if current_document is None:
            raise HTTPException(status_code=404, detail="schema document not found")
        model_by_kind = {
            "attribute-definition": AttributeDefinition,
            "entity-type": EntityType,
            "relationship-type": RelationshipType,
            "view-definition": ViewDefinition,
        }
        model = model_by_kind[body.schema_kind]
        try:
            before = model.model_validate(current_document)
            after = model.model_validate(body.proposed_document)
            analysis = analyze_schema_change(
                schema_kind=body.schema_kind,
                before=before,
                after=after,
                entities=repository.list_entities(),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if analysis.requires_migration and not body.operations:
            raise HTTPException(
                status_code=422,
                detail="migratory and breaking changes require migration operations",
            )
        frozen_revision_ids = repository.current_revision_ids(analysis.affected_entity_ids)
        if set(frozen_revision_ids) != set(analysis.affected_entity_ids):
            raise HTTPException(
                status_code=409,
                detail="affected entity set changed during migration planning",
            )
        proposal = AgentProposal(
            id=body.proposal_id,
            entity_id=None,
            proposal_type="schema",
            operations=[
                ChangeOperation(
                    operation="replace",
                    path=f"/schemas/{body.schema_kind}/{body.schema_id}",
                    before=current_document,
                    after=body.proposed_document,
                    citation_ids=body.citation_ids,
                    confidence=body.confidence,
                )
            ],
            risk="high",
            status="proposed",
            agent_run_id=f"schema-migration-plan:{body.id}",
            impact={
                "entityCount": len(analysis.affected_entity_ids),
                "relationCount": analysis.affected_relationship_count,
            },
        )
        governed = governed_or_404(lambda: governance.add_proposal(proposal))
        repository.save_governed_proposal(governed)
        manifest = SchemaMigrationManifest(
            id=body.id,
            schema_kind=body.schema_kind,
            schema_id=body.schema_id,
            from_version=analysis.from_version,
            to_version=analysis.to_version,
            proposed_document=body.proposed_document,
            analysis=analysis,
            operations=body.operations,
            proposal_id=body.proposal_id,
            frozen_revision_ids=frozen_revision_ids,
            data_version=body.data_version,
            created_by=principal.subject,
        )
        try:
            repository.save_schema_migration(manifest)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="schema.migration.plan",
            resource_type="schema-migration",
            resource_id=manifest.id,
            outcome="success",
            metadata={
                "schemaId": manifest.schema_id,
                "classification": analysis.classification,
                "affectedEntityCount": len(analysis.affected_entity_ids),
                "proposalId": manifest.proposal_id,
            },
        )
        return manifest

    @app.post(
        "/api/v1/schema-migrations/{migration_id}/apply",
        response_model=SchemaMigrationManifest,
        tags=["schema"],
    )
    async def apply_schema_migration(
        migration_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.RELEASE_PUBLISH,
                    action="schema.migration.apply",
                )
            ),
        ],
    ) -> SchemaMigrationManifest:
        synchronize_governance()
        manifest = repository.get_schema_migration(migration_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="schema migration not found")
        governed = governance.proposals.get(manifest.proposal_id)
        if governed is None or governed.proposal.status != "accepted":
            raise HTTPException(
                status_code=409,
                detail="schema migration proposal is not accepted",
            )
        entities = []
        try:
            for entity_id in manifest.frozen_revision_ids:
                entity = repository.get_entity_by_id(entity_id)
                if entity is None:
                    raise ValueError("affected entity is unavailable")
                entities.append(
                    apply_schema_migration_to_entity(
                        entity,
                        manifest,
                        policy_version="policy-1.0.0",
                    )
                )
            applied = manifest.model_copy(
                update={
                    "status": "applied",
                    "applied_revision_ids": {
                        entity.ref.id: entity.revision.revision_id for entity in entities
                    },
                    "applied_at": datetime.now(UTC),
                    "error": None,
                }
            )
            repository.apply_schema_migration(applied, entities)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="schema.migration.apply",
            resource_type="schema-migration",
            resource_id=applied.id,
            outcome="success",
            metadata={
                "schemaId": applied.schema_id,
                "schemaVersion": applied.to_version,
                "entityCount": len(entities),
            },
        )
        return applied

    @app.post(
        "/api/v1/schema-migrations/{migration_id}/rollback",
        response_model=SchemaMigrationManifest,
        tags=["schema"],
    )
    async def rollback_schema_migration(
        migration_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.RELEASE_ROLLBACK,
                    action="schema.migration.rollback",
                )
            ),
        ],
    ) -> SchemaMigrationManifest:
        manifest = repository.get_schema_migration(migration_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="schema migration not found")
        rolled_back = manifest.model_copy(
            update={
                "status": "rolled-back",
                "rolled_back_at": datetime.now(UTC),
                "error": None,
            }
        )
        try:
            repository.rollback_schema_migration(rolled_back)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="schema.migration.rollback",
            resource_type="schema-migration",
            resource_id=rolled_back.id,
            outcome="success",
            metadata={
                "schemaId": rolled_back.schema_id,
                "schemaVersion": rolled_back.from_version,
                "entityCount": len(rolled_back.frozen_revision_ids),
            },
        )
        return rolled_back

    @app.get(
        "/api/v1/entity-types",
        response_model=list[EntityType],
        tags=["schema"],
    )
    async def entity_types() -> list[EntityType]:
        return [
            EntityType.model_validate(document)
            for document in repository.list_schema_documents(kind="entity-type")
        ]

    @app.get(
        "/api/v1/entity-types/{type_id}",
        response_model=EntityType,
        tags=["schema"],
    )
    async def entity_type(type_id: str) -> EntityType:
        document = repository.get_schema_document(
            document_id=type_id,
            kind="entity-type",
        )
        if document is None:
            raise HTTPException(status_code=404, detail="entity type not found")
        return EntityType.model_validate(document)

    @app.get(
        "/api/v1/attribute-definitions/{attribute_id}",
        response_model=AttributeDefinition,
        tags=["schema"],
    )
    async def attribute_definition(attribute_id: str) -> AttributeDefinition:
        document = repository.get_schema_document(
            document_id=attribute_id,
            kind="attribute-definition",
        )
        if document is None:
            raise HTTPException(status_code=404, detail="attribute definition not found")
        return AttributeDefinition.model_validate(document)

    @app.get(
        "/api/v1/authoring-drafts",
        response_model=list[AuthoringDraftRecord],
        tags=["authoring"],
    )
    async def authoring_drafts(
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="authoring.draft.read",
                )
            ),
        ],
        status: Literal["editing", "submitted", "abandoned"] | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[AuthoringDraftRecord]:
        return repository.list_authoring_drafts(
            principal_workspace_id(principal),
            status=status,
            limit=limit,
        )

    @app.post(
        "/api/v1/authoring-drafts",
        response_model=AuthoringDraftRecord,
        status_code=201,
        tags=["authoring"],
    )
    async def create_authoring_draft(
        body: AuthoringDraftCreateRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="authoring.draft.create",
                )
            ),
        ],
    ) -> AuthoringDraftRecord:
        authoring_schema(body.draft)
        validate_authoring_draft_identity(
            mode=body.mode,
            draft=body.draft,
            base_revision_id=body.base_revision_id,
        )
        workspace_id = principal_workspace_id(principal)
        repository.ensure_workspace(
            workspace_id,
            f"{principal.display_name} workspace",
        )
        record = AuthoringDraftRecord(
            id=body.id,
            workspace_id=workspace_id,
            created_by=principal.subject,
            mode=body.mode,
            draft=body.draft,
            base_revision_id=body.base_revision_id,
        )
        try:
            repository.create_authoring_draft(record)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="authoring.draft.create",
            resource_type="authoring-draft",
            resource_id=record.id,
            outcome="success",
            metadata={"mode": record.mode, "entityId": record.draft.id},
        )
        return record

    @app.get(
        "/api/v1/authoring-drafts/{draft_id}",
        response_model=AuthoringDraftRecord,
        tags=["authoring"],
    )
    async def authoring_draft_detail(
        draft_id: str,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="authoring.draft.read",
                )
            ),
        ],
    ) -> AuthoringDraftRecord:
        record = repository.get_authoring_draft(
            principal_workspace_id(principal),
            draft_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="authoring draft not found")
        return record

    @app.put(
        "/api/v1/authoring-drafts/{draft_id}",
        response_model=AuthoringDraftRecord,
        tags=["authoring"],
    )
    async def update_authoring_draft(
        draft_id: str,
        body: AuthoringDraftUpdateRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="authoring.draft.update",
                )
            ),
        ],
    ) -> AuthoringDraftRecord:
        workspace_id = principal_workspace_id(principal)
        existing = repository.get_authoring_draft(workspace_id, draft_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="authoring draft not found")
        if existing.status != "editing":
            raise HTTPException(
                status_code=409,
                detail="only editing drafts can be updated",
            )
        authoring_schema(body.draft)
        validate_authoring_draft_identity(
            mode=existing.mode,
            draft=body.draft,
            base_revision_id=existing.base_revision_id,
        )
        updated = existing.model_copy(
            update={
                "draft": body.draft,
                "version": existing.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            repository.update_authoring_draft(
                updated,
                expected_version=body.expected_version,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="authoring.draft.update",
            resource_type="authoring-draft",
            resource_id=updated.id,
            outcome="success",
            metadata={"version": updated.version},
        )
        return updated

    @app.post(
        "/api/v1/authoring-drafts/{draft_id}/submit",
        response_model=AuthoringDraftSubmissionResponse,
        tags=["authoring"],
    )
    async def submit_authoring_draft(
        draft_id: str,
        body: AuthoringDraftSubmitRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="authoring.draft.submit",
                )
            ),
        ],
    ) -> AuthoringDraftSubmissionResponse:
        workspace_id = principal_workspace_id(principal)
        record = repository.get_authoring_draft(workspace_id, draft_id)
        if record is None:
            raise HTTPException(status_code=404, detail="authoring draft not found")
        if record.status == "submitted" and record.proposal_id:
            existing_proposal = repository.get_governed_proposal(record.proposal_id)
            if existing_proposal is None:
                raise HTTPException(
                    status_code=409,
                    detail="submitted draft proposal is unavailable",
                )
            return AuthoringDraftSubmissionResponse(
                draft=record,
                proposal=existing_proposal,
            )
        if record.status != "editing":
            raise HTTPException(
                status_code=409,
                detail="only editing drafts can be submitted",
            )
        if record.version != body.expected_version:
            raise HTTPException(
                status_code=409,
                detail="authoring draft version conflict",
            )
        entity_type_model, attributes, validation = authoring_schema(record.draft)
        if not validation.valid:
            raise HTTPException(
                status_code=422,
                detail=validation.model_dump(mode="json", by_alias=True),
            )
        current = validate_authoring_draft_identity(
            mode=record.mode,
            draft=record.draft,
            base_revision_id=record.base_revision_id,
        )
        if record.mode == "create" and any(
            entity.ref.id == record.draft.id or entity.ref.slug == record.draft.slug
            for entity in repository.list_entities()
        ):
            raise HTTPException(
                status_code=409,
                detail="entity id or slug already exists",
            )
        existing_governed = repository.get_governed_proposal(body.proposal_id)
        if existing_governed is None:
            try:
                proposal = (
                    build_creation_proposal(
                        proposal_id=body.proposal_id,
                        draft=record.draft,
                        entity_type=entity_type_model,
                        attributes=attributes,
                        agent_run_id=body.agent_run_id,
                        confidence=body.confidence,
                        risk=body.risk,
                    )
                    if record.mode == "create"
                    else build_revision_proposal(
                        proposal_id=body.proposal_id,
                        draft=record.draft,
                        current=current,
                        entity_type=entity_type_model,
                        attributes=attributes,
                        base_revision_id=record.base_revision_id or "",
                        agent_run_id=body.agent_run_id,
                        confidence=body.confidence,
                        risk=body.risk,
                    )
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            governed = governed_or_404(lambda: governance.add_proposal(proposal))
            repository.save_governed_proposal(governed)
        else:
            governed = existing_governed
            if governed.proposal.entity_id != record.draft.id:
                raise HTTPException(
                    status_code=409,
                    detail="proposal id belongs to another entity",
                )
        submitted = record.model_copy(
            update={
                "status": "submitted",
                "proposal_id": governed.proposal.id,
                "version": record.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            repository.update_authoring_draft(
                submitted,
                expected_version=body.expected_version,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="authoring.draft.submit",
            resource_type="authoring-draft",
            resource_id=submitted.id,
            outcome="success",
            metadata={
                "proposalId": governed.proposal.id,
                "version": submitted.version,
            },
        )
        return AuthoringDraftSubmissionResponse(
            draft=submitted,
            proposal=governed,
        )

    @app.post(
        "/api/v1/authoring-drafts/{draft_id}/abandon",
        response_model=AuthoringDraftRecord,
        tags=["authoring"],
    )
    async def abandon_authoring_draft(
        draft_id: str,
        body: AuthoringDraftAbandonRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="authoring.draft.abandon",
                )
            ),
        ],
    ) -> AuthoringDraftRecord:
        workspace_id = principal_workspace_id(principal)
        existing = repository.get_authoring_draft(workspace_id, draft_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="authoring draft not found")
        if existing.status != "editing":
            raise HTTPException(
                status_code=409,
                detail="only editing drafts can be abandoned",
            )
        abandoned = existing.model_copy(
            update={
                "status": "abandoned",
                "version": existing.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            repository.update_authoring_draft(
                abandoned,
                expected_version=body.expected_version,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="authoring.draft.abandon",
            resource_type="authoring-draft",
            resource_id=abandoned.id,
            outcome="success",
        )
        return abandoned

    @app.post(
        "/api/v1/entity-drafts/validate",
        response_model=DraftValidation,
        tags=["authoring"],
    )
    async def validate_draft(draft: EntityDraft) -> DraftValidation:
        _, _, validation = authoring_schema(draft)
        return validation

    @app.get(
        "/api/v1/entities/{entity_id}/authoring-draft",
        response_model=EntityAuthoringDraftResponse,
        tags=["authoring"],
    )
    async def entity_authoring_draft(
        entity_id: str,
        _: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="entity.authoring.read",
                )
            ),
        ],
    ) -> EntityAuthoringDraftResponse:
        entity = repository.get_entity_by_id(entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="entity not found")
        draft = build_draft_from_entity(entity)
        entity_type_model, attributes, _ = authoring_schema(draft)
        view_document = repository.get_schema_document(
            document_id=entity_type_model.default_view_definition_id,
            kind="view-definition",
        )
        if view_document is None:
            raise HTTPException(
                status_code=409,
                detail="entity type default view is unavailable",
            )
        return EntityAuthoringDraftResponse(
            draft=draft,
            base_revision_id=entity.revision.revision_id,
            entity_type=entity_type_model,
            attribute_definitions=attributes,
            view_definition=ViewDefinition.model_validate(view_document),
        )

    @app.post(
        "/api/v1/entity-drafts/proposals",
        response_model=GovernedProposal,
        tags=["authoring"],
    )
    async def propose_entity_draft(
        request: EntityDraftProposalRequest,
        http_request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="proposal.create",
                )
            ),
        ],
    ) -> GovernedProposal:
        entity_type_model, attributes, validation = authoring_schema(request.draft)
        if not validation.valid:
            raise HTTPException(
                status_code=422,
                detail=validation.model_dump(mode="json", by_alias=True),
            )
        existing_entities = repository.list_entities()
        if any(
            entity.ref.id == request.draft.id or entity.ref.slug == request.draft.slug
            for entity in existing_entities
        ):
            raise HTTPException(status_code=409, detail="entity id or slug already exists")
        proposal = build_creation_proposal(
            proposal_id=request.proposal_id,
            draft=request.draft,
            entity_type=entity_type_model,
            attributes=attributes,
            agent_run_id=request.agent_run_id,
            confidence=request.confidence,
            risk=request.risk,
        )
        governed = governed_or_404(lambda: governance.add_proposal(proposal))
        repository.save_governed_proposal(governed)
        audit(
            http_request,
            actor=principal,
            action="proposal.create",
            resource_type="proposal",
            resource_id=proposal.id,
            outcome="success",
            metadata={"entityId": request.draft.id, "proposalType": "content"},
        )
        return governed

    @app.post(
        "/api/v1/entities/{entity_id}/draft-proposals",
        response_model=GovernedProposal,
        tags=["authoring"],
    )
    async def propose_entity_revision(
        entity_id: str,
        request: EntityRevisionProposalRequest,
        http_request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_CREATE,
                    action="entity.revision.propose",
                )
            ),
        ],
    ) -> GovernedProposal:
        current = repository.get_entity_by_id(entity_id)
        if current is None:
            raise HTTPException(status_code=404, detail="entity not found")
        entity_type_model, attributes, validation = authoring_schema(request.draft)
        if not validation.valid:
            raise HTTPException(
                status_code=422,
                detail=validation.model_dump(mode="json", by_alias=True),
            )
        try:
            proposal = build_revision_proposal(
                proposal_id=request.proposal_id,
                draft=request.draft,
                current=current,
                entity_type=entity_type_model,
                attributes=attributes,
                base_revision_id=request.base_revision_id,
                agent_run_id=request.agent_run_id,
                confidence=request.confidence,
                risk=request.risk,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        governed = governed_or_404(lambda: governance.add_proposal(proposal))
        repository.save_governed_proposal(governed)
        audit(
            http_request,
            actor=principal,
            action="entity.revision.propose",
            resource_type="proposal",
            resource_id=proposal.id,
            outcome="success",
            metadata={
                "entityId": entity_id,
                "baseRevisionId": request.base_revision_id,
                "operationCount": len(proposal.operations),
            },
        )
        return governed

    @app.get(
        "/api/v1/view-definitions/{view_id}",
        response_model=ViewDefinition,
        tags=["schema"],
    )
    async def view_definition(view_id: str) -> ViewDefinition:
        document = repository.get_schema_document(
            document_id=view_id,
            kind="view-definition",
        )
        if document is None:
            raise HTTPException(status_code=404, detail="view definition not found")
        return ViewDefinition.model_validate(document)

    def quality_maintenance_service() -> QualityMaintenanceService:
        profiles = [
            QualityProfile.model_validate(document)
            for document in repository.list_schema_documents(kind="quality-profile")
        ]
        return QualityMaintenanceService(repository, profiles)

    def maintenance_work_view(
        work_item: MaintenanceWorkItem,
    ) -> MaintenanceWorkItemView:
        document = work_item.model_dump(mode="json", by_alias=True)
        document.pop("leaseToken", None)
        return MaintenanceWorkItemView.model_validate(document)

    @app.get(
        "/api/v1/quality/summary",
        response_model=QualitySummaryResponse,
        tags=["quality"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="quality.summary.read",
                )
            )
        ],
    )
    async def quality_summary() -> QualitySummaryResponse:
        assessments = repository.list_quality_assessments()
        open_tasks = repository.list_maintenance_tasks(status="open")
        scheduled_tasks = repository.list_maintenance_tasks(status="scheduled")
        tasks_by_action: dict[str, int] = {}
        for task in [*open_tasks, *scheduled_tasks]:
            tasks_by_action[task.action] = tasks_by_action.get(task.action, 0) + 1
        return QualitySummaryResponse(
            generated_at=datetime.now(UTC),
            assessed_entity_count=len(assessments),
            healthy_count=sum(item.status == "healthy" for item in assessments),
            attention_count=sum(item.status == "attention" for item in assessments),
            critical_count=sum(item.status == "critical" for item in assessments),
            average_score=(
                round(
                    sum(item.score for item in assessments) / len(assessments),
                    1,
                )
                if assessments
                else 100
            ),
            open_task_count=len(open_tasks),
            scheduled_task_count=len(scheduled_tasks),
            tasks_by_action=tasks_by_action,
        )

    @app.get(
        "/api/v1/quality/assessments",
        response_model=list[QualityAssessment],
        tags=["quality"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="quality.assessment.read",
                )
            )
        ],
    )
    async def quality_assessments(
        entity_id: Annotated[
            str | None,
            Query(alias="entityId", max_length=160),
        ] = None,
        status: Literal["healthy", "attention", "critical"] | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[QualityAssessment]:
        return repository.list_quality_assessments(
            entity_id=entity_id,
            status=status,
            limit=limit,
        )

    @app.get(
        "/api/v1/quality/tasks",
        response_model=list[MaintenanceTask],
        tags=["quality"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="quality.task.read",
                )
            )
        ],
    )
    async def quality_tasks(
        entity_id: Annotated[
            str | None,
            Query(alias="entityId", max_length=160),
        ] = None,
        status: Literal[
            "open",
            "scheduled",
            "resolved",
            "superseded",
        ]
        | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[MaintenanceTask]:
        return repository.list_maintenance_tasks(
            entity_id=entity_id,
            status=status,
            limit=limit,
        )

    @app.get(
        "/api/v1/maintenance/work-items",
        response_model=list[MaintenanceWorkItemView],
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.work.read",
                )
            )
        ],
    )
    async def maintenance_work_items(
        entity_id: Annotated[
            str | None,
            Query(alias="entityId", max_length=160),
        ] = None,
        route: Literal[
            "source-acquisition",
            "translation-evidence",
            "taxonomy-review",
        ]
        | None = None,
        status: Literal[
            "queued",
            "ready",
            "claimed",
            "blocked",
            "completed",
            "superseded",
        ]
        | None = None,
        assignee_id: Annotated[
            str | None,
            Query(alias="assigneeId", max_length=240),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[MaintenanceWorkItemView]:
        return [
            maintenance_work_view(item)
            for item in repository.list_maintenance_work_items(
                entity_id=entity_id,
                route=route,
                status=status,
                assignee_id=assignee_id,
                limit=limit,
            )
        ]

    @app.get(
        "/api/v1/maintenance/work-items/{work_item_id}",
        response_model=MaintenanceWorkItemView,
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.work.read",
                )
            )
        ],
    )
    async def maintenance_work_item(
        work_item_id: str,
    ) -> MaintenanceWorkItemView:
        item = repository.get_maintenance_work_item(work_item_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail="maintenance work item not found",
            )
        return maintenance_work_view(item)

    @app.post(
        "/api/v1/maintenance/work-items/{work_item_id}/claim",
        response_model=MaintenanceWorkClaimResponse,
        tags=["maintenance"],
    )
    async def claim_maintenance_work(
        work_item_id: str,
        body: MaintenanceWorkClaimRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="maintenance.work.claim",
                )
            ),
        ],
    ) -> MaintenanceWorkClaimResponse:
        try:
            item = repository.claim_maintenance_work_item(
                work_item_id,
                assignee_id=principal.subject,
                lease_seconds=body.lease_seconds,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=(404 if "not found" in str(error) else 409),
                detail=str(error),
            ) from error
        if item.status == "superseded":
            audit(
                request,
                actor=principal,
                action="maintenance.work.claim",
                resource_type="maintenance-work-item",
                resource_id=item.id,
                outcome="failed",
                metadata={"reason": "stale entity revision"},
            )
            raise HTTPException(
                status_code=409,
                detail="maintenance work item revision is no longer current",
            )
        assert item.lease_token is not None
        audit(
            request,
            actor=principal,
            action="maintenance.work.claim",
            resource_type="maintenance-work-item",
            resource_id=item.id,
            outcome="success",
            metadata={
                "route": item.route,
                "attempt": item.attempt,
                "leaseExpiresAt": item.lease_expires_at,
            },
        )
        return MaintenanceWorkClaimResponse(
            work_item=maintenance_work_view(item),
            lease_token=item.lease_token,
        )

    @app.post(
        "/api/v1/maintenance/work-items/{work_item_id}/heartbeat",
        response_model=MaintenanceWorkItemView,
        tags=["maintenance"],
    )
    async def heartbeat_maintenance_work(
        work_item_id: str,
        body: MaintenanceWorkHeartbeatRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="maintenance.work.heartbeat",
                )
            ),
        ],
    ) -> MaintenanceWorkItemView:
        try:
            item = repository.heartbeat_maintenance_work_item(
                work_item_id,
                assignee_id=principal.subject,
                lease_token=body.lease_token,
                lease_seconds=body.lease_seconds,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=(404 if "not found" in str(error) else 409),
                detail=str(error),
            ) from error
        audit(
            request,
            actor=principal,
            action="maintenance.work.heartbeat",
            resource_type="maintenance-work-item",
            resource_id=item.id,
            outcome="success",
            metadata={"leaseExpiresAt": item.lease_expires_at},
        )
        return maintenance_work_view(item)

    @app.post(
        "/api/v1/maintenance/work-items/{work_item_id}/complete",
        response_model=MaintenanceWorkItemView,
        tags=["maintenance"],
    )
    async def complete_maintenance_work(
        work_item_id: str,
        body: MaintenanceWorkCompleteRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="maintenance.work.complete",
                )
            ),
        ],
    ) -> MaintenanceWorkItemView:
        try:
            item = repository.complete_maintenance_work_item(
                work_item_id,
                assignee_id=principal.subject,
                lease_token=body.lease_token,
                evidence_refs=body.evidence_refs,
                output_refs=body.output_refs,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=(404 if "not found" in str(error) else 409),
                detail=str(error),
            ) from error
        if item.status == "superseded":
            audit(
                request,
                actor=principal,
                action="maintenance.work.complete",
                resource_type="maintenance-work-item",
                resource_id=item.id,
                outcome="failed",
                metadata={"reason": "stale entity revision"},
            )
            raise HTTPException(
                status_code=409,
                detail="maintenance work item revision is no longer current",
            )
        audit(
            request,
            actor=principal,
            action="maintenance.work.complete",
            resource_type="maintenance-work-item",
            resource_id=item.id,
            outcome="success",
            metadata={
                "route": item.route,
                "evidenceRefs": [{"kind": ref.kind, "id": ref.id} for ref in item.evidence_refs],
                "outputRefs": [{"kind": ref.kind, "id": ref.id} for ref in item.output_refs],
            },
        )
        return maintenance_work_view(item)

    @app.post(
        "/api/v1/maintenance/work-items/{work_item_id}/translation-proposal",
        response_model=MaintenanceProposalSubmissionResponse,
        tags=["maintenance"],
    )
    async def submit_maintenance_translation(
        work_item_id: str,
        body: MaintenanceTranslationProposalRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="maintenance.translation.submit",
                )
            ),
        ],
    ) -> MaintenanceProposalSubmissionResponse:
        if not principal.can(Permission.PROPOSAL_CREATE):
            raise HTTPException(status_code=403, detail="insufficient permission")
        work_item = repository.get_maintenance_work_item(work_item_id)
        if work_item is None:
            raise HTTPException(status_code=404, detail="maintenance work item not found")
        entity = repository.get_entity_by_id(work_item.entity.id)
        if entity is None:
            raise HTTPException(status_code=409, detail="maintenance entity does not exist")
        try:
            governed = build_translation_work_proposal(
                work_item,
                entity,
                target_locale=body.target_locale,
                translated_name=body.translated_name,
                translated_description=body.translated_description,
                citation_ids=body.citation_ids,
                confidence=body.confidence,
            )
            item, persisted = repository.submit_maintenance_governed_proposal(
                work_item_id,
                assignee_id=principal.subject,
                lease_token=body.lease_token,
                governed=governed,
                evidence_refs=[
                    MaintenanceEvidenceLink(kind="citation", id=citation_id)
                    for citation_id in sorted(set(body.citation_ids))
                ],
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if item.status == "superseded":
            raise HTTPException(
                status_code=409,
                detail="maintenance work item revision is no longer current",
            )
        audit(
            request,
            actor=principal,
            action="maintenance.translation.submit",
            resource_type="maintenance-work-item",
            resource_id=item.id,
            outcome="success",
            metadata={
                "proposalId": persisted.proposal.id,
                "targetLocale": body.target_locale,
                "citationIds": sorted(set(body.citation_ids)),
                "revisionId": item.revision_id,
            },
        )
        return MaintenanceProposalSubmissionResponse(
            work_item=maintenance_work_view(item),
            proposal=persisted,
        )

    @app.post(
        "/api/v1/maintenance/work-items/{work_item_id}/taxonomy-proposal",
        response_model=MaintenanceProposalSubmissionResponse,
        tags=["maintenance"],
    )
    async def submit_maintenance_taxonomy(
        work_item_id: str,
        body: MaintenanceTaxonomyProposalRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="maintenance.taxonomy.submit",
                )
            ),
        ],
    ) -> MaintenanceProposalSubmissionResponse:
        if not principal.can(Permission.PROPOSAL_CREATE):
            raise HTTPException(status_code=403, detail="insufficient permission")
        work_item = repository.get_maintenance_work_item(work_item_id)
        if work_item is None:
            raise HTTPException(status_code=404, detail="maintenance work item not found")
        entity = repository.get_entity_by_id(work_item.entity.id)
        if entity is None:
            raise HTTPException(status_code=409, detail="maintenance entity does not exist")
        type_document = repository.get_schema_document(
            document_id=entity.ref.type_id,
            kind="entity-type",
        )
        if type_document is None:
            raise HTTPException(status_code=409, detail="entity type schema does not exist")
        entity_type = EntityType.model_validate(type_document)
        taxonomy_nodes = repository.list_taxonomy_nodes()
        known_node = next(
            (node for node in taxonomy_nodes if node.id == body.taxonomy_node_id),
            None,
        )
        if known_node is None:
            raise HTTPException(status_code=409, detail="taxonomy node does not exist")
        allowed_node_ids = (
            set(entity_type.allowed_taxonomy_node_ids)
            if entity_type.allowed_taxonomy_node_ids
            else {
                node.id
                for node in taxonomy_nodes
                if node.space_id == entity_type.space_id
            }
        )
        try:
            governed = build_taxonomy_work_proposal(
                work_item,
                entity,
                taxonomy_node_id=body.taxonomy_node_id,
                allowed_taxonomy_node_ids=allowed_node_ids,
                citation_ids=body.citation_ids,
                confidence=body.confidence,
            )
            item, persisted = repository.submit_maintenance_governed_proposal(
                work_item_id,
                assignee_id=principal.subject,
                lease_token=body.lease_token,
                governed=governed,
                evidence_refs=[
                    MaintenanceEvidenceLink(kind="citation", id=citation_id)
                    for citation_id in sorted(set(body.citation_ids))
                ],
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if item.status == "superseded":
            raise HTTPException(
                status_code=409,
                detail="maintenance work item revision is no longer current",
            )
        audit(
            request,
            actor=principal,
            action="maintenance.taxonomy.submit",
            resource_type="maintenance-work-item",
            resource_id=item.id,
            outcome="success",
            metadata={
                "proposalId": persisted.proposal.id,
                "taxonomyNodeId": body.taxonomy_node_id,
                "citationIds": sorted(set(body.citation_ids)),
                "revisionId": item.revision_id,
            },
        )
        return MaintenanceProposalSubmissionResponse(
            work_item=maintenance_work_view(item),
            proposal=persisted,
        )

    @app.post(
        "/api/v1/maintenance/work-items/{work_item_id}/block",
        response_model=MaintenanceWorkItemView,
        tags=["maintenance"],
    )
    async def block_maintenance_work(
        work_item_id: str,
        body: MaintenanceWorkBlockRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="maintenance.work.block",
                )
            ),
        ],
    ) -> MaintenanceWorkItemView:
        try:
            item = repository.block_maintenance_work_item(
                work_item_id,
                assignee_id=principal.subject,
                lease_token=body.lease_token,
                reason=body.reason,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=(404 if "not found" in str(error) else 409),
                detail=str(error),
            ) from error
        if item.status == "superseded":
            raise HTTPException(
                status_code=409,
                detail="maintenance work item revision is no longer current",
            )
        audit(
            request,
            actor=principal,
            action="maintenance.work.block",
            resource_type="maintenance-work-item",
            resource_id=item.id,
            outcome="success",
            metadata={"route": item.route, "reason": item.blocked_reason},
        )
        return maintenance_work_view(item)

    @app.post(
        "/api/v1/maintenance/work-items/{work_item_id}/requeue",
        response_model=MaintenanceWorkItemView,
        tags=["maintenance"],
    )
    async def requeue_blocked_maintenance_work(
        work_item_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="maintenance.work.requeue",
                )
            ),
        ],
    ) -> MaintenanceWorkItemView:
        try:
            item, event_id = repository.requeue_blocked_maintenance_work_item(
                work_item_id,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=(404 if "not found" in str(error) else 409),
                detail=str(error),
            ) from error
        if item.status == "superseded":
            raise HTTPException(
                status_code=409,
                detail="maintenance work item revision is no longer current",
            )
        audit(
            request,
            actor=principal,
            action="maintenance.work.requeue",
            resource_type="maintenance-work-item",
            resource_id=item.id,
            outcome="success",
            metadata={
                "route": item.route,
                "revisionId": item.revision_id,
                "outboxEventId": event_id,
            },
        )
        return maintenance_work_view(item)

    @app.post(
        "/api/v1/maintenance/work-items/{work_item_id}/release",
        response_model=MaintenanceWorkItemView,
        tags=["maintenance"],
    )
    async def release_maintenance_work(
        work_item_id: str,
        body: MaintenanceWorkReleaseRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="maintenance.work.release",
                )
            ),
        ],
    ) -> MaintenanceWorkItemView:
        try:
            item = repository.release_maintenance_work_item(
                work_item_id,
                assignee_id=principal.subject,
                lease_token=body.lease_token,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=(404 if "not found" in str(error) else 409),
                detail=str(error),
            ) from error
        audit(
            request,
            actor=principal,
            action="maintenance.work.release",
            resource_type="maintenance-work-item",
            resource_id=item.id,
            outcome="success",
            metadata={"route": item.route},
        )
        return maintenance_work_view(item)

    @app.post(
        "/api/v1/quality/scans",
        response_model=QualityScanResult,
        tags=["quality"],
    )
    async def scan_quality(
        body: QualityScanRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="quality.scan",
                )
            ),
        ],
    ) -> QualityScanResult:
        result = quality_maintenance_service().scan(entity_ids=body.entity_ids or None)
        audit(
            request,
            actor=principal,
            action="quality.scan",
            resource_type="quality-assessment",
            resource_id=",".join(body.entity_ids) or "all-published",
            outcome="success" if not result.failures else "failed",
            metadata={
                "assessedCount": len(result.assessed),
                "failureCount": len(result.failures),
                "openTaskCount": result.open_task_count,
            },
        )
        return result

    @app.get(
        "/api/v1/operations/summary",
        response_model=OperationsSummaryResponse,
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="operations.summary.read",
                )
            )
        ],
    )
    async def operations_summary() -> OperationsSummaryResponse:
        proposals = repository.list_governed_proposals()
        conflicts = detect_proposal_conflicts(proposals)
        schedules = repository.list_agent_graph_schedules(limit=8)
        sources = repository.list_source_definitions()
        entities = repository.list_entities()
        releases = repository.list_releases()
        active_schedule_statuses = {"queued", "dispatched", "running"}
        review_statuses = {"proposed", "human-review", "policy-blocked"}
        evidence_total = 0
        evidence_covered = 0
        entity_names = {entity.ref.id: entity.ref.canonical_name for entity in entities}
        for entity in entities:
            evidence_items = [
                *entity.claims,
                *entity.sections,
                *entity.relationships,
            ]
            evidence_total += len(evidence_items)
            evidence_covered += sum(bool(item.citation_ids) for item in evidence_items)
        evidence_coverage = (
            round((evidence_covered / evidence_total) * 100, 1) if evidence_total else 100
        )
        recent_proposals = proposals[:8]
        return OperationsSummaryResponse(
            generated_at=datetime.now(UTC),
            data_version=settings.data_version,
            agent_definition_count=len(agent_registry.definitions),
            active_schedule_count=repository.count_agent_graph_schedules(
                statuses=active_schedule_statuses
            ),
            proposal_count=len(proposals),
            review_queue_count=sum(item.proposal.status in review_statuses for item in proposals),
            high_risk_review_count=sum(
                item.proposal.status in review_statuses
                and item.proposal.risk in {"high", "critical"}
                for item in proposals
            ),
            proposal_conflict_count=len(conflicts),
            evidence_coverage_percent=evidence_coverage,
            source_count=len(sources),
            active_source_count=sum(evaluate_source_policy(source).allowed for source in sources),
            pending_acquisition_count=repository.count_source_acquisition_jobs(
                statuses=active_schedule_statuses
            ),
            pending_outbox_count=len(repository.pending_outbox_ids()),
            agents=[
                OperationsAgentItem(
                    id=definition.id,
                    name=definition.name,
                    role=definition.role,
                    model_policy=definition.model_policy,
                    version=definition.version,
                )
                for definition in agent_registry.definitions.values()
            ],
            proposals=[
                OperationsProposalItem(
                    id=item.proposal.id,
                    entity_id=item.proposal.entity_id,
                    entity_name=(
                        entity_names.get(item.proposal.entity_id)
                        if item.proposal.entity_id
                        else None
                    )
                    or item.proposal.entity_id
                    or "Schema / 全局变更",
                    proposal_type=item.proposal.proposal_type,
                    risk=item.proposal.risk,
                    status=item.proposal.status,
                    operation_count=len(item.proposal.operations),
                    conflict_count=sum(
                        item.proposal.id in conflict.proposal_ids for conflict in conflicts
                    ),
                    impact=item.proposal.impact,
                )
                for item in recent_proposals
            ],
            schedules=[
                OperationsScheduleItem(
                    id=item.id,
                    graph_id=item.graph_id,
                    graph_version=item.graph_version,
                    trigger_type=item.trigger_type,
                    status=item.status,
                    requested_by=item.requested_by,
                    created_at=item.created_at,
                )
                for item in schedules
            ],
            latest_release=releases[0] if releases else None,
        )

    @app.get(
        "/api/v1/agent-runs",
        response_model=list[AgentRun],
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def agent_runs() -> list[AgentRun]:
        return AGENT_RUNS

    @app.get(
        "/api/v1/agent-runtimes",
        response_model=list[AgentRuntimeState],
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="agent.runtime.read",
                )
            )
        ],
    )
    async def agent_runtimes() -> list[AgentRuntimeState]:
        return repository.list_agent_runtime_states()

    @app.put(
        "/api/v1/agent-runtimes/self",
        response_model=AgentRuntimeState,
        tags=["maintenance"],
    )
    async def register_agent_runtime(
        body: AgentRuntimeRegistrationRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="agent.runtime.register",
                )
            ),
        ],
    ) -> AgentRuntimeState:
        definition = agent_registry.definitions.get(body.definition_id)
        if definition is None or definition.version != body.definition_version:
            raise HTTPException(
                status_code=409,
                detail="pinned agent definition is not registered",
            )
        capabilities = sorted(
            {item.strip() for item in body.capabilities if item.strip()}
        )
        unknown_capabilities = sorted(
            set(capabilities) - set(definition.capabilities)
        )
        if unknown_capabilities:
            raise HTTPException(
                status_code=409,
                detail=(
                    "runtime advertises capabilities outside its definition: "
                    + ", ".join(unknown_capabilities)
                ),
            )
        for route in body.supported_routes:
            missing = sorted(
                set(maintenance_route_capabilities(route)) - set(capabilities)
            )
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"runtime is missing capabilities for {route}: "
                        + ", ".join(missing)
                    ),
                )
        if len(body.labels) > 30 or any(
            not key.strip()
            or len(key) > 80
            or len(value) > 240
            for key, value in body.labels.items()
        ):
            raise HTTPException(status_code=422, detail="runtime labels are invalid")
        now = datetime.now(UTC)
        runtime = AgentRuntime(
            id=principal.subject,
            definition_id=body.definition_id,
            definition_version=body.definition_version,
            capabilities=capabilities,
            supported_routes=body.supported_routes,
            status=body.status,
            max_concurrency=body.max_concurrency,
            heartbeat_ttl_seconds=body.heartbeat_ttl_seconds,
            labels=body.labels,
            registered_at=now,
            last_heartbeat_at=now,
            updated_at=now,
        )
        try:
            state = repository.register_agent_runtime(runtime)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="agent.runtime.register",
            resource_type="agent-runtime",
            resource_id=runtime.id,
            outcome="success",
            metadata={
                "definitionId": runtime.definition_id,
                "definitionVersion": runtime.definition_version,
                "supportedRoutes": runtime.supported_routes,
                "maxConcurrency": runtime.max_concurrency,
            },
        )
        return state

    @app.post(
        "/api/v1/agent-runtimes/self/heartbeat",
        response_model=AgentRuntimeState,
        tags=["maintenance"],
    )
    async def heartbeat_agent_runtime(
        body: AgentRuntimeHeartbeatRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="agent.runtime.heartbeat",
                )
            ),
        ],
    ) -> AgentRuntimeState:
        try:
            state = repository.heartbeat_agent_runtime(
                principal.subject,
                status=body.status,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=(404 if "not found" in str(error) else 409),
                detail=str(error),
            ) from error
        audit(
            request,
            actor=principal,
            action="agent.runtime.heartbeat",
            resource_type="agent-runtime",
            resource_id=principal.subject,
            outcome="success",
            metadata={"effectiveStatus": state.effective_status},
        )
        return state

    @app.post(
        "/api/v1/agent-runtimes/self/claim-next",
        response_model=MaintenanceWorkClaimResponse,
        responses={204: {"description": "No compatible work or no capacity"}},
        tags=["maintenance"],
    )
    async def claim_next_agent_work(
        body: MaintenanceWorkClaimRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="agent.runtime.claim-next",
                )
            ),
        ],
    ) -> MaintenanceWorkClaimResponse | Response:
        try:
            item = repository.claim_next_maintenance_work_item(
                principal.subject,
                lease_seconds=body.lease_seconds,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=(404 if "not found" in str(error) else 409),
                detail=str(error),
            ) from error
        if item is None:
            return Response(status_code=204)
        assert item.lease_token is not None
        lease_token = item.lease_token
        audit(
            request,
            actor=principal,
            action="agent.runtime.claim-next",
            resource_type="maintenance-work-item",
            resource_id=item.id,
            outcome="success",
            metadata={
                "runtimeId": principal.subject,
                "route": item.route,
                "revisionId": item.revision_id,
                "leaseExpiresAt": item.lease_expires_at,
            },
        )
        return MaintenanceWorkClaimResponse(
            work_item=maintenance_work_view(item),
            lease_token=lease_token,
        )

    @app.get(
        "/api/v1/agent-definitions",
        response_model=list[AgentDefinition],
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def agent_definitions() -> list[AgentDefinition]:
        return list(agent_registry.definitions.values())

    @app.get(
        "/api/v1/agent-graphs",
        response_model=list[AgentGraphSpec],
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def agent_graphs() -> list[AgentGraphSpec]:
        return list(agent_registry.graphs.values())

    @app.get(
        "/api/v1/agent-graphs/{graph_id}",
        response_model=AgentGraphSpec,
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def agent_graph(graph_id: str) -> AgentGraphSpec:
        graph = agent_registry.graphs.get(graph_id)
        if graph is None:
            raise HTTPException(status_code=404, detail="agent graph not found")
        return graph

    @app.get(
        "/api/v1/agent-graphs/{graph_id}/schedules",
        response_model=list[GraphRunSchedule],
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def graph_schedules(
        graph_id: str,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[GraphRunSchedule]:
        if graph_id not in agent_registry.graphs:
            raise HTTPException(status_code=404, detail="agent graph not found")
        return repository.list_agent_graph_schedules(
            graph_id=graph_id,
            limit=limit,
        )

    @app.post(
        "/api/v1/agent-graphs/{graph_id}/schedules",
        response_model=GraphRunSchedule,
        tags=["maintenance"],
    )
    async def schedule_agent_graph(
        graph_id: str,
        body: AgentGraphScheduleRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="agent.schedule",
                )
            ),
        ],
    ) -> GraphRunSchedule:
        graph = agent_registry.graphs.get(graph_id)
        if graph is None:
            raise HTTPException(status_code=404, detail="agent graph not found")
        if body.trigger_type not in graph.trigger_types:
            raise HTTPException(
                status_code=409,
                detail="trigger type is not allowed by graph template",
            )
        try:
            governed_input = governed_agent_input(body.input)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        existing = repository.get_agent_graph_schedule_by_idempotency(body.idempotency_key)
        if existing is not None:
            if (
                existing.graph_id != graph_id
                or existing.input != governed_input
                or existing.trigger_type != body.trigger_type
            ):
                raise HTTPException(
                    status_code=409,
                    detail="idempotency key belongs to another schedule input",
                )
            return existing
        try:
            agent_registry.validate_graph_input(graph, governed_input)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        schedule = GraphRunSchedule(
            id=body.id,
            graph_id=graph.id,
            graph_version=graph.version,
            trigger_type=body.trigger_type,
            input=governed_input,
            requested_by=principal.subject,
            idempotency_key=body.idempotency_key,
            budget=graph.budget,
        )
        try:
            repository.save_agent_graph_schedule(schedule)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            request,
            actor=principal,
            action="agent.schedule",
            resource_type="agent-graph-schedule",
            resource_id=schedule.id,
            outcome="success",
            metadata={
                "graphId": graph.id,
                "graphVersion": graph.version,
                "triggerType": schedule.trigger_type,
            },
        )
        return schedule

    @app.get(
        "/api/v1/agent-graphs/{graph_id}/schedules/{schedule_id}",
        response_model=GraphRunSchedule,
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def graph_schedule_detail(
        graph_id: str,
        schedule_id: str,
    ) -> GraphRunSchedule:
        schedule = repository.get_agent_graph_schedule(schedule_id)
        if schedule is None or schedule.graph_id != graph_id:
            raise HTTPException(status_code=404, detail="agent graph schedule not found")
        return schedule

    @app.post(
        "/api/v1/agent-graphs/{graph_id}/schedules/{schedule_id}/replay",
        response_model=PipelineReplayResponse,
        tags=["maintenance"],
    )
    async def replay_graph_schedule(
        graph_id: str,
        schedule_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="agent.schedule.replay",
                )
            ),
        ],
    ) -> PipelineReplayResponse:
        schedule = repository.get_agent_graph_schedule(schedule_id)
        if schedule is None or schedule.graph_id != graph_id:
            raise HTTPException(status_code=404, detail="agent graph schedule not found")
        if schedule.status != "failed":
            raise HTTPException(
                status_code=409,
                detail="only failed agent schedules can be replayed",
            )
        event_id = repository.requeue_outbox_event(
            topic="agent.graph.scheduled",
            aggregate_id=schedule.id,
            payload={
                "scheduleId": schedule.id,
                "graphId": schedule.graph_id,
                "graphVersion": schedule.graph_version,
            },
        )
        audit(
            request,
            actor=principal,
            action="agent.schedule.replay",
            resource_type="agent-graph-schedule",
            resource_id=schedule.id,
            outcome="success",
        )
        return PipelineReplayResponse(
            accepted=True,
            stage="agent-schedule",
            resource_id=schedule.id,
            outbox_event_id=event_id,
        )

    @app.post(
        "/api/v1/agent-graphs/{graph_id}/runs",
        response_model=GraphRun,
        tags=["maintenance"],
    )
    async def run_agent_graph(
        graph_id: str,
        request: AgentGraphRunRequest,
        http_request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.AGENT_RUN,
                    action="agent.run",
                )
            ),
        ],
    ) -> GraphRun:
        graph = agent_registry.graphs.get(graph_id)
        if graph is None:
            raise HTTPException(status_code=404, detail="agent graph not found")
        try:
            governed_input = governed_agent_input(request.model_dump(mode="json", by_alias=True))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            graph_run = graph_runner.run(
                spec=graph,
                run_id=request.run_id,
                input=governed_input,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if graph_run.status == "completed" and graph_run.proposal_ids:
            synchronize_governance()
            for proposal_id in graph_run.proposal_ids:
                if proposal_id in governance.proposals:
                    continue
                proposal_node = next(
                    node
                    for node in graph.nodes
                    if proposal_id in graph_run.nodes[node.id].proposal_ids
                )
                try:
                    agent_registry.validate_proposal(
                        proposal_node,
                        proposal_type="content",
                        risk=request.risk,
                    )
                except ValueError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                proposal_output = graph_run.nodes[proposal_node.id].output
                governed = governance.add_proposal(
                    build_evidence_backed_maintenance_proposal(
                        proposal_id=proposal_id,
                        entity_id=str(proposal_output.get("entityId", request.entity_id)),
                        agent_run_id=request.run_id,
                        field_path=str(
                            proposal_output.get(
                                "fieldPath",
                                request.field_path,
                            )
                        ),
                        proposed_value=proposal_output.get(
                            "proposedValue",
                            request.proposed_value,
                        ),
                        citation_id=str(
                            proposal_output.get(
                                "citationId",
                                request.citation_id,
                            )
                        ),
                        confidence=float(
                            proposal_output.get(
                                "confidence",
                                request.confidence,
                            )
                        ),
                        risk=request.risk,
                        current_value_present=(governed_input.get("currentValuePresent") is True),
                        current_value=governed_input.get("currentValue"),
                        citation=(
                            governed_input.get("citation")
                            if isinstance(
                                governed_input.get("citation"),
                                dict,
                            )
                            else None
                        ),
                        citation_already_present=(
                            governed_input.get("citationAlreadyPresent") is True
                        ),
                    )
                )
                repository.save_governed_proposal(governed)
        audit(
            http_request,
            actor=principal,
            action="agent.run",
            resource_type="agent-graph-run",
            resource_id=graph_run.id,
            outcome="success",
            metadata={
                "graphId": graph_id,
                "status": graph_run.status,
                "proposalCount": len(graph_run.proposal_ids),
            },
        )
        return graph_run

    @app.get(
        "/api/v1/agent-graphs/{graph_id}/runs/{run_id}",
        response_model=GraphRun,
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def agent_graph_run(graph_id: str, run_id: str) -> GraphRun:
        document = repository.get_agent_graph_run(run_id)
        if document is None or document.get("graphId") != graph_id:
            raise HTTPException(status_code=404, detail="agent graph run not found")
        return GraphRun.model_validate(document)

    @app.get(
        "/api/v1/proposals",
        response_model=list[GovernedProposal],
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def proposals() -> list[GovernedProposal]:
        synchronize_governance()
        return governance.list_proposals()

    @app.get(
        "/api/v1/proposals/queue",
        response_model=ProposalQueueResponse,
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="proposal.queue.read",
                )
            )
        ],
    )
    async def proposal_queue(
        status: Annotated[list[ProposalStatus] | None, Query()] = None,
        risk: Annotated[list[ProposalRisk] | None, Query()] = None,
        proposal_type: Annotated[
            list[ProposalType] | None,
            Query(alias="proposalType"),
        ] = None,
        entity_id: Annotated[
            str | None,
            Query(alias="entityId", max_length=160),
        ] = None,
        q: Annotated[str | None, Query(max_length=160)] = None,
        conflicts_only: Annotated[bool, Query(alias="conflictsOnly")] = False,
        include_terminal: Annotated[bool, Query(alias="includeTerminal")] = False,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> ProposalQueueResponse:
        synchronize_governance()
        all_proposals = governance.list_proposals()
        all_conflicts = detect_proposal_conflicts(all_proposals)
        conflict_ids_by_proposal: dict[str, list[str]] = {}
        for conflict in all_conflicts:
            for proposal_id in conflict.proposal_ids:
                conflict_ids_by_proposal.setdefault(proposal_id, []).append(conflict.id)

        default_statuses = {
            "proposed",
            "policy-approved",
            "policy-blocked",
            "human-review",
            "accepted",
        }
        requested_statuses = set(status or [])
        requested_risks = set(risk or [])
        requested_types = set(proposal_type or [])
        normalized_query = q.strip().casefold() if q else ""
        filtered: list[GovernedProposal] = []
        for governed in all_proposals:
            proposal = governed.proposal
            if requested_statuses:
                if proposal.status not in requested_statuses:
                    continue
            elif not include_terminal and proposal.status not in default_statuses:
                continue
            if requested_risks and proposal.risk not in requested_risks:
                continue
            if requested_types and proposal.proposal_type not in requested_types:
                continue
            if entity_id and proposal.entity_id != entity_id:
                continue
            if conflicts_only and proposal.id not in conflict_ids_by_proposal:
                continue
            if normalized_query:
                searchable = " ".join(
                    [
                        proposal.id,
                        proposal.entity_id or "",
                        proposal.proposal_type,
                        proposal.agent_run_id,
                        *(operation.path for operation in proposal.operations),
                    ]
                ).casefold()
                if normalized_query not in searchable:
                    continue
            filtered.append(governed)

        status_priority = {
            "human-review": 0,
            "proposed": 1,
            "policy-blocked": 2,
            "policy-approved": 3,
            "accepted": 4,
            "rejected": 5,
            "superseded": 6,
            "released": 7,
        }
        risk_priority = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        original_order = {
            governed.proposal.id: index for index, governed in enumerate(all_proposals)
        }
        filtered.sort(
            key=lambda governed: (
                -len(
                    conflict_ids_by_proposal.get(
                        governed.proposal.id,
                        [],
                    )
                ),
                status_priority[governed.proposal.status],
                risk_priority[governed.proposal.risk],
                original_order[governed.proposal.id],
                governed.proposal.id,
            )
        )
        total = len(filtered)
        page = filtered[offset : offset + limit]
        page_ids = {governed.proposal.id for governed in page}
        page_conflicts = [
            conflict for conflict in all_conflicts if page_ids.intersection(conflict.proposal_ids)
        ]
        status_counts: dict[str, int] = {}
        for governed in all_proposals:
            proposal_status = governed.proposal.status
            status_counts[proposal_status] = status_counts.get(proposal_status, 0) + 1
        return ProposalQueueResponse(
            items=[
                ProposalQueueItem(
                    governed=governed,
                    conflict_ids=conflict_ids_by_proposal.get(
                        governed.proposal.id,
                        [],
                    ),
                )
                for governed in page
            ],
            conflicts=page_conflicts,
            total=total,
            offset=offset,
            limit=limit,
            has_more=offset + len(page) < total,
            total_conflicts=len(all_conflicts),
            status_counts=status_counts,
        )

    @app.get(
        "/api/v1/proposals/conflicts",
        response_model=list[ProposalConflict],
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="proposal.conflicts.read",
                )
            )
        ],
    )
    async def proposal_conflicts() -> list[ProposalConflict]:
        synchronize_governance()
        return detect_proposal_conflicts(governance.list_proposals())

    @app.post(
        "/api/v1/proposals/conflicts/{conflict_id}/merge",
        response_model=ProposalMergeResponse,
        tags=["maintenance"],
    )
    async def merge_proposal_conflict(
        conflict_id: str,
        body: ProposalMergeRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_REVIEW,
                    action="proposal.conflict.merge",
                )
            ),
        ],
    ) -> ProposalMergeResponse:
        synchronize_governance()
        conflicts = detect_proposal_conflicts(governance.list_proposals())
        conflict = next(
            (item for item in conflicts if item.id == conflict_id),
            None,
        )
        if conflict is None:
            raise HTTPException(
                status_code=404,
                detail="active proposal conflict not found",
            )
        source_proposals = [
            governed_or_404(lambda proposal_id=proposal_id: governance.get_proposal(proposal_id))
            for proposal_id in conflict.proposal_ids
        ]
        if governance.proposals.get(body.proposal_id) is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "proposal-already-exists",
                    "proposalId": body.proposal_id,
                },
            )
        try:
            merge_proposal = build_conflict_merge_proposal(
                conflict,
                source_proposals,
                proposal_id=body.proposal_id,
                agent_run_id=body.agent_run_id,
                resolutions=body.resolutions,
            )
        except ConflictMergeError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        source_backups = {item.proposal.id: item.model_copy(deep=True) for item in source_proposals}
        try:
            governed, superseded = governance.add_merge_proposal(
                merge_proposal,
                source_proposal_ids=conflict.proposal_ids,
            )
            repository.save_governed_proposals(
                [governed, *superseded],
            )
        except GovernanceError as error:
            for proposal_id, backup in source_backups.items():
                governance.proposals[proposal_id] = backup
            governance.proposals.pop(body.proposal_id, None)
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception:
            for proposal_id, backup in source_backups.items():
                governance.proposals[proposal_id] = backup
            governance.proposals.pop(body.proposal_id, None)
            raise
        audit(
            request,
            actor=principal,
            action="proposal.conflict.merge",
            resource_type="proposal-conflict",
            resource_id=conflict.id,
            outcome="success",
            metadata={
                "mergeProposalId": governed.proposal.id,
                "sourceProposalIds": conflict.proposal_ids,
                "resolutions": [
                    resolution.model_dump(mode="json", by_alias=True)
                    for resolution in body.resolutions
                ],
                "comment": body.comment,
            },
        )
        return ProposalMergeResponse(
            proposal=governed,
            superseded_proposals=superseded,
        )

    @app.post(
        "/api/v1/proposals/bulk-evaluations",
        response_model=BulkEvaluationResponse,
        tags=["maintenance"],
    )
    async def bulk_evaluate_proposals(
        body: BulkEvaluationRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_EVALUATE,
                    action="proposal.evaluate.bulk",
                )
            ),
        ],
    ) -> BulkEvaluationResponse:
        proposal_ids = list(dict.fromkeys(body.proposal_ids))
        if len(proposal_ids) != len(body.proposal_ids):
            raise HTTPException(
                status_code=422,
                detail="proposal ids must be unique",
            )
        governed_items = [
            governed_or_404(lambda proposal_id=proposal_id: governance.get_proposal(proposal_id))
            for proposal_id in proposal_ids
        ]
        for governed in governed_items:
            if governed.proposal.status != "proposed":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "proposal-not-awaiting-evaluation",
                        "proposalId": governed.proposal.id,
                        "status": governed.proposal.status,
                    },
                )
        evaluated = [governance.evaluate(proposal_id) for proposal_id in proposal_ids]
        for governed in evaluated:
            repository.save_governed_proposal(governed)
        audit(
            request,
            actor=principal,
            action="proposal.evaluate.bulk",
            resource_type="proposal-batch",
            resource_id=f"batch-{uuid4()}",
            outcome="success",
            metadata={
                "proposalIds": proposal_ids,
                "resultStatuses": {
                    governed.proposal.id: governed.proposal.status for governed in evaluated
                },
            },
        )
        return BulkEvaluationResponse(proposals=evaluated)

    @app.post(
        "/api/v1/proposals/bulk-reviews",
        response_model=BulkReviewResponse,
        tags=["maintenance"],
    )
    async def bulk_review_proposals(
        body: BulkReviewRequest,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_REVIEW,
                    action="proposal.review.bulk",
                )
            ),
        ],
    ) -> BulkReviewResponse:
        proposal_ids = list(dict.fromkeys(body.proposal_ids))
        if len(proposal_ids) != len(body.proposal_ids):
            raise HTTPException(
                status_code=422,
                detail="proposal ids must be unique",
            )
        governed_items = [
            governed_or_404(lambda proposal_id=proposal_id: governance.get_proposal(proposal_id))
            for proposal_id in proposal_ids
        ]
        for governed in governed_items:
            if governed.proposal.status != "human-review" or governed.policy_evaluation is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "proposal-not-awaiting-review",
                        "proposalId": governed.proposal.id,
                    },
                )
            if any(review.reviewer_id == principal.subject for review in governed.reviews):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "reviewer-already-decided",
                        "proposalId": governed.proposal.id,
                    },
                )
        if body.decision == "approve":
            selected = set(proposal_ids)
            blocking = [
                conflict
                for conflict in detect_proposal_conflicts(governance.list_proposals())
                if selected.intersection(conflict.proposal_ids)
            ]
            if blocking:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "proposal-conflict",
                        "conflictIds": [conflict.id for conflict in blocking],
                    },
                )
        reviewed = [
            governance.review(
                proposal_id,
                reviewer_id=principal.subject,
                decision=body.decision,
                comment=body.comment,
            )
            for proposal_id in proposal_ids
        ]
        for governed in reviewed:
            repository.save_governed_proposal(governed)
        audit(
            request,
            actor=principal,
            action="proposal.review.bulk",
            resource_type="proposal-batch",
            resource_id=f"batch-{uuid4()}",
            outcome="success",
            metadata={
                "decision": body.decision,
                "proposalIds": proposal_ids,
            },
        )
        return BulkReviewResponse(proposals=reviewed)

    @app.get(
        "/api/v1/proposals/{proposal_id}",
        response_model=GovernedProposal,
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def proposal_detail(proposal_id: str) -> GovernedProposal:
        return governed_or_404(lambda: governance.get_proposal(proposal_id))

    @app.post(
        "/api/v1/proposals/{proposal_id}/evaluate",
        response_model=GovernedProposal,
        tags=["maintenance"],
    )
    async def evaluate_proposal(
        proposal_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_EVALUATE,
                    action="proposal.evaluate",
                )
            ),
        ],
    ) -> GovernedProposal:
        governed = governed_or_404(lambda: governance.evaluate(proposal_id))
        repository.save_governed_proposal(governed)
        audit(
            request,
            actor=principal,
            action="proposal.evaluate",
            resource_type="proposal",
            resource_id=proposal_id,
            outcome="success",
            metadata={"resultStatus": governed.proposal.status},
        )
        return governed

    @app.post(
        "/api/v1/proposals/{proposal_id}/accept-policy",
        response_model=GovernedProposal,
        tags=["maintenance"],
    )
    async def accept_policy_proposal(
        proposal_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_EVALUATE,
                    action="proposal.accept-policy",
                )
            ),
        ],
    ) -> GovernedProposal:
        synchronize_governance()
        blocking = [
            conflict
            for conflict in detect_proposal_conflicts(governance.list_proposals())
            if proposal_id in conflict.proposal_ids
        ]
        if blocking:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "proposal-conflict",
                    "conflictIds": [conflict.id for conflict in blocking],
                },
            )
        try:
            governed = governance.accept_policy_approved(proposal_id)
        except GovernanceError as error:
            status_code = 404 if "not found" in str(error) else 409
            raise HTTPException(
                status_code=status_code,
                detail=str(error),
            ) from error
        repository.save_governed_proposal(governed)
        audit(
            request,
            actor=principal,
            action="proposal.accept-policy",
            resource_type="proposal",
            resource_id=proposal_id,
            outcome="success",
        )
        return governed

    @app.post(
        "/api/v1/proposals/{proposal_id}/reviews",
        response_model=GovernedProposal,
        tags=["maintenance"],
    )
    async def review_proposal(
        proposal_id: str,
        request: ReviewRequest,
        http_request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.PROPOSAL_REVIEW,
                    action="proposal.review",
                )
            ),
        ],
    ) -> GovernedProposal:
        if request.decision == "approve":
            conflicts = detect_proposal_conflicts(governance.list_proposals())
            blocking = [conflict for conflict in conflicts if proposal_id in conflict.proposal_ids]
            if blocking:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "proposal-conflict",
                        "conflictIds": [conflict.id for conflict in blocking],
                    },
                )
        governed = governed_or_404(
            lambda: governance.review(
                proposal_id,
                reviewer_id=principal.subject,
                decision=request.decision,
                comment=request.comment,
            )
        )
        repository.save_governed_proposal(governed)
        audit(
            http_request,
            actor=principal,
            action="proposal.review",
            resource_type="proposal",
            resource_id=proposal_id,
            outcome="success",
            metadata={
                "decision": request.decision,
                "resultStatus": governed.proposal.status,
            },
        )
        return governed

    @app.get(
        "/api/v1/releases",
        response_model=list[ReleaseManifest],
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def releases() -> list[ReleaseManifest]:
        synchronize_governance()
        return list(governance.releases.values())

    @app.post(
        "/api/v1/releases",
        response_model=ReleaseManifest,
        tags=["maintenance"],
    )
    async def stage_release(
        request: ReleaseRequest,
        http_request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.RELEASE_STAGE,
                    action="release.stage",
                )
            ),
        ],
    ) -> ReleaseManifest:
        try:
            release = ReleaseOrchestrator(
                repository=repository,
                search_backend=search_backend,
            ).stage(
                release_id=request.id,
                proposal_ids=request.proposal_ids,
                data_version=request.data_version,
                schema_versions=request.schema_versions,
                policy_version=governance.policy_version,
                previous_release_id=request.previous_release_id,
                trigger="manual",
                initiated_by=principal.subject,
            )
        except ReleaseOrchestrationError as error:
            raise HTTPException(
                status_code=503 if error.retryable else 409,
                detail=str(error),
            ) from error
        synchronize_governance()
        audit(
            http_request,
            actor=principal,
            action="release.stage",
            resource_type="release",
            resource_id=release.id,
            outcome="success",
            metadata={
                "proposalCount": len(release.proposal_ids),
                "dataVersion": release.data_version,
            },
        )
        return release

    @app.post(
        "/api/v1/releases/{release_id}/publish",
        response_model=ReleaseManifest,
        tags=["maintenance"],
    )
    async def publish_release(
        release_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.RELEASE_PUBLISH,
                    action="release.publish",
                )
            ),
        ],
    ) -> ReleaseManifest:
        try:
            result = ReleaseOrchestrator(
                repository=repository,
                search_backend=search_backend,
            ).publish(release_id)
        except ReleaseOrchestrationError as error:
            if error.phase == "load" and str(error) == "release not found":
                status_code = 404
            else:
                status_code = 503 if error.retryable else 409
            detail = str(error)
            if error.phase == "search-activate":
                detail = (
                    "search index activation failed; release remains "
                    f"publishing and can be retried: {error}"
                )
            elif error.phase == "database-complete":
                detail = (
                    "search index is active but release completion is pending "
                    f"and can be retried: {error}"
                )
            raise HTTPException(
                status_code=status_code,
                detail=detail,
            ) from error
        release = result.release
        synchronize_governance()
        audit(
            request,
            actor=principal,
            action="release.publish",
            resource_type="release",
            resource_id=release.id,
            outcome="success",
            metadata={
                "dataVersion": release.data_version,
                "entityRevisionCount": len(release.entity_revisions_after),
                "searchIndex": release.search_index or "",
            },
        )
        return release

    @app.post(
        "/api/v1/releases/{release_id}/verify",
        response_model=ReleaseManifest,
        tags=["maintenance"],
    )
    async def verify_release(
        release_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.RELEASE_PUBLISH,
                    action="release.verify",
                )
            ),
        ],
        retry_failed: Annotated[
            bool,
            Query(alias="retryFailed"),
        ] = False,
    ) -> ReleaseManifest:
        try:
            result = ReleaseVerifier(
                repository=repository,
                search_backend=search_backend,
            ).verify(
                release_id,
                retry_failed=retry_failed,
            )
        except ReleaseVerificationError as error:
            status_code = (
                404 if str(error) == "release not found" else (503 if error.retryable else 409)
            )
            raise HTTPException(
                status_code=status_code,
                detail=str(error),
            ) from error
        release = result.release
        synchronize_governance()
        audit(
            request,
            actor=principal,
            action="release.verify",
            resource_type="release",
            resource_id=release.id,
            outcome=("failed" if release.verification.status == "failed" else "success"),
            metadata={
                "verificationStatus": release.verification.status,
                "verificationAttempt": (release.verification.attempt),
                "retryFailed": retry_failed,
                "automaticRollbackRecommended": (result.should_rollback),
            },
        )
        return release

    @app.post(
        "/api/v1/releases/{release_id}/rollback",
        response_model=ReleaseManifest,
        tags=["maintenance"],
    )
    async def rollback_release(
        release_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(
                require_permission(
                    Permission.RELEASE_ROLLBACK,
                    action="release.rollback",
                )
            ),
        ],
    ) -> ReleaseManifest:
        try:
            result = ReleaseOrchestrator(
                repository=repository,
                search_backend=search_backend,
            ).rollback(release_id)
        except ReleaseOrchestrationError as error:
            if error.phase == "rollback-load" and str(error) == "release not found":
                status_code = 404
            else:
                status_code = 503 if error.retryable else 409
            detail = str(error)
            if error.phase == "rollback-search-activate":
                detail = (
                    "rollback index activation failed; release remains "
                    f"rolling-back and can be retried: {error}"
                )
            elif error.phase == "rollback-database-complete":
                detail = (
                    "rollback index is active but database completion is "
                    f"pending and can be retried: {error}"
                )
            raise HTTPException(
                status_code=status_code,
                detail=detail,
            ) from error
        release = result.release
        synchronize_governance()
        audit(
            request,
            actor=principal,
            action="release.rollback",
            resource_type="release",
            resource_id=release.id,
            outcome="success",
            metadata={"rollbackSearchIndex": release.rollback_search_index or ""},
        )
        return release

    if settings.enable_hardware_fixture_extension:

        @app.post(
            "/api/v1/extensions/hardware/compatibility-checks",
            response_model=CompatibilityReport,
            tags=["hardware-extension"],
        )
        async def hardware_compatibility(
            request: HardwareCompatibilityRequest,
        ) -> CompatibilityReport:
            return RuleEngine(
                HARDWARE_RULES,
                "hardware-fixture-2026.07.28",
            ).evaluate(
                subjects=request.subjects,
                facts=request.facts,
            )

    def job_state(job_id: str) -> dict[str, object] | None:
        acquisition = repository.get_source_acquisition_job(job_id)
        if acquisition is not None:
            progress = {
                "queued": 0,
                "dispatched": 10,
                "running": 45,
                "completed": 100,
                "failed": 100,
                "canceled": 100,
            }[acquisition.status]
            return {
                "event": acquisition.status,
                "jobId": acquisition.id,
                "jobType": "source-acquisition",
                "status": acquisition.status,
                "progress": progress,
                "snapshotId": acquisition.snapshot_id,
                "error": acquisition.error,
                "updatedAt": acquisition.updated_at.isoformat(),
            }
        schedule = repository.get_agent_graph_schedule(job_id)
        if schedule is not None:
            progress = {
                "queued": 0,
                "dispatched": 10,
                "running": 50,
                "completed": 100,
                "failed": 100,
                "canceled": 100,
            }[schedule.status]
            return {
                "event": schedule.status,
                "jobId": schedule.id,
                "jobType": "agent-graph-schedule",
                "status": schedule.status,
                "progress": progress,
                "runId": schedule.run_id,
                "error": schedule.error,
                "updatedAt": schedule.updated_at.isoformat(),
            }
        run = repository.get_agent_graph_run(job_id)
        if run is not None:
            graph_run = GraphRun.model_validate(run)
            completed_nodes = sum(node.status == "completed" for node in graph_run.nodes.values())
            total_nodes = max(len(graph_run.nodes), 1)
            progress = (
                100
                if graph_run.status in {"completed", "failed"}
                else round(completed_nodes / total_nodes * 100)
            )
            return {
                "event": graph_run.status,
                "jobId": graph_run.id,
                "jobType": "agent-graph-run",
                "status": graph_run.status,
                "progress": progress,
                "completedNodes": completed_nodes,
                "totalNodes": total_nodes,
                "proposalIds": graph_run.proposal_ids,
                "completedAt": (
                    graph_run.completed_at.isoformat() if graph_run.completed_at else None
                ),
            }
        return None

    async def event_stream(
        job_id: str,
        *,
        follow: bool,
        interval_seconds: float,
    ) -> AsyncIterator[str]:
        terminal = {"completed", "failed", "canceled"}
        last_document = ""
        while True:
            state = job_state(job_id)
            if state is None:
                return
            document = json.dumps(
                state,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if document != last_document:
                yield f"event: {state['event']}\ndata: {document}\n\n"
                last_document = document
            if not follow or state["status"] in terminal:
                return
            await asyncio.sleep(interval_seconds)

    @app.get(
        "/api/v1/jobs/{job_id}/events",
        tags=["maintenance"],
        dependencies=[
            Depends(
                require_permission(
                    Permission.MAINTENANCE_READ,
                    action="maintenance.read",
                )
            )
        ],
    )
    async def job_events(
        job_id: str,
        follow: bool = False,
        interval_ms: Annotated[
            int,
            Query(alias="intervalMs", ge=250, le=30_000),
        ] = 1000,
    ) -> StreamingResponse:
        if job_state(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        return StreamingResponse(
            event_stream(
                job_id,
                follow=follow,
                interval_seconds=interval_ms / 1000,
            ),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
            },
        )

    return app


app = create_app()
