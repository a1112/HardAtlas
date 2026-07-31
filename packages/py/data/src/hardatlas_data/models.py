from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonDocument = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class KnowledgeSpaceRow(Base):
    __tablename__ = "knowledge_space"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)


class WorkspaceRow(Base):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)


class SavedCollectionRow(Base):
    __tablename__ = "saved_collection"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("workspace.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SavedCollectionItemRow(Base):
    __tablename__ = "saved_collection_item"

    collection_id: Mapped[str] = mapped_column(
        ForeignKey("saved_collection.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entity.id"),
        primary_key=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("workspace.id"),
        nullable=False,
        index=True,
    )
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthoringDraftRow(Base):
    __tablename__ = "authoring_draft"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("workspace.id"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaxonomyNodeRow(Base):
    __tablename__ = "taxonomy_node"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    space_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_space.id"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)


class SchemaDocumentRow(Base):
    __tablename__ = "schema_document"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchemaActivationRow(Base):
    __tablename__ = "schema_activation"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchemaMigrationRow(Base):
    __tablename__ = "schema_migration"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    schema_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    schema_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    proposal_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DomainPackRow(Base):
    __tablename__ = "domain_pack"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeEntityRow(Base):
    __tablename__ = "knowledge_entity"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    slug: Mapped[str] = mapped_column(String(240), unique=True, nullable=False, index=True)
    entity_type_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    publication_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("entity_revision.id", use_alter=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntityRevisionRow(Base):
    __tablename__ = "entity_revision"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entity.id"),
        nullable=False,
        index=True,
    )
    data_version: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeAnswerRow(Base):
    __tablename__ = "knowledge_answer"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QualityAssessmentRow(Base):
    __tablename__ = "quality_assessment"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entity.id"),
        nullable=False,
        index=True,
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("entity_revision.id"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    profile_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )


class MaintenanceTaskRow(Base):
    __tablename__ = "maintenance_task"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("quality_assessment.id"),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entity.id"),
        nullable=False,
        index=True,
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("entity_revision.id"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    profile_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaintenanceWorkItemRow(Base):
    __tablename__ = "maintenance_work_item"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    maintenance_task_id: Mapped[str] = mapped_column(
        ForeignKey("maintenance_task.id"),
        unique=True,
        nullable=False,
        index=True,
    )
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entity.id"),
        nullable=False,
        index=True,
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("entity_revision.id"),
        nullable=False,
        index=True,
    )
    route: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    assignee_id: Mapped[str | None] = mapped_column(
        String(240),
        nullable=True,
        index=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    document: Mapped[dict[str, Any]] = mapped_column(
        JsonDocument,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AgentRuntimeRow(Base):
    __tablename__ = "agent_runtime"

    id: Mapped[str] = mapped_column(String(240), primary_key=True)
    definition_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    definition_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RelationEdgeRow(Base):
    __tablename__ = "relation_edge"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    relationship_type_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    source_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entity.id"),
        nullable=False,
        index=True,
    )
    target_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entity.id"),
        nullable=False,
        index=True,
    )
    qualifiers: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("entity_revision.id"),
        nullable=False,
    )


class GovernedProposalRow(Base):
    __tablename__ = "governed_proposal"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    entity_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    risk: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentGraphRunRow(Base):
    __tablename__ = "agent_graph_run"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    graph_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentGraphScheduleRow(Base):
    __tablename__ = "agent_graph_schedule"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    graph_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(240),
        unique=True,
        nullable=False,
    )
    requested_by: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReleaseManifestRow(Base):
    __tablename__ = "release_manifest"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    data_version: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    previous_release_id: Mapped[str] = mapped_column(String(160), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboxRow(Base):
    __tablename__ = "knowledge_outbox"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    topic: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class SourceDefinitionRow(Base):
    __tablename__ = "source_definition"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    license_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceSnapshotRow(Base):
    __tablename__ = "source_snapshot"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    source_version: Mapped[str] = mapped_column(String(80), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(320), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceAcquisitionJobRow(Base):
    __tablename__ = "source_acquisition_job"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    source_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(240),
        unique=True,
        nullable=False,
    )
    requested_by: Mapped[str] = mapped_column(String(240), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtractionBatchRow(Base):
    __tablename__ = "extraction_batch"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshot.id"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    parser_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtractionCandidateRow(Base):
    __tablename__ = "extraction_candidate"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_batch.id"),
        nullable=False,
        index=True,
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshot.id"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    entity_type_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    document: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
