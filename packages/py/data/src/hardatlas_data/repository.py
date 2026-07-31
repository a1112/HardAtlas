import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hardatlas_ai import GraphRunSchedule
from hardatlas_domain import (
    AgentRuntime,
    AgentRuntimeState,
    AttributeDefinition,
    AuditEvent,
    AuthoringDraftRecord,
    DomainPackRelease,
    EntityResolutionMatch,
    EntityType,
    ExtractionBatch,
    ExtractionCandidate,
    GovernedProposal,
    KnowledgeAnswer,
    KnowledgeEntity,
    KnowledgeSpace,
    MaintenanceEvidenceLink,
    MaintenanceOutputLink,
    MaintenanceTask,
    MaintenanceWorkItem,
    Principal,
    QualityAssessment,
    Relationship,
    RelationshipType,
    ReleaseManifest,
    ReleaseVerification,
    SavedCollection,
    SavedCollectionItem,
    SchemaMigrationManifest,
    SourceAcquisitionJob,
    SourceDefinition,
    SourceSnapshot,
    TaxonomyNode,
    audit_event_hash,
    build_audit_event,
    validate_published_entity_claims,
    validate_relationship,
)
from sqlalchemy import Engine, case, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    AgentGraphRunRow,
    AgentGraphScheduleRow,
    AgentRuntimeRow,
    AuditEventRow,
    AuthoringDraftRow,
    Base,
    DomainPackRow,
    EntityRevisionRow,
    ExtractionBatchRow,
    ExtractionCandidateRow,
    GovernedProposalRow,
    KnowledgeAnswerRow,
    KnowledgeEntityRow,
    KnowledgeSpaceRow,
    MaintenanceTaskRow,
    MaintenanceWorkItemRow,
    OutboxRow,
    QualityAssessmentRow,
    RelationEdgeRow,
    ReleaseManifestRow,
    SavedCollectionItemRow,
    SavedCollectionRow,
    SchemaActivationRow,
    SchemaDocumentRow,
    SchemaMigrationRow,
    SourceAcquisitionJobRow,
    SourceDefinitionRow,
    SourceSnapshotRow,
    TaxonomyNodeRow,
    WorkspaceRow,
)
from .outbox import OutboxRecord


class ProposalConcurrencyError(ValueError):
    def __init__(
        self,
        proposal_id: str,
        *,
        expected_version: int,
        actual_version: int,
    ) -> None:
        self.proposal_id = proposal_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"proposal {proposal_id} changed concurrently: "
            f"expected version {expected_version}, actual version {actual_version}"
        )


def _proposal_outbox_id(
    topic_suffix: str,
    proposal_id: str,
    proposal_version: int,
) -> str:
    digest = hashlib.sha256(f"{topic_suffix}:{proposal_id}:{proposal_version}".encode()).hexdigest()
    return f"outbox-governance-{topic_suffix}-{digest[:32]}"


def _release_outbox_id(topic_suffix: str, release_id: str) -> str:
    digest = hashlib.sha256(f"{topic_suffix}:{release_id}".encode()).hexdigest()
    return f"outbox-release-{topic_suffix}-{digest[:32]}"


def _agent_schedule_outbox_id(schedule_id: str) -> str:
    digest = hashlib.sha256(f"agent.graph.scheduled:{schedule_id}".encode()).hexdigest()
    return f"outbox-agent-scheduled-{digest[:32]}"


def _maintenance_work_outbox_id(work_item_id: str) -> str:
    digest = hashlib.sha256(f"maintenance.work.requested:{work_item_id}".encode()).hexdigest()
    return f"outbox-maintenance-work-{digest[:32]}"


class KnowledgeRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.sessions = sessionmaker(engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def ready(self) -> bool:
        try:
            with self.sessions() as session:
                return session.scalar(text("SELECT 1")) == 1
        except SQLAlchemyError:
            return False

    @staticmethod
    def _write_governed_proposal(
        session: Session,
        governed: GovernedProposal,
        *,
        require_new: bool = False,
    ) -> int:
        proposal = governed.proposal
        row = session.get(GovernedProposalRow, proposal.id)
        if row is None:
            if governed.version != 0:
                raise ProposalConcurrencyError(
                    proposal.id,
                    expected_version=governed.version,
                    actual_version=0,
                )
            next_version = 1
            persisted = governed.model_copy(update={"version": next_version})
            session.add(
                GovernedProposalRow(
                    id=proposal.id,
                    entity_id=proposal.entity_id,
                    status=proposal.status,
                    risk=proposal.risk,
                    lock_version=next_version,
                    document=persisted.model_dump(mode="json", by_alias=True),
                    updated_at=datetime.now(UTC),
                )
            )
            session.add(
                OutboxRow(
                    id=_proposal_outbox_id(
                        "created",
                        proposal.id,
                        next_version,
                    ),
                    topic="governance.proposal.created",
                    aggregate_id=proposal.id,
                    payload={
                        "proposalId": proposal.id,
                        "proposalVersion": next_version,
                        "status": proposal.status,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )
            if proposal.status == "accepted":
                session.add(
                    OutboxRow(
                        id=_proposal_outbox_id(
                            "accepted",
                            proposal.id,
                            next_version,
                        ),
                        topic="governance.proposal.accepted",
                        aggregate_id=proposal.id,
                        payload={
                            "proposalId": proposal.id,
                            "proposalVersion": next_version,
                            "policyVersion": (
                                governed.policy_evaluation.policy_version
                                if governed.policy_evaluation
                                else ""
                            ),
                        },
                        occurred_at=datetime.now(UTC),
                    )
                )
            return next_version

        if require_new:
            raise ValueError("proposal already exists")
        if governed.version != row.lock_version:
            raise ProposalConcurrencyError(
                proposal.id,
                expected_version=governed.version,
                actual_version=row.lock_version,
            )
        next_version = row.lock_version + 1
        persisted = governed.model_copy(update={"version": next_version})
        previous_status = row.status
        result = session.execute(
            update(GovernedProposalRow)
            .where(
                GovernedProposalRow.id == proposal.id,
                GovernedProposalRow.lock_version == governed.version,
            )
            .values(
                entity_id=proposal.entity_id,
                status=proposal.status,
                risk=proposal.risk,
                lock_version=next_version,
                document=persisted.model_dump(mode="json", by_alias=True),
                updated_at=datetime.now(UTC),
            )
        )
        if result.rowcount != 1:
            actual_version = session.scalar(
                select(GovernedProposalRow.lock_version).where(
                    GovernedProposalRow.id == proposal.id
                )
            )
            raise ProposalConcurrencyError(
                proposal.id,
                expected_version=governed.version,
                actual_version=actual_version or 0,
            )
        if previous_status != "accepted" and proposal.status == "accepted":
            session.add(
                OutboxRow(
                    id=_proposal_outbox_id(
                        "accepted",
                        proposal.id,
                        next_version,
                    ),
                    topic="governance.proposal.accepted",
                    aggregate_id=proposal.id,
                    payload={
                        "proposalId": proposal.id,
                        "proposalVersion": next_version,
                        "policyVersion": (
                            governed.policy_evaluation.policy_version
                            if governed.policy_evaluation
                            else ""
                        ),
                    },
                    occurred_at=datetime.now(UTC),
                )
            )
        return next_version

    @contextmanager
    def _workspace_transaction(self, workspace_id: str) -> Iterator[Session]:
        if not workspace_id:
            raise ValueError("workspace id is required")
        with self.sessions.begin() as session:
            if self.engine.dialect.name == "postgresql":
                session.execute(
                    text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
                    {"workspace_id": workspace_id},
                )
            yield session

    def ensure_workspace(self, workspace_id: str, name: str) -> None:
        if not workspace_id:
            raise ValueError("workspace id is required")
        with self.sessions.begin() as session:
            row = session.get(WorkspaceRow, workspace_id)
            if row is None:
                session.add(WorkspaceRow(id=workspace_id, name=name))

    def create_authoring_draft(self, draft: AuthoringDraftRecord) -> None:
        draft.validate_state()
        if draft.version != 1 or draft.status != "editing":
            raise ValueError("new authoring drafts must start at editing version 1")
        with self._workspace_transaction(draft.workspace_id) as session:
            if session.get(AuthoringDraftRow, draft.id) is not None:
                raise ValueError("authoring draft id already exists")
            session.add(
                AuthoringDraftRow(
                    id=draft.id,
                    workspace_id=draft.workspace_id,
                    created_by=draft.created_by,
                    mode=draft.mode,
                    status=draft.status,
                    entity_id=draft.draft.id,
                    version=draft.version,
                    document=draft.model_dump(mode="json", by_alias=True),
                    created_at=draft.created_at,
                    updated_at=draft.updated_at,
                )
            )

    def get_authoring_draft(
        self,
        workspace_id: str,
        draft_id: str,
    ) -> AuthoringDraftRecord | None:
        with self._workspace_transaction(workspace_id) as session:
            row = session.scalar(
                select(AuthoringDraftRow).where(
                    AuthoringDraftRow.id == draft_id,
                    AuthoringDraftRow.workspace_id == workspace_id,
                )
            )
            return AuthoringDraftRecord.model_validate(row.document) if row else None

    def list_authoring_drafts(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AuthoringDraftRecord]:
        with self._workspace_transaction(workspace_id) as session:
            statement = (
                select(AuthoringDraftRow)
                .where(AuthoringDraftRow.workspace_id == workspace_id)
                .order_by(
                    AuthoringDraftRow.updated_at.desc(),
                    AuthoringDraftRow.id,
                )
                .limit(limit)
            )
            if status is not None:
                statement = statement.where(AuthoringDraftRow.status == status)
            rows = session.scalars(statement).all()
            return [AuthoringDraftRecord.model_validate(row.document) for row in rows]

    def update_authoring_draft(
        self,
        draft: AuthoringDraftRecord,
        *,
        expected_version: int,
    ) -> None:
        draft.validate_state()
        with self._workspace_transaction(draft.workspace_id) as session:
            row = session.scalar(
                select(AuthoringDraftRow).where(
                    AuthoringDraftRow.id == draft.id,
                    AuthoringDraftRow.workspace_id == draft.workspace_id,
                )
            )
            if row is None:
                raise ValueError("authoring draft not found")
            existing = AuthoringDraftRecord.model_validate(row.document)
            if existing.version != expected_version:
                raise ValueError("authoring draft version conflict")
            if draft.version != expected_version + 1:
                raise ValueError("authoring draft version must advance by one")
            immutable_fields = (
                "id",
                "workspace_id",
                "created_by",
                "mode",
                "base_revision_id",
                "created_at",
            )
            if any(getattr(existing, field) != getattr(draft, field) for field in immutable_fields):
                raise ValueError("authoring draft identity is immutable")
            allowed = {
                "editing": {"editing", "submitted", "abandoned"},
                "submitted": {"submitted"},
                "abandoned": {"abandoned"},
            }
            if draft.status not in allowed[existing.status]:
                raise ValueError(
                    f"invalid authoring draft transition: {existing.status} -> {draft.status}"
                )
            row.status = draft.status
            row.entity_id = draft.draft.id
            row.version = draft.version
            row.document = draft.model_dump(mode="json", by_alias=True)
            row.updated_at = draft.updated_at

    def save_collection(self, collection: SavedCollection) -> None:
        with self._workspace_transaction(collection.workspace_id) as session:
            row = session.get(SavedCollectionRow, collection.id)
            document = collection.model_dump(mode="json", by_alias=True)
            if row is None:
                session.add(
                    SavedCollectionRow(
                        id=collection.id,
                        workspace_id=collection.workspace_id,
                        name=collection.name,
                        document=document,
                        created_at=collection.created_at,
                        updated_at=collection.updated_at,
                    )
                )
                return
            if row.workspace_id != collection.workspace_id:
                raise ValueError("collection belongs to another workspace")
            existing = SavedCollection.model_validate(row.document)
            if (
                existing.created_by != collection.created_by
                or existing.created_at != collection.created_at
            ):
                raise ValueError("collection identity is immutable")
            row.name = collection.name
            row.document = document
            row.updated_at = collection.updated_at

    def list_collections(self, workspace_id: str) -> list[SavedCollection]:
        with self._workspace_transaction(workspace_id) as session:
            rows = session.scalars(
                select(SavedCollectionRow)
                .where(SavedCollectionRow.workspace_id == workspace_id)
                .order_by(
                    SavedCollectionRow.updated_at.desc(),
                    SavedCollectionRow.id,
                )
            ).all()
            return [SavedCollection.model_validate(row.document) for row in rows]

    def get_collection(
        self,
        workspace_id: str,
        collection_id: str,
    ) -> SavedCollection | None:
        with self._workspace_transaction(workspace_id) as session:
            row = session.scalar(
                select(SavedCollectionRow).where(
                    SavedCollectionRow.id == collection_id,
                    SavedCollectionRow.workspace_id == workspace_id,
                )
            )
            return SavedCollection.model_validate(row.document) if row else None

    def remove_collection(
        self,
        workspace_id: str,
        collection_id: str,
    ) -> bool:
        with self._workspace_transaction(workspace_id) as session:
            row = session.scalar(
                select(SavedCollectionRow).where(
                    SavedCollectionRow.id == collection_id,
                    SavedCollectionRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                return False
            item_rows = session.scalars(
                select(SavedCollectionItemRow).where(
                    SavedCollectionItemRow.collection_id == collection_id,
                    SavedCollectionItemRow.workspace_id == workspace_id,
                )
            ).all()
            for item_row in item_rows:
                session.delete(item_row)
            session.delete(row)
            return True

    def save_collection_item(self, item: SavedCollectionItem) -> None:
        with self._workspace_transaction(item.workspace_id) as session:
            collection = session.scalar(
                select(SavedCollectionRow).where(
                    SavedCollectionRow.id == item.collection_id,
                    SavedCollectionRow.workspace_id == item.workspace_id,
                )
            )
            if collection is None:
                raise ValueError("collection not found in workspace")
            if session.get(KnowledgeEntityRow, item.entity_id) is None:
                raise ValueError("knowledge entity not found")
            row = session.get(
                SavedCollectionItemRow,
                {
                    "collection_id": item.collection_id,
                    "entity_id": item.entity_id,
                },
            )
            document = item.model_dump(mode="json", by_alias=True)
            if row is None:
                session.add(
                    SavedCollectionItemRow(
                        collection_id=item.collection_id,
                        entity_id=item.entity_id,
                        workspace_id=item.workspace_id,
                        document=document,
                        saved_at=item.saved_at,
                    )
                )
            else:
                if row.workspace_id != item.workspace_id:
                    raise ValueError("collection item belongs to another workspace")
                row.document = document
                row.saved_at = item.saved_at
            collection_document = SavedCollection.model_validate(collection.document).model_copy(
                update={"updated_at": item.saved_at}
            )
            collection.document = collection_document.model_dump(
                mode="json",
                by_alias=True,
            )
            collection.updated_at = item.saved_at

    def list_collection_items(
        self,
        workspace_id: str,
        collection_id: str,
    ) -> list[SavedCollectionItem]:
        with self._workspace_transaction(workspace_id) as session:
            collection = session.scalar(
                select(SavedCollectionRow.id).where(
                    SavedCollectionRow.id == collection_id,
                    SavedCollectionRow.workspace_id == workspace_id,
                )
            )
            if collection is None:
                raise ValueError("collection not found in workspace")
            rows = session.scalars(
                select(SavedCollectionItemRow)
                .where(
                    SavedCollectionItemRow.collection_id == collection_id,
                    SavedCollectionItemRow.workspace_id == workspace_id,
                )
                .order_by(SavedCollectionItemRow.saved_at.desc())
            ).all()
            return [SavedCollectionItem.model_validate(row.document) for row in rows]

    def remove_collection_item(
        self,
        workspace_id: str,
        collection_id: str,
        entity_id: str,
    ) -> bool:
        with self._workspace_transaction(workspace_id) as session:
            row = session.scalar(
                select(SavedCollectionItemRow).where(
                    SavedCollectionItemRow.collection_id == collection_id,
                    SavedCollectionItemRow.entity_id == entity_id,
                    SavedCollectionItemRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                return False
            session.delete(row)
            collection = session.scalar(
                select(SavedCollectionRow).where(
                    SavedCollectionRow.id == collection_id,
                    SavedCollectionRow.workspace_id == workspace_id,
                )
            )
            if collection is not None:
                updated_at = datetime.now(UTC)
                collection_document = SavedCollection.model_validate(
                    collection.document
                ).model_copy(update={"updated_at": updated_at})
                collection.document = collection_document.model_dump(
                    mode="json",
                    by_alias=True,
                )
                collection.updated_at = updated_at
            return True

    def save_space(self, space: KnowledgeSpace) -> None:
        with self.sessions.begin() as session:
            self._save_space_in_session(session, space)

    @staticmethod
    def _save_space_in_session(session: Session, space: KnowledgeSpace) -> None:
        row = session.get(KnowledgeSpaceRow, space.id)
        if row is None:
            row = KnowledgeSpaceRow(
                id=space.id,
                slug=space.slug,
                document=space.model_dump(mode="json", by_alias=True),
                status=space.status,
            )
            session.add(row)
        else:
            row.slug = space.slug
            row.document = space.model_dump(mode="json", by_alias=True)
            row.status = space.status

    def list_spaces(self) -> list[KnowledgeSpace]:
        with self.sessions() as session:
            rows = session.scalars(select(KnowledgeSpaceRow).order_by(KnowledgeSpaceRow.slug)).all()
            return [KnowledgeSpace.model_validate(row.document) for row in rows]

    def save_taxonomy_node(self, node: TaxonomyNode) -> None:
        with self.sessions.begin() as session:
            self._save_taxonomy_node_in_session(session, node)

    @staticmethod
    def _save_taxonomy_node_in_session(
        session: Session,
        node: TaxonomyNode,
    ) -> None:
        row = session.get(TaxonomyNodeRow, node.id)
        document = node.model_dump(mode="json", by_alias=True)
        if row is None:
            session.add(
                TaxonomyNodeRow(
                    id=node.id,
                    space_id=node.space_id,
                    slug=node.slug,
                    document=document,
                )
            )
        else:
            row.space_id = node.space_id
            row.slug = node.slug
            row.document = document

    def list_taxonomy_nodes(self, space_id: str | None = None) -> list[TaxonomyNode]:
        with self.sessions() as session:
            statement = select(TaxonomyNodeRow).order_by(
                TaxonomyNodeRow.space_id,
                TaxonomyNodeRow.slug,
            )
            if space_id is not None:
                statement = statement.where(TaxonomyNodeRow.space_id == space_id)
            rows = session.scalars(statement).all()
            return [TaxonomyNode.model_validate(row.document) for row in rows]

    def save_domain_pack(
        self,
        pack: DomainPackRelease,
        governed: GovernedProposal | None = None,
    ) -> None:
        if pack.status != "draft":
            raise ValueError("new domain pack versions must be saved as draft")
        with self.sessions.begin() as session:
            existing = session.get(
                DomainPackRow,
                {"id": pack.id, "version": pack.version},
            )
            document = pack.model_dump(mode="json", by_alias=True)
            if existing is not None:
                if existing.status != "draft":
                    raise ValueError("published domain pack versions are immutable")
                if existing.document != document:
                    raise ValueError("domain pack versions are immutable; create a new version")
                return
            if governed is None or governed.proposal.id != pack.proposal_id:
                raise ValueError("domain pack requires its governed proposal")
            now = datetime.now(UTC)
            session.add(
                DomainPackRow(
                    id=pack.id,
                    version=pack.version,
                    status=pack.status,
                    document=document,
                    created_at=pack.created_at,
                    updated_at=now,
                )
            )
            next_proposal_version = self._write_governed_proposal(
                session,
                governed,
                require_new=True,
            )
        governed.version = next_proposal_version

    def get_domain_pack(
        self,
        pack_id: str,
        version: str,
    ) -> DomainPackRelease | None:
        with self.sessions() as session:
            row = session.get(
                DomainPackRow,
                {"id": pack_id, "version": version},
            )
            return DomainPackRelease.model_validate(row.document) if row is not None else None

    def list_domain_packs(self) -> list[DomainPackRelease]:
        with self.sessions() as session:
            rows = session.scalars(
                select(DomainPackRow).order_by(
                    DomainPackRow.id,
                    DomainPackRow.created_at.desc(),
                )
            ).all()
            return [DomainPackRelease.model_validate(row.document) for row in rows]

    def schema_activation_versions(
        self,
        references: list[tuple[str, str]],
    ) -> dict[str, str | None]:
        with self.sessions() as session:
            return {
                f"{kind}|{document_id}": (activation.schema_version if activation else None)
                for kind, document_id in references
                for activation in [
                    session.get(
                        SchemaActivationRow,
                        {"id": document_id, "kind": kind},
                    )
                ]
            }

    def publish_domain_pack(
        self,
        pack: DomainPackRelease,
        governed: GovernedProposal,
    ) -> DomainPackRelease:
        if pack.status != "published" or pack.published_at is None:
            raise ValueError("domain pack must be marked published")
        if governed.proposal.id != pack.proposal_id or governed.proposal.status != "released":
            raise ValueError("domain pack requires a released governed proposal")
        if pack.rollback_snapshot is None:
            raise ValueError("domain pack publication requires a rollback snapshot")
        with self.sessions.begin() as session:
            row = session.get(
                DomainPackRow,
                {"id": pack.id, "version": pack.version},
            )
            if row is None:
                raise ValueError("domain pack draft does not exist")
            existing = DomainPackRelease.model_validate(row.document)
            if existing.status == "published":
                return existing
            if existing.status != "draft":
                raise ValueError("only draft domain packs can be published")

            snapshot = pack.rollback_snapshot
            for space in pack.spaces:
                if space.id not in snapshot.spaces:
                    raise ValueError("domain pack rollback snapshot is incomplete")
                current_space = session.get(KnowledgeSpaceRow, space.id)
                previous_space = snapshot.spaces[space.id]
                if (current_space is None and previous_space is not None) or (
                    current_space is not None
                    and (
                        previous_space is None
                        or current_space.document
                        != previous_space.model_dump(
                            mode="json",
                            by_alias=True,
                        )
                    )
                ):
                    raise ValueError(f"knowledge space changed before publication: {space.id}")
                self._save_space_in_session(session, space)
            for node in pack.taxonomy_nodes:
                if node.id not in snapshot.taxonomy_nodes:
                    raise ValueError("domain pack rollback snapshot is incomplete")
                current_node = session.get(TaxonomyNodeRow, node.id)
                previous_node = snapshot.taxonomy_nodes[node.id]
                if (current_node is None and previous_node is not None) or (
                    current_node is not None
                    and (
                        previous_node is None
                        or current_node.document
                        != previous_node.model_dump(
                            mode="json",
                            by_alias=True,
                        )
                    )
                ):
                    raise ValueError(f"taxonomy changed before publication: {node.id}")
                self._save_taxonomy_node_in_session(session, node)
            schema_documents = (
                (
                    "attribute-definition",
                    pack.attributes,
                ),
                (
                    "relationship-type",
                    pack.relationship_types,
                ),
                ("view-definition", pack.views),
                ("entity-type", pack.entity_types),
                ("quality-profile", pack.quality_profiles),
            )
            for kind, documents in schema_documents:
                for document in documents:
                    key = f"{kind}|{document.id}"
                    if key not in snapshot.schema_activations:
                        raise ValueError("domain pack rollback snapshot is incomplete")
                    activation = session.get(
                        SchemaActivationRow,
                        {"id": document.id, "kind": kind},
                    )
                    current_version = activation.schema_version if activation else None
                    if current_version != snapshot.schema_activations[key]:
                        raise ValueError(
                            f"schema activation changed before publication: {document.id}"
                        )
                    self._save_schema_document_in_session(
                        session,
                        document_id=document.id,
                        schema_version=document.schema_version,
                        kind=kind,
                        document=document.model_dump(mode="json", by_alias=True),
                        activate=True,
                    )

            for previous in session.scalars(
                select(DomainPackRow).where(
                    DomainPackRow.id == pack.id,
                    DomainPackRow.status == "published",
                )
            ).all():
                archived = DomainPackRelease.model_validate(previous.document).model_copy(
                    update={"status": "archived"}
                )
                previous.status = "archived"
                previous.document = archived.model_dump(
                    mode="json",
                    by_alias=True,
                )
                previous.updated_at = datetime.now(UTC)

            row.status = "published"
            row.document = pack.model_dump(mode="json", by_alias=True)
            row.updated_at = datetime.now(UTC)
            proposal_row = session.get(
                GovernedProposalRow,
                pack.proposal_id,
            )
            if proposal_row is None:
                raise ValueError("domain pack proposal does not exist")
            current_proposal = GovernedProposal.model_validate(proposal_row.document)
            if current_proposal.proposal.status != "accepted":
                raise ValueError("domain pack proposal is not accepted")
            next_proposal_version = self._write_governed_proposal(
                session,
                governed,
            )
            session.add(
                OutboxRow(
                    id=f"outbox-domain-pack-{pack.id}-{pack.version}",
                    topic="domain-pack.published",
                    aggregate_id=pack.id,
                    payload={
                        "domainPackId": pack.id,
                        "version": pack.version,
                        "counts": {
                            "spaces": len(pack.spaces),
                            "taxonomyNodes": len(pack.taxonomy_nodes),
                            "entityTypes": len(pack.entity_types),
                            "attributes": len(pack.attributes),
                            "relationshipTypes": len(pack.relationship_types),
                            "views": len(pack.views),
                        },
                    },
                    occurred_at=pack.published_at,
                    published_at=None,
                    error=None,
                )
            )
        governed.version = next_proposal_version
        return pack

    def rollback_domain_pack(
        self,
        pack: DomainPackRelease,
        restore_pack: DomainPackRelease | None,
    ) -> tuple[DomainPackRelease, DomainPackRelease | None]:
        if pack.status != "rolled-back" or pack.rolled_back_at is None:
            raise ValueError("domain pack must be marked rolled-back")
        snapshot = pack.rollback_snapshot
        if snapshot is None:
            raise ValueError("domain pack publication has no rollback snapshot")
        with self.sessions.begin() as session:
            row = session.get(
                DomainPackRow,
                {"id": pack.id, "version": pack.version},
            )
            if row is None:
                raise ValueError("domain pack version does not exist")
            current = DomainPackRelease.model_validate(row.document)
            if current.status == "rolled-back":
                return current, restore_pack
            if current.status != "published":
                raise ValueError("only the published domain pack can be rolled back")

            schema_documents = (
                ("attribute-definition", pack.attributes),
                ("relationship-type", pack.relationship_types),
                ("view-definition", pack.views),
                ("entity-type", pack.entity_types),
                ("quality-profile", pack.quality_profiles),
            )
            for kind, documents in schema_documents:
                for document in documents:
                    key = f"{kind}|{document.id}"
                    if key not in snapshot.schema_activations:
                        raise ValueError("rollback snapshot is incomplete")
                    activation = session.get(
                        SchemaActivationRow,
                        {"id": document.id, "kind": kind},
                    )
                    if activation is None or activation.schema_version != document.schema_version:
                        raise ValueError(
                            f"schema activation changed after publication: {document.id}"
                        )
                    previous_version = snapshot.schema_activations[key]
                    if previous_version is None:
                        session.delete(activation)
                    else:
                        previous_document = session.get(
                            SchemaDocumentRow,
                            {
                                "id": document.id,
                                "schema_version": previous_version,
                            },
                        )
                        if previous_document is None or previous_document.kind != kind:
                            raise ValueError(f"rollback schema version is missing: {document.id}")
                        activation.schema_version = previous_version
                        activation.updated_at = pack.rolled_back_at

            for node in pack.taxonomy_nodes:
                current_node = session.get(TaxonomyNodeRow, node.id)
                if current_node is None or current_node.document != node.model_dump(
                    mode="json", by_alias=True
                ):
                    raise ValueError(f"taxonomy changed after publication: {node.id}")
                previous_node = snapshot.taxonomy_nodes.get(node.id)
                if previous_node is None:
                    session.delete(current_node)
                else:
                    self._save_taxonomy_node_in_session(
                        session,
                        previous_node,
                    )

            for space in pack.spaces:
                current_space = session.get(KnowledgeSpaceRow, space.id)
                if current_space is None or current_space.document != space.model_dump(
                    mode="json", by_alias=True
                ):
                    raise ValueError(f"knowledge space changed after publication: {space.id}")
                previous_space = snapshot.spaces.get(space.id)
                if previous_space is None:
                    session.delete(current_space)
                else:
                    self._save_space_in_session(session, previous_space)

            restored: DomainPackRelease | None = None
            if restore_pack is not None:
                restore_row = session.get(
                    DomainPackRow,
                    {"id": restore_pack.id, "version": restore_pack.version},
                )
                if restore_row is None:
                    raise ValueError("rollback target domain pack does not exist")
                existing_restore = DomainPackRelease.model_validate(restore_row.document)
                if existing_restore.status != "archived":
                    raise ValueError("rollback target domain pack is not archived")
                restored = existing_restore.model_copy(update={"status": "published"})
                restore_row.status = "published"
                restore_row.document = restored.model_dump(
                    mode="json",
                    by_alias=True,
                )
                restore_row.updated_at = pack.rolled_back_at

            row.status = "rolled-back"
            row.document = pack.model_dump(mode="json", by_alias=True)
            row.updated_at = pack.rolled_back_at
            session.add(
                OutboxRow(
                    id=f"outbox-domain-pack-rollback-{pack.id}-{pack.version}",
                    topic="domain-pack.rolled-back",
                    aggregate_id=pack.id,
                    payload={
                        "domainPackId": pack.id,
                        "version": pack.version,
                        "restoredVersion": (restored.version if restored is not None else None),
                    },
                    occurred_at=pack.rolled_back_at,
                    published_at=None,
                    error=None,
                )
            )
        return pack, restored

    def save_schema_document(
        self,
        *,
        document_id: str,
        schema_version: str,
        kind: str,
        document: dict[str, object],
        activate: bool = True,
        preserve_activation: bool = False,
    ) -> None:
        with self.sessions.begin() as session:
            self._save_schema_document_in_session(
                session,
                document_id=document_id,
                schema_version=schema_version,
                kind=kind,
                document=document,
                activate=activate,
                preserve_activation=preserve_activation,
            )

    @staticmethod
    def _save_schema_document_in_session(
        session: Session,
        *,
        document_id: str,
        schema_version: str,
        kind: str,
        document: dict[str, object],
        activate: bool,
        preserve_activation: bool = False,
    ) -> None:
        existing = session.scalar(
            select(SchemaDocumentRow).where(
                SchemaDocumentRow.id == document_id,
                SchemaDocumentRow.schema_version == schema_version,
            )
        )
        if existing is not None and (existing.document != document or existing.kind != kind):
            raise ValueError("published schema documents are immutable")
        if existing is None:
            session.add(
                SchemaDocumentRow(
                    id=document_id,
                    schema_version=schema_version,
                    kind=kind,
                    document=document,
                    created_at=datetime.now(UTC),
                )
            )
        if activate:
            activation = session.get(
                SchemaActivationRow,
                {"id": document_id, "kind": kind},
            )
            if activation is None:
                session.add(
                    SchemaActivationRow(
                        id=document_id,
                        kind=kind,
                        schema_version=schema_version,
                        updated_at=datetime.now(UTC),
                    )
                )
            elif not preserve_activation:
                activation.schema_version = schema_version
                activation.updated_at = datetime.now(UTC)

    @staticmethod
    def _active_schema_row(
        session: Session,
        *,
        document_id: str,
        kind: str,
    ) -> SchemaDocumentRow | None:
        activation = session.get(
            SchemaActivationRow,
            {"id": document_id, "kind": kind},
        )
        if activation is None:
            return session.scalar(
                select(SchemaDocumentRow)
                .where(
                    SchemaDocumentRow.id == document_id,
                    SchemaDocumentRow.kind == kind,
                )
                .order_by(SchemaDocumentRow.created_at.desc())
            )
        return session.get(
            SchemaDocumentRow,
            {
                "id": document_id,
                "schema_version": activation.schema_version,
            },
        )

    def get_schema_document(
        self,
        *,
        document_id: str,
        kind: str,
        schema_version: str | None = None,
    ) -> dict[str, object] | None:
        with self.sessions() as session:
            if schema_version is not None:
                row = session.get(
                    SchemaDocumentRow,
                    {"id": document_id, "schema_version": schema_version},
                )
                if row is not None and row.kind != kind:
                    row = None
            else:
                row = self._active_schema_row(
                    session,
                    document_id=document_id,
                    kind=kind,
                )
            return dict(row.document) if row else None

    def list_schema_documents(self, *, kind: str) -> list[dict[str, object]]:
        with self.sessions() as session:
            activations = session.scalars(
                select(SchemaActivationRow)
                .where(SchemaActivationRow.kind == kind)
                .order_by(SchemaActivationRow.id)
            ).all()
            if activations:
                return [
                    dict(row.document)
                    for activation in activations
                    if (
                        row := session.get(
                            SchemaDocumentRow,
                            {
                                "id": activation.id,
                                "schema_version": activation.schema_version,
                            },
                        )
                    )
                    is not None
                ]
            rows = session.scalars(
                select(SchemaDocumentRow)
                .where(SchemaDocumentRow.kind == kind)
                .order_by(
                    SchemaDocumentRow.id,
                    SchemaDocumentRow.created_at.desc(),
                )
            ).all()
            latest: dict[str, dict[str, object]] = {}
            for row in rows:
                latest.setdefault(row.id, dict(row.document))
            return list(latest.values())

    def save_entity(self, entity: KnowledgeEntity) -> None:
        with self.sessions.begin() as session:
            self._save_entity_in_session(session, entity)

    def _save_entity_in_session(
        self,
        session: Session,
        entity: KnowledgeEntity,
        *,
        outbox_metadata: dict[str, object] | None = None,
    ) -> None:
        entity_type_row = self._active_schema_row(
            session,
            document_id=entity.ref.type_id,
            kind="entity-type",
        )
        if entity_type_row is not None:
            entity_type = EntityType.model_validate(entity_type_row.document)
            attributes = []
            for attribute_id in entity_type.attribute_definition_ids:
                attribute_row = self._active_schema_row(
                    session,
                    document_id=attribute_id,
                    kind="attribute-definition",
                )
                if attribute_row is None:
                    raise ValueError(f"entity type references inactive attribute: {attribute_id}")
                attributes.append(AttributeDefinition.model_validate(attribute_row.document))
            claim_issues = validate_published_entity_claims(
                entity,
                entity_type,
                attributes,
            )
            if claim_issues:
                raise ValueError(
                    "entity claim validation failed: "
                    + "; ".join(f"{issue.code} ({issue.path})" for issue in claim_issues)
                )
        row = session.get(KnowledgeEntityRow, entity.ref.id)
        if row is None:
            row = KnowledgeEntityRow(
                id=entity.ref.id,
                slug=entity.ref.slug,
                entity_type_id=entity.ref.type_id,
                publication_status=entity.publication_status,
                created_at=entity.revision.created_at,
            )
            session.add(row)
            session.flush()

        revision = session.get(EntityRevisionRow, entity.revision.revision_id)
        document = entity.model_dump(mode="json", by_alias=True)
        if revision is None:
            revision = EntityRevisionRow(
                id=entity.revision.revision_id,
                entity_id=entity.ref.id,
                data_version=entity.revision.data_version,
                schema_version=entity.revision.schema_version,
                policy_version=entity.revision.policy_version,
                document=document,
                created_at=entity.revision.created_at,
            )
            session.add(revision)
            session.flush()
        elif revision.document != document:
            raise ValueError("entity revisions are immutable")

        revision_changed = row.current_revision_id != revision.id
        row.slug = entity.ref.slug
        row.entity_type_id = entity.ref.type_id
        row.publication_status = entity.publication_status
        row.current_revision_id = revision.id
        self._sync_relation_edges(session, entity)
        if revision_changed:
            session.add(
                OutboxRow(
                    id=str(uuid4()),
                    topic="knowledge.entity.revised",
                    aggregate_id=entity.ref.id,
                    payload={
                        "entityId": entity.ref.id,
                        "revisionId": revision.id,
                        "dataVersion": revision.data_version,
                        **(outbox_metadata or {}),
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

    @staticmethod
    def _sync_relation_edges(
        session: Session,
        entity: KnowledgeEntity,
    ) -> None:
        relationships = {item.id: item for item in entity.relationships}
        if len(relationships) != len(entity.relationships):
            raise ValueError("relationship ids must be unique within an entity")
        existing_rows = session.scalars(
            select(RelationEdgeRow).where(RelationEdgeRow.source_entity_id == entity.ref.id)
        ).all()
        existing_by_id = {row.id: row for row in existing_rows}
        source_type_row = KnowledgeRepository._active_schema_row(
            session,
            document_id=entity.ref.type_id,
            kind="entity-type",
        )
        if relationships and source_type_row is None:
            raise ValueError("relationship source entity type is not registered")
        source_entity_type = (
            EntityType.model_validate(source_type_row.document) if source_type_row else None
        )
        known_citation_ids = {citation.id for citation in entity.citations}
        relationship_ids = list(relationships)
        replaced_edge_ids = [row.id for row in existing_rows]
        seen_outgoing: dict[str, int] = {}
        seen_incoming: dict[tuple[str, str], int] = {}
        for relationship in relationships.values():
            if relationship.source.id != entity.ref.id:
                raise ValueError("relationship source does not match owning entity")
            if relationship.revision_id != entity.revision.revision_id:
                raise ValueError("relationship revision does not match entity revision")
            target = session.get(KnowledgeEntityRow, relationship.target.id)
            if target is None:
                raise ValueError("relationship target entity does not exist")
            if target.entity_type_id != relationship.target.type_id:
                raise ValueError("relationship target type does not match target entity")
            type_row = KnowledgeRepository._active_schema_row(
                session,
                document_id=relationship.type_id,
                kind="relationship-type",
            )
            if type_row is None:
                raise ValueError(f"relationship type is not registered: {relationship.type_id}")
            relationship_type = RelationshipType.model_validate(type_row.document)
            existing_outgoing = 0
            existing_incoming = int(
                session.scalar(
                    select(func.count())
                    .select_from(RelationEdgeRow)
                    .where(
                        RelationEdgeRow.target_entity_id == relationship.target.id,
                        RelationEdgeRow.relationship_type_id == relationship.type_id,
                        ~RelationEdgeRow.id.in_(set(relationship_ids) | set(replaced_edge_ids)),
                    )
                )
                or 0
            )
            validation = validate_relationship(
                relationship,
                relationship_type,
                source_entity_type,
                known_citation_ids=known_citation_ids,
                outgoing_count=(existing_outgoing + seen_outgoing.get(relationship.type_id, 0)),
                incoming_count=(
                    existing_incoming
                    + seen_incoming.get(
                        (relationship.type_id, relationship.target.id),
                        0,
                    )
                ),
                target_entity_type_id=target.entity_type_id,
            )
            if not validation.valid:
                raise ValueError(
                    "relationship validation failed: "
                    + "; ".join(f"{issue.code} ({issue.path})" for issue in validation.issues)
                )
            seen_outgoing[relationship.type_id] = seen_outgoing.get(relationship.type_id, 0) + 1
            incoming_key = (relationship.type_id, relationship.target.id)
            seen_incoming[incoming_key] = seen_incoming.get(incoming_key, 0) + 1
            edge = existing_by_id.pop(relationship.id, None)
            if edge is None:
                edge = session.get(RelationEdgeRow, relationship.id)
            if edge is None:
                edge = RelationEdgeRow(id=relationship.id)
                session.add(edge)
            elif edge.source_entity_id != entity.ref.id:
                raise ValueError("relationship id belongs to another source entity")
            edge.relationship_type_id = relationship.type_id
            edge.source_entity_id = relationship.source.id
            edge.target_entity_id = relationship.target.id
            edge.qualifiers = relationship.qualifiers
            edge.confidence = relationship.confidence
            edge.revision_id = relationship.revision_id
        for stale in existing_by_id.values():
            session.delete(stale)

    def get_entity(self, slug: str) -> KnowledgeEntity | None:
        with self.sessions() as session:
            row = session.scalar(select(KnowledgeEntityRow).where(KnowledgeEntityRow.slug == slug))
            if row is None or row.current_revision_id is None:
                return None
            revision = session.get(EntityRevisionRow, row.current_revision_id)
            if revision is None:
                return None
            return KnowledgeEntity.model_validate(revision.document)

    def get_entity_by_id(self, entity_id: str) -> KnowledgeEntity | None:
        with self.sessions() as session:
            row = session.get(KnowledgeEntityRow, entity_id)
            if row is None or row.current_revision_id is None:
                return None
            revision = session.get(EntityRevisionRow, row.current_revision_id)
            return KnowledgeEntity.model_validate(revision.document) if revision else None

    def get_entity_revision(
        self,
        entity_key: str,
        revision_id: str,
    ) -> KnowledgeEntity | None:
        with self.sessions() as session:
            entity = session.get(KnowledgeEntityRow, entity_key)
            if entity is None:
                entity = session.scalar(
                    select(KnowledgeEntityRow).where(KnowledgeEntityRow.slug == entity_key)
                )
            if entity is None:
                return None
            revision = session.get(EntityRevisionRow, revision_id)
            if revision is None or revision.entity_id != entity.id:
                return None
            return KnowledgeEntity.model_validate(revision.document)

    def list_entity_revisions(
        self,
        entity_key: str,
        *,
        limit: int = 100,
    ) -> list[KnowledgeEntity]:
        with self.sessions() as session:
            entity = session.get(KnowledgeEntityRow, entity_key)
            if entity is None:
                entity = session.scalar(
                    select(KnowledgeEntityRow).where(KnowledgeEntityRow.slug == entity_key)
                )
            if entity is None:
                return []
            revisions = session.scalars(
                select(EntityRevisionRow)
                .where(EntityRevisionRow.entity_id == entity.id)
                .order_by(
                    EntityRevisionRow.created_at.desc(),
                    EntityRevisionRow.id.desc(),
                )
                .limit(limit)
            ).all()
            return [KnowledgeEntity.model_validate(revision.document) for revision in revisions]

    def list_relationships(
        self,
        entity_id: str,
        *,
        direction: str = "both",
        relationship_type_id: str | None = None,
        limit: int = 100,
    ) -> list[Relationship]:
        if direction not in {"outgoing", "incoming", "both"}:
            raise ValueError("relationship direction is invalid")
        with self.sessions() as session:
            statement = select(RelationEdgeRow)
            if direction == "outgoing":
                statement = statement.where(RelationEdgeRow.source_entity_id == entity_id)
            elif direction == "incoming":
                statement = statement.where(RelationEdgeRow.target_entity_id == entity_id)
            else:
                statement = statement.where(
                    (RelationEdgeRow.source_entity_id == entity_id)
                    | (RelationEdgeRow.target_entity_id == entity_id)
                )
            if relationship_type_id:
                statement = statement.where(
                    RelationEdgeRow.relationship_type_id == relationship_type_id
                )
            rows = session.scalars(
                statement.order_by(
                    RelationEdgeRow.relationship_type_id,
                    RelationEdgeRow.id,
                ).limit(limit)
            ).all()
            relationships: list[Relationship] = []
            for edge in rows:
                source = session.get(KnowledgeEntityRow, edge.source_entity_id)
                if source is None or source.current_revision_id != edge.revision_id:
                    continue
                revision = session.get(EntityRevisionRow, edge.revision_id)
                if revision is None:
                    continue
                entity = KnowledgeEntity.model_validate(revision.document)
                relationship = next(
                    (item for item in entity.relationships if item.id == edge.id),
                    None,
                )
                if relationship:
                    relationships.append(relationship)
            return relationships

    def save_knowledge_answer(self, answer: KnowledgeAnswer) -> None:
        normalized_question = " ".join(answer.question.casefold().split())
        question_hash = hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()
        with self.sessions.begin() as session:
            if session.get(KnowledgeAnswerRow, answer.id) is not None:
                raise ValueError(f"knowledge answer already exists: {answer.id}")
            session.add(
                KnowledgeAnswerRow(
                    id=answer.id,
                    question_hash=question_hash,
                    status=answer.status,
                    mode=answer.mode,
                    document=answer.model_dump(mode="json", by_alias=True),
                    created_at=answer.created_at,
                )
            )

    def get_knowledge_answer(self, answer_id: str) -> KnowledgeAnswer | None:
        with self.sessions() as session:
            row = session.get(KnowledgeAnswerRow, answer_id)
            return KnowledgeAnswer.model_validate(row.document) if row is not None else None

    def reconcile_quality_assessment(
        self,
        assessment: QualityAssessment,
        tasks: list[MaintenanceTask],
    ) -> tuple[QualityAssessment, list[MaintenanceTask]]:
        """Atomically persist one assessment and its current task set."""

        desired_tasks = {task.id: task for task in tasks}
        if len(desired_tasks) != len(tasks):
            raise ValueError("maintenance task ids must be unique")
        if any(task.assessment_id != assessment.id for task in tasks):
            raise ValueError("maintenance task belongs to another assessment")
        with self.sessions.begin() as session:
            entity = session.get(KnowledgeEntityRow, assessment.entity.id)
            if entity is None:
                raise ValueError(
                    f"quality assessment entity does not exist: {assessment.entity.id}"
                )
            if entity.current_revision_id != assessment.revision_id:
                raise ValueError("quality assessment must target the current entity revision")
            revision = session.get(EntityRevisionRow, assessment.revision_id)
            if revision is None or revision.entity_id != assessment.entity.id:
                raise ValueError("quality assessment revision does not exist")

            assessment_row = session.get(QualityAssessmentRow, assessment.id)
            assessment_document = assessment.model_dump(
                mode="json",
                by_alias=True,
            )
            if assessment_row is None:
                assessment_row = QualityAssessmentRow(
                    id=assessment.id,
                    entity_id=assessment.entity.id,
                    revision_id=assessment.revision_id,
                    profile_id=assessment.profile_id,
                    profile_version=assessment.profile_version,
                    status=assessment.status,
                    score=assessment.score,
                    document=assessment_document,
                    assessed_at=assessment.assessed_at,
                )
                session.add(assessment_row)
                session.flush()
            else:
                assessment_row.status = assessment.status
                assessment_row.score = assessment.score
                assessment_row.document = assessment_document
                assessment_row.assessed_at = assessment.assessed_at

            active_rows = session.scalars(
                select(MaintenanceTaskRow).where(
                    MaintenanceTaskRow.entity_id == assessment.entity.id,
                    MaintenanceTaskRow.profile_id == assessment.profile_id,
                    MaintenanceTaskRow.status.in_(["open", "scheduled"]),
                )
            ).all()
            persisted: dict[str, MaintenanceTask] = {}
            for row in active_rows:
                desired = desired_tasks.get(row.id)
                if desired is not None:
                    current = MaintenanceTask.model_validate(row.document)
                    desired = desired.model_copy(
                        update={
                            "status": current.status,
                            "agent_graph_schedule_id": (current.agent_graph_schedule_id),
                            "created_at": current.created_at,
                            "updated_at": assessment.assessed_at,
                        }
                    )
                    self._write_maintenance_task_row(row, desired)
                    persisted[desired.id] = desired
                    continue
                current = MaintenanceTask.model_validate(row.document)
                terminal_status = (
                    "superseded"
                    if (
                        row.revision_id != assessment.revision_id
                        or row.profile_version != assessment.profile_version
                    )
                    else "resolved"
                )
                terminal = current.model_copy(
                    update={
                        "status": terminal_status,
                        "updated_at": assessment.assessed_at,
                        "resolved_at": assessment.assessed_at,
                    }
                )
                self._write_maintenance_task_row(row, terminal)

            for task in tasks:
                if task.id in persisted:
                    continue
                row = session.get(MaintenanceTaskRow, task.id)
                if row is None:
                    row = MaintenanceTaskRow(
                        id=task.id,
                        assessment_id=task.assessment_id,
                        entity_id=task.entity.id,
                        revision_id=task.revision_id,
                        profile_id=task.profile_id,
                        profile_version=task.profile_version,
                        status=task.status,
                        priority=task.priority,
                        action=task.action,
                        document=task.model_dump(mode="json", by_alias=True),
                        created_at=task.created_at,
                        updated_at=task.updated_at,
                        resolved_at=task.resolved_at,
                    )
                    session.add(row)
                    session.add(
                        OutboxRow(
                            id=f"outbox-{task.id}",
                            topic="quality.maintenance.requested",
                            aggregate_id=task.entity.id,
                            payload={
                                "taskId": task.id,
                                "assessmentId": assessment.id,
                                "entityId": task.entity.id,
                                "action": task.action,
                                "priority": task.priority,
                            },
                            occurred_at=task.created_at,
                        )
                    )
                else:
                    self._write_maintenance_task_row(row, task)
                persisted[task.id] = task
            return assessment, [persisted[task.id] for task in tasks]

    @staticmethod
    def _write_maintenance_task_row(
        row: MaintenanceTaskRow,
        task: MaintenanceTask,
    ) -> None:
        row.assessment_id = task.assessment_id
        row.entity_id = task.entity.id
        row.revision_id = task.revision_id
        row.profile_id = task.profile_id
        row.profile_version = task.profile_version
        row.status = task.status
        row.priority = task.priority
        row.action = task.action
        row.document = task.model_dump(mode="json", by_alias=True)
        row.created_at = task.created_at
        row.updated_at = task.updated_at
        row.resolved_at = task.resolved_at

    def list_quality_assessments(
        self,
        *,
        entity_id: str | None = None,
        status: str | None = None,
        current_only: bool = True,
        limit: int = 500,
    ) -> list[QualityAssessment]:
        with self.sessions() as session:
            statement = select(QualityAssessmentRow)
            if current_only:
                statement = statement.join(
                    KnowledgeEntityRow,
                    KnowledgeEntityRow.id == QualityAssessmentRow.entity_id,
                ).where(QualityAssessmentRow.revision_id == KnowledgeEntityRow.current_revision_id)
            if entity_id is not None:
                statement = statement.where(QualityAssessmentRow.entity_id == entity_id)
            if status is not None:
                statement = statement.where(QualityAssessmentRow.status == status)
            rows = session.scalars(
                statement.order_by(
                    QualityAssessmentRow.assessed_at.desc(),
                    QualityAssessmentRow.id,
                ).limit(limit)
            ).all()
            assessments = [QualityAssessment.model_validate(row.document) for row in rows]
            if not current_only:
                return assessments
            latest: dict[tuple[str, str], QualityAssessment] = {}
            for assessment in assessments:
                latest.setdefault(
                    (assessment.entity.id, assessment.profile_id),
                    assessment,
                )
            return list(latest.values())

    def list_maintenance_tasks(
        self,
        *,
        entity_id: str | None = None,
        status: str | None = None,
        limit: int = 500,
    ) -> list[MaintenanceTask]:
        with self.sessions() as session:
            statement = select(MaintenanceTaskRow)
            if entity_id is not None:
                statement = statement.where(MaintenanceTaskRow.entity_id == entity_id)
            if status is not None:
                statement = statement.where(MaintenanceTaskRow.status == status)
            rows = session.scalars(
                statement.order_by(
                    MaintenanceTaskRow.updated_at.desc(),
                    MaintenanceTaskRow.id,
                ).limit(limit)
            ).all()
            return [MaintenanceTask.model_validate(row.document) for row in rows]

    def get_maintenance_task(
        self,
        task_id: str,
    ) -> MaintenanceTask | None:
        with self.sessions() as session:
            row = session.get(MaintenanceTaskRow, task_id)
            return MaintenanceTask.model_validate(row.document) if row is not None else None

    def schedule_maintenance_task(
        self,
        task_id: str,
        schedule: GraphRunSchedule,
    ) -> tuple[MaintenanceTask, GraphRunSchedule | None]:
        """Atomically pin an active quality task to one graph schedule.

        A task whose entity revision is no longer current is superseded without
        creating executable work. Repeating the same request is idempotent.
        """

        with self.sessions.begin() as session:
            row = session.get(MaintenanceTaskRow, task_id)
            if row is None:
                raise ValueError("maintenance task not found")
            task = MaintenanceTask.model_validate(row.document)
            if task.status in {"resolved", "superseded"}:
                return task, None

            entity = session.get(KnowledgeEntityRow, task.entity.id)
            if entity is None:
                raise ValueError("maintenance task entity does not exist")
            if entity.current_revision_id != task.revision_id:
                now = datetime.now(UTC)
                superseded = task.model_copy(
                    update={
                        "status": "superseded",
                        "updated_at": now,
                        "resolved_at": now,
                    }
                )
                self._write_maintenance_task_row(row, superseded)
                return superseded, None

            expected_input = {
                "maintenanceTaskId": task.id,
                "assessmentId": task.assessment_id,
                "entityId": task.entity.id,
                "entityTypeId": task.entity.type_id,
                "currentRevisionId": task.revision_id,
                "profileId": task.profile_id,
                "profileVersion": task.profile_version,
                "issueCode": task.issue.code,
                "issuePath": task.issue.path,
                "action": task.action,
            }
            if schedule.input != expected_input:
                raise ValueError("agent graph schedule input does not match maintenance task")

            if task.status == "scheduled":
                if task.agent_graph_schedule_id != schedule.id:
                    raise ValueError("maintenance task is already pinned to another schedule")
                schedule_row = session.get(AgentGraphScheduleRow, schedule.id)
                if schedule_row is None:
                    raise ValueError("maintenance task references a missing agent graph schedule")
                persisted = GraphRunSchedule.model_validate(schedule_row.document)
                self._validate_agent_graph_schedule_identity(
                    persisted,
                    schedule,
                )
                return task, persisted

            schedule_row = session.get(AgentGraphScheduleRow, schedule.id)
            if schedule_row is None:
                self._insert_agent_graph_schedule(session, schedule)
                persisted = schedule
            else:
                persisted = GraphRunSchedule.model_validate(schedule_row.document)
                self._validate_agent_graph_schedule_identity(
                    persisted,
                    schedule,
                )

            scheduled_task = task.model_copy(
                update={
                    "status": "scheduled",
                    "agent_graph_schedule_id": persisted.id,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._write_maintenance_task_row(row, scheduled_task)
            return scheduled_task, persisted

    def complete_quality_triage_schedule(
        self,
        schedule: GraphRunSchedule,
        work_item: MaintenanceWorkItem,
    ) -> tuple[GraphRunSchedule, MaintenanceWorkItem | None]:
        """Complete triage and create leaseable work in one transaction."""

        with self.sessions.begin() as session:
            schedule_row = session.get(
                AgentGraphScheduleRow,
                schedule.id,
            )
            if schedule_row is None:
                raise ValueError("agent graph schedule not found")
            existing_schedule = GraphRunSchedule.model_validate(schedule_row.document)
            self._validate_agent_graph_schedule_identity(
                existing_schedule,
                schedule,
            )
            if schedule.graph_id != "quality-maintenance-triage":
                raise ValueError("schedule is not a quality triage graph")
            if schedule.status != "completed" or not schedule.run_id:
                raise ValueError("quality triage schedule must be completed with a run id")
            if existing_schedule.status not in {"running", "completed"}:
                raise ValueError("quality triage can only complete from running state")

            task_row = session.get(
                MaintenanceTaskRow,
                work_item.maintenance_task_id,
            )
            if task_row is None:
                raise ValueError("maintenance task not found")
            task = MaintenanceTask.model_validate(task_row.document)
            if (
                work_item.triage_schedule_id != schedule.id
                or work_item.triage_run_id != schedule.run_id
                or work_item.maintenance_task_id != task.id
                or work_item.assessment_id != task.assessment_id
                or work_item.entity != task.entity
                or work_item.revision_id != task.revision_id
                or work_item.action != task.action
                or work_item.issue != task.issue
                or work_item.priority != task.priority
            ):
                raise ValueError("maintenance work identity does not match triage task")
            if work_item.requires_evidence is not True:
                raise ValueError("maintenance work must require evidence")
            if work_item.proposal_eligible is not False:
                raise ValueError("triage work may not be proposal eligible")

            schedule_row.status = schedule.status
            schedule_row.document = schedule.model_dump(
                mode="json",
                by_alias=True,
            )
            schedule_row.updated_at = schedule.updated_at

            entity = session.get(KnowledgeEntityRow, task.entity.id)
            if (
                entity is None
                or entity.current_revision_id != task.revision_id
                or task.status in {"resolved", "superseded"}
            ):
                if task.status not in {"resolved", "superseded"}:
                    now = datetime.now(UTC)
                    task = task.model_copy(
                        update={
                            "status": "superseded",
                            "updated_at": now,
                            "resolved_at": now,
                        }
                    )
                    self._write_maintenance_task_row(task_row, task)
                return schedule, None

            existing_row = session.scalar(
                select(MaintenanceWorkItemRow).where(
                    MaintenanceWorkItemRow.maintenance_task_id == task.id
                )
            )
            if existing_row is not None:
                existing_work = MaintenanceWorkItem.model_validate(existing_row.document)
                self._validate_maintenance_work_identity(
                    existing_work,
                    work_item,
                )
                return schedule, existing_work

            session.add(
                MaintenanceWorkItemRow(
                    id=work_item.id,
                    maintenance_task_id=work_item.maintenance_task_id,
                    entity_id=work_item.entity.id,
                    revision_id=work_item.revision_id,
                    route=work_item.route,
                    status=work_item.status,
                    priority=work_item.priority,
                    assignee_id=work_item.assignee_id,
                    lease_expires_at=work_item.lease_expires_at,
                    document=work_item.model_dump(
                        mode="json",
                        by_alias=True,
                    ),
                    created_at=work_item.created_at,
                    updated_at=work_item.updated_at,
                    completed_at=work_item.completed_at,
                )
            )
            session.add(
                OutboxRow(
                    id=_maintenance_work_outbox_id(work_item.id),
                    topic="maintenance.work.requested",
                    aggregate_id=work_item.id,
                    payload={
                        "workItemId": work_item.id,
                        "taskId": task.id,
                        "entityId": task.entity.id,
                        "revisionId": task.revision_id,
                        "route": work_item.route,
                    },
                    occurred_at=work_item.created_at,
                )
            )
            return schedule, work_item

    @staticmethod
    def _validate_maintenance_work_identity(
        existing: MaintenanceWorkItem,
        work_item: MaintenanceWorkItem,
    ) -> None:
        if (
            existing.id != work_item.id
            or existing.maintenance_task_id != work_item.maintenance_task_id
            or existing.entity != work_item.entity
            or existing.revision_id != work_item.revision_id
            or existing.route != work_item.route
            or existing.triage_schedule_id != work_item.triage_schedule_id
            or existing.triage_run_id != work_item.triage_run_id
        ):
            raise ValueError("maintenance work identity is immutable")

    @staticmethod
    def _write_maintenance_work_item_row(
        row: MaintenanceWorkItemRow,
        work_item: MaintenanceWorkItem,
    ) -> None:
        row.route = work_item.route
        row.status = work_item.status
        row.priority = work_item.priority
        row.assignee_id = work_item.assignee_id
        row.lease_expires_at = work_item.lease_expires_at
        row.document = work_item.model_dump(mode="json", by_alias=True)
        row.updated_at = work_item.updated_at
        row.completed_at = work_item.completed_at

    def _supersede_maintenance_work_item(
        self,
        session: Session,
        row: MaintenanceWorkItemRow,
        work_item: MaintenanceWorkItem,
        *,
        superseded_at: datetime,
    ) -> MaintenanceWorkItem:
        superseded = work_item.model_copy(
            update={
                "status": "superseded",
                "assignee_id": None,
                "lease_token": None,
                "lease_expires_at": None,
                "updated_at": superseded_at,
            }
        )
        self._write_maintenance_work_item_row(row, superseded)
        task_row = session.get(
            MaintenanceTaskRow,
            work_item.maintenance_task_id,
        )
        if task_row is not None:
            task = MaintenanceTask.model_validate(task_row.document)
            if task.status in {"open", "scheduled"}:
                task = task.model_copy(
                    update={
                        "status": "superseded",
                        "updated_at": superseded_at,
                        "resolved_at": superseded_at,
                    }
                )
                self._write_maintenance_task_row(task_row, task)
        return superseded

    @staticmethod
    def _write_agent_runtime_row(
        row: AgentRuntimeRow,
        runtime: AgentRuntime,
    ) -> None:
        row.status = runtime.status
        row.last_heartbeat_at = runtime.last_heartbeat_at
        row.document = runtime.model_dump(mode="json", by_alias=True)
        row.updated_at = runtime.updated_at

    @staticmethod
    def _agent_runtime_state_in_session(
        session: Session,
        runtime: AgentRuntime,
        *,
        observed_at: datetime,
    ) -> AgentRuntimeState:
        active_lease_count = int(
            session.scalar(
                select(func.count())
                .select_from(MaintenanceWorkItemRow)
                .where(
                    MaintenanceWorkItemRow.status == "claimed",
                    MaintenanceWorkItemRow.assignee_id == runtime.id,
                    MaintenanceWorkItemRow.lease_expires_at > observed_at,
                )
            )
            or 0
        )
        heartbeat_deadline = runtime.last_heartbeat_at + timedelta(
            seconds=runtime.heartbeat_ttl_seconds
        )
        effective_status = (
            "offline"
            if heartbeat_deadline <= observed_at
            else runtime.status
        )
        available_capacity = (
            max(runtime.max_concurrency - active_lease_count, 0)
            if effective_status == "online"
            else 0
        )
        return AgentRuntimeState(
            runtime=runtime,
            effective_status=effective_status,
            active_lease_count=active_lease_count,
            available_capacity=available_capacity,
            observed_at=observed_at,
        )

    def register_agent_runtime(
        self,
        runtime: AgentRuntime,
    ) -> AgentRuntimeState:
        with self.sessions.begin() as session:
            row = session.scalar(
                select(AgentRuntimeRow)
                .where(AgentRuntimeRow.id == runtime.id)
                .with_for_update()
            )
            if row is None:
                session.add(
                    AgentRuntimeRow(
                        id=runtime.id,
                        definition_id=runtime.definition_id,
                        definition_version=runtime.definition_version,
                        status=runtime.status,
                        last_heartbeat_at=runtime.last_heartbeat_at,
                        document=runtime.model_dump(mode="json", by_alias=True),
                        registered_at=runtime.registered_at,
                        updated_at=runtime.updated_at,
                    )
                )
                return self._agent_runtime_state_in_session(
                    session,
                    runtime,
                    observed_at=runtime.updated_at,
                )
            existing = AgentRuntime.model_validate(row.document)
            immutable_identity = (
                existing.definition_id,
                existing.definition_version,
                existing.capabilities,
                existing.supported_routes,
            )
            requested_identity = (
                runtime.definition_id,
                runtime.definition_version,
                runtime.capabilities,
                runtime.supported_routes,
            )
            if immutable_identity != requested_identity:
                raise ValueError(
                    "agent runtime definition, capabilities and routes are immutable"
                )
            updated = runtime.model_copy(
                update={"registered_at": existing.registered_at}
            )
            self._write_agent_runtime_row(row, updated)
            return self._agent_runtime_state_in_session(
                session,
                updated,
                observed_at=updated.updated_at,
            )

    def heartbeat_agent_runtime(
        self,
        runtime_id: str,
        *,
        status: str | None = None,
        heartbeat_at: datetime | None = None,
    ) -> AgentRuntimeState:
        now = heartbeat_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            row = session.scalar(
                select(AgentRuntimeRow)
                .where(AgentRuntimeRow.id == runtime_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("agent runtime not found")
            runtime = AgentRuntime.model_validate(row.document)
            if status is not None and status not in {"online", "draining"}:
                raise ValueError("agent runtime heartbeat status is invalid")
            updated = runtime.model_copy(
                update={
                    "status": status or runtime.status,
                    "last_heartbeat_at": now,
                    "updated_at": now,
                }
            )
            self._write_agent_runtime_row(row, updated)
            return self._agent_runtime_state_in_session(
                session,
                updated,
                observed_at=now,
            )

    def get_agent_runtime_state(
        self,
        runtime_id: str,
        *,
        observed_at: datetime | None = None,
    ) -> AgentRuntimeState | None:
        now = observed_at or datetime.now(UTC)
        with self.sessions() as session:
            row = session.get(AgentRuntimeRow, runtime_id)
            if row is None:
                return None
            return self._agent_runtime_state_in_session(
                session,
                AgentRuntime.model_validate(row.document),
                observed_at=now,
            )

    def list_agent_runtime_states(
        self,
        *,
        observed_at: datetime | None = None,
    ) -> list[AgentRuntimeState]:
        now = observed_at or datetime.now(UTC)
        with self.sessions() as session:
            rows = session.scalars(
                select(AgentRuntimeRow).order_by(
                    AgentRuntimeRow.status,
                    AgentRuntimeRow.id,
                )
            ).all()
            return [
                self._agent_runtime_state_in_session(
                    session,
                    AgentRuntime.model_validate(row.document),
                    observed_at=now,
                )
                for row in rows
            ]

    def claim_next_maintenance_work_item(
        self,
        runtime_id: str,
        *,
        lease_seconds: int,
        claimed_at: datetime | None = None,
    ) -> MaintenanceWorkItem | None:
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("maintenance work lease must be between 30 and 3600 seconds")
        now = claimed_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            runtime_row = session.scalar(
                select(AgentRuntimeRow)
                .where(AgentRuntimeRow.id == runtime_id)
                .with_for_update()
            )
            if runtime_row is None:
                raise ValueError("agent runtime not found")
            runtime = AgentRuntime.model_validate(runtime_row.document)
            state = self._agent_runtime_state_in_session(
                session,
                runtime,
                observed_at=now,
            )
            if state.effective_status != "online":
                raise ValueError(
                    f"agent runtime cannot claim work while {state.effective_status}"
                )
            if state.available_capacity <= 0:
                return None

            priority_order = case(
                (MaintenanceWorkItemRow.priority == "critical", 0),
                (MaintenanceWorkItemRow.priority == "high", 1),
                (MaintenanceWorkItemRow.priority == "medium", 2),
                else_=3,
            )
            candidates = session.scalars(
                select(MaintenanceWorkItemRow)
                .where(
                    MaintenanceWorkItemRow.status == "ready",
                    MaintenanceWorkItemRow.route.in_(runtime.supported_routes),
                )
                .order_by(
                    priority_order,
                    MaintenanceWorkItemRow.created_at,
                    MaintenanceWorkItemRow.id,
                )
                .with_for_update(skip_locked=True)
                .limit(200)
            ).all()
            runtime_capabilities = set(runtime.capabilities)
            for row in candidates:
                work_item = MaintenanceWorkItem.model_validate(row.document)
                if not set(work_item.required_capabilities).issubset(
                    runtime_capabilities
                ):
                    continue
                entity = session.get(KnowledgeEntityRow, work_item.entity.id)
                if entity is None or entity.current_revision_id != work_item.revision_id:
                    self._supersede_maintenance_work_item(
                        session,
                        row,
                        work_item,
                        superseded_at=now,
                    )
                    continue
                claimed = work_item.model_copy(
                    update={
                        "status": "claimed",
                        "assignee_id": runtime.id,
                        "lease_token": str(uuid4()),
                        "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "attempt": work_item.attempt + 1,
                        "updated_at": now,
                    }
                )
                self._write_maintenance_work_item_row(row, claimed)
                return claimed
            return None

    def get_maintenance_work_item(
        self,
        work_item_id: str,
    ) -> MaintenanceWorkItem | None:
        with self.sessions() as session:
            row = session.get(MaintenanceWorkItemRow, work_item_id)
            return MaintenanceWorkItem.model_validate(row.document) if row is not None else None

    def list_maintenance_work_items(
        self,
        *,
        entity_id: str | None = None,
        route: str | None = None,
        status: str | None = None,
        assignee_id: str | None = None,
        limit: int = 500,
    ) -> list[MaintenanceWorkItem]:
        with self.sessions() as session:
            statement = select(MaintenanceWorkItemRow)
            if entity_id is not None:
                statement = statement.where(MaintenanceWorkItemRow.entity_id == entity_id)
            if route is not None:
                statement = statement.where(MaintenanceWorkItemRow.route == route)
            if status is not None:
                statement = statement.where(MaintenanceWorkItemRow.status == status)
            if assignee_id is not None:
                statement = statement.where(MaintenanceWorkItemRow.assignee_id == assignee_id)
            rows = session.scalars(
                statement.order_by(
                    MaintenanceWorkItemRow.updated_at.desc(),
                    MaintenanceWorkItemRow.id,
                ).limit(limit)
            ).all()
            return [MaintenanceWorkItem.model_validate(row.document) for row in rows]

    def activate_maintenance_work_item(
        self,
        work_item_id: str,
        *,
        activated_at: datetime | None = None,
    ) -> MaintenanceWorkItem:
        now = activated_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            row = session.get(MaintenanceWorkItemRow, work_item_id)
            if row is None:
                raise ValueError("maintenance work item not found")
            work_item = MaintenanceWorkItem.model_validate(row.document)
            if work_item.status != "queued":
                return work_item
            entity = session.get(KnowledgeEntityRow, work_item.entity.id)
            if entity is None or entity.current_revision_id != work_item.revision_id:
                return self._supersede_maintenance_work_item(
                    session,
                    row,
                    work_item,
                    superseded_at=now,
                )
            ready = work_item.model_copy(update={"status": "ready", "updated_at": now})
            self._write_maintenance_work_item_row(row, ready)
            return ready

    def claim_maintenance_work_item(
        self,
        work_item_id: str,
        *,
        assignee_id: str,
        lease_seconds: int,
        claimed_at: datetime | None = None,
    ) -> MaintenanceWorkItem:
        if not assignee_id.strip():
            raise ValueError("maintenance work assignee is required")
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("maintenance work lease must be between 30 and 3600 seconds")
        now = claimed_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            row = session.scalar(
                select(MaintenanceWorkItemRow)
                .where(MaintenanceWorkItemRow.id == work_item_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("maintenance work item not found")
            work_item = MaintenanceWorkItem.model_validate(row.document)
            entity = session.get(KnowledgeEntityRow, work_item.entity.id)
            if entity is None or entity.current_revision_id != work_item.revision_id:
                return self._supersede_maintenance_work_item(
                    session,
                    row,
                    work_item,
                    superseded_at=now,
                )
            if work_item.status == "claimed":
                assert work_item.lease_expires_at is not None
                if work_item.assignee_id == assignee_id and work_item.lease_expires_at > now:
                    return work_item
                if work_item.lease_expires_at > now:
                    raise ValueError("maintenance work item is leased by another assignee")
            elif work_item.status != "ready":
                raise ValueError(f"maintenance work item cannot be claimed from {work_item.status}")

            claimed = work_item.model_copy(
                update={
                    "status": "claimed",
                    "assignee_id": assignee_id,
                    "lease_token": str(uuid4()),
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "attempt": work_item.attempt + 1,
                    "updated_at": now,
                }
            )
            self._write_maintenance_work_item_row(row, claimed)
            return claimed

    def heartbeat_maintenance_work_item(
        self,
        work_item_id: str,
        *,
        assignee_id: str,
        lease_token: str,
        lease_seconds: int,
        heartbeat_at: datetime | None = None,
    ) -> MaintenanceWorkItem:
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("maintenance work lease must be between 30 and 3600 seconds")
        now = heartbeat_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            row = session.scalar(
                select(MaintenanceWorkItemRow)
                .where(MaintenanceWorkItemRow.id == work_item_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("maintenance work item not found")
            work_item = MaintenanceWorkItem.model_validate(row.document)
            if (
                work_item.status != "claimed"
                or work_item.assignee_id != assignee_id
                or work_item.lease_token != lease_token
            ):
                raise ValueError("maintenance work lease does not match")
            assert work_item.lease_expires_at is not None
            if work_item.lease_expires_at <= now:
                raise ValueError("maintenance work lease has expired")
            renewed = work_item.model_copy(
                update={
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                }
            )
            self._write_maintenance_work_item_row(row, renewed)
            return renewed

    def release_maintenance_work_item(
        self,
        work_item_id: str,
        *,
        assignee_id: str,
        lease_token: str,
        released_at: datetime | None = None,
    ) -> MaintenanceWorkItem:
        now = released_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            row = session.scalar(
                select(MaintenanceWorkItemRow)
                .where(MaintenanceWorkItemRow.id == work_item_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("maintenance work item not found")
            work_item = MaintenanceWorkItem.model_validate(row.document)
            if (
                work_item.status != "claimed"
                or work_item.assignee_id != assignee_id
                or work_item.lease_token != lease_token
            ):
                raise ValueError("maintenance work lease does not match")
            ready = work_item.model_copy(
                update={
                    "status": "ready",
                    "assignee_id": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                }
            )
            self._write_maintenance_work_item_row(row, ready)
            return ready

    def complete_maintenance_work_item(
        self,
        work_item_id: str,
        *,
        assignee_id: str,
        lease_token: str,
        evidence_refs: list[MaintenanceEvidenceLink],
        output_refs: list[MaintenanceOutputLink],
        completed_at: datetime | None = None,
    ) -> MaintenanceWorkItem:
        now = completed_at or datetime.now(UTC)
        if not evidence_refs:
            raise ValueError("maintenance work completion requires evidence")
        if len({(item.kind, item.id) for item in evidence_refs}) != len(evidence_refs):
            raise ValueError("maintenance evidence references must be unique")
        if len({(item.kind, item.id) for item in output_refs}) != len(output_refs):
            raise ValueError("maintenance output references must be unique")
        with self.sessions.begin() as session:
            row = session.scalar(
                select(MaintenanceWorkItemRow)
                .where(MaintenanceWorkItemRow.id == work_item_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("maintenance work item not found")
            work_item = MaintenanceWorkItem.model_validate(row.document)
            entity_row = session.get(
                KnowledgeEntityRow,
                work_item.entity.id,
            )
            if entity_row is None or entity_row.current_revision_id != work_item.revision_id:
                return self._supersede_maintenance_work_item(
                    session,
                    row,
                    work_item,
                    superseded_at=now,
                )
            self._validate_maintenance_work_lease(
                work_item,
                assignee_id=assignee_id,
                lease_token=lease_token,
                now=now,
            )
            revision = session.get(
                EntityRevisionRow,
                work_item.revision_id,
            )
            if revision is None:
                raise ValueError("maintenance work revision does not exist")
            entity = KnowledgeEntity.model_validate(revision.document)

            for evidence in evidence_refs:
                if evidence.kind == "citation":
                    if not any(citation.id == evidence.id for citation in entity.citations):
                        raise ValueError(
                            f"maintenance citation does not exist on "
                            f"the pinned revision: {evidence.id}"
                        )
                elif session.get(SourceSnapshotRow, evidence.id) is None:
                    raise ValueError(f"maintenance source snapshot does not exist: {evidence.id}")

            proposal_types: set[str] = set()
            for output in output_refs:
                if output.kind == "source-acquisition-job":
                    if (
                        session.get(
                            SourceAcquisitionJobRow,
                            output.id,
                        )
                        is None
                    ):
                        raise ValueError(f"maintenance acquisition job does not exist: {output.id}")
                    continue
                proposal_row = session.get(
                    GovernedProposalRow,
                    output.id,
                )
                if proposal_row is None:
                    raise ValueError(f"maintenance governed proposal does not exist: {output.id}")
                proposal = GovernedProposal.model_validate(proposal_row.document).proposal
                if proposal.entity_id is not None and proposal.entity_id != work_item.entity.id:
                    raise ValueError("maintenance proposal targets another entity")
                proposal_types.add(proposal.proposal_type)

            evidence_kinds = {item.kind for item in evidence_refs}
            output_kinds = {item.kind for item in output_refs}
            if work_item.route == "source-acquisition" and "source-snapshot" not in evidence_kinds:
                raise ValueError("source-acquisition work requires a source snapshot")
            if work_item.route == "translation-evidence":
                if "governed-proposal" not in output_kinds or not (
                    proposal_types & {"translation", "content"}
                ):
                    raise ValueError("translation work requires a translation or content proposal")
            if work_item.route == "taxonomy-review":
                if "governed-proposal" not in output_kinds or not (
                    proposal_types & {"relation", "schema"}
                ):
                    raise ValueError("taxonomy work requires a relation or schema proposal")

            completed = work_item.model_copy(
                update={
                    "status": "completed",
                    "assignee_id": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "evidence_refs": evidence_refs,
                    "output_refs": output_refs,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            self._write_maintenance_work_item_row(row, completed)
            return completed

    def submit_maintenance_governed_proposal(
        self,
        work_item_id: str,
        *,
        assignee_id: str,
        lease_token: str,
        governed: GovernedProposal,
        evidence_refs: list[MaintenanceEvidenceLink],
        completed_at: datetime | None = None,
    ) -> tuple[MaintenanceWorkItem, GovernedProposal]:
        """Atomically persist one route-compatible proposal and complete its work."""

        now = completed_at or datetime.now(UTC)
        if governed.version != 0 or governed.proposal.status != "proposed":
            raise ValueError("maintenance submission requires a new proposed proposal")
        if not evidence_refs or any(item.kind != "citation" for item in evidence_refs):
            raise ValueError("maintenance proposal submission requires citation evidence")
        if len({item.id for item in evidence_refs}) != len(evidence_refs):
            raise ValueError("maintenance proposal evidence references must be unique")
        next_version = 0
        with self.sessions.begin() as session:
            row = session.scalar(
                select(MaintenanceWorkItemRow)
                .where(MaintenanceWorkItemRow.id == work_item_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("maintenance work item not found")
            work_item = MaintenanceWorkItem.model_validate(row.document)
            if work_item.status == "completed":
                output = next(
                    (
                        item
                        for item in work_item.output_refs
                        if item.kind == "governed-proposal"
                    ),
                    None,
                )
                if output is None or output.id != governed.proposal.id:
                    raise ValueError("maintenance work item already has another output")
                proposal_row = session.get(GovernedProposalRow, output.id)
                if proposal_row is None:
                    raise ValueError("completed maintenance proposal does not exist")
                existing = GovernedProposal.model_validate(proposal_row.document)
                if (
                    existing.proposal.model_copy(update={"status": "proposed"})
                    != governed.proposal
                ):
                    raise ValueError("maintenance proposal replay does not match persisted output")
                return work_item, existing

            entity_row = session.get(KnowledgeEntityRow, work_item.entity.id)
            if entity_row is None or entity_row.current_revision_id != work_item.revision_id:
                superseded = self._supersede_maintenance_work_item(
                    session,
                    row,
                    work_item,
                    superseded_at=now,
                )
                return superseded, governed
            self._validate_maintenance_work_lease(
                work_item,
                assignee_id=assignee_id,
                lease_token=lease_token,
                now=now,
            )
            revision = session.get(EntityRevisionRow, work_item.revision_id)
            if revision is None:
                raise ValueError("maintenance work revision does not exist")
            entity = KnowledgeEntity.model_validate(revision.document)
            proposal = governed.proposal
            if proposal.entity_id != entity.ref.id:
                raise ValueError("maintenance proposal targets another entity")
            expected_type = {
                "translation-evidence": "translation",
                "taxonomy-review": "relation",
            }.get(work_item.route)
            if expected_type is None or proposal.proposal_type != expected_type:
                raise ValueError("maintenance proposal type is incompatible with the work route")
            if proposal.risk != "medium":
                raise ValueError("translation and taxonomy work require medium-risk review")
            allowed_paths = (
                {"/names/-", "/description/-"}
                if work_item.route == "translation-evidence"
                else {"/taxonomyNodeIds"}
            )
            if not proposal.operations or any(
                operation.path not in allowed_paths for operation in proposal.operations
            ):
                raise ValueError("maintenance proposal contains an unsupported operation path")
            cited_by_operations = {
                citation_id
                for operation in proposal.operations
                for citation_id in operation.citation_ids
            }
            evidence_ids = {item.id for item in evidence_refs}
            if cited_by_operations != evidence_ids:
                raise ValueError(
                    "maintenance proposal citations must match completion evidence"
                )
            known_citations = {citation.id for citation in entity.citations}
            unknown_citations = sorted(evidence_ids - known_citations)
            if unknown_citations:
                raise ValueError(
                    "maintenance proposal references citations outside the pinned revision: "
                    + ", ".join(unknown_citations)
                )
            next_version = self._write_governed_proposal(
                session,
                governed,
                require_new=True,
            )
            completed = work_item.model_copy(
                update={
                    "status": "completed",
                    "assignee_id": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "evidence_refs": evidence_refs,
                    "output_refs": [
                        MaintenanceOutputLink(
                            kind="governed-proposal",
                            id=proposal.id,
                        )
                    ],
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            self._write_maintenance_work_item_row(row, completed)
        governed.version = next_version
        return completed, governed

    @staticmethod
    def _validate_maintenance_work_lease(
        work_item: MaintenanceWorkItem,
        *,
        assignee_id: str,
        lease_token: str,
        now: datetime,
    ) -> None:
        if (
            work_item.status != "claimed"
            or work_item.assignee_id != assignee_id
            or work_item.lease_token != lease_token
        ):
            raise ValueError("maintenance work lease does not match")
        assert work_item.lease_expires_at is not None
        if work_item.lease_expires_at <= now:
            raise ValueError("maintenance work lease has expired")

    def block_maintenance_work_item(
        self,
        work_item_id: str,
        *,
        assignee_id: str,
        lease_token: str,
        reason: str,
        blocked_at: datetime | None = None,
    ) -> MaintenanceWorkItem:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("maintenance work block reason is required")
        now = blocked_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            row = session.scalar(
                select(MaintenanceWorkItemRow)
                .where(MaintenanceWorkItemRow.id == work_item_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("maintenance work item not found")
            work_item = MaintenanceWorkItem.model_validate(row.document)
            entity = session.get(KnowledgeEntityRow, work_item.entity.id)
            if entity is None or entity.current_revision_id != work_item.revision_id:
                return self._supersede_maintenance_work_item(
                    session,
                    row,
                    work_item,
                    superseded_at=now,
                )
            self._validate_maintenance_work_lease(
                work_item,
                assignee_id=assignee_id,
                lease_token=lease_token,
                now=now,
            )
            blocked = work_item.model_copy(
                update={
                    "status": "blocked",
                    "assignee_id": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "blocked_reason": normalized_reason,
                    "updated_at": now,
                }
            )
            self._write_maintenance_work_item_row(row, blocked)
            return blocked

    def requeue_blocked_maintenance_work_item(
        self,
        work_item_id: str,
        *,
        requeued_at: datetime | None = None,
    ) -> tuple[MaintenanceWorkItem, str | None]:
        now = requeued_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            row = session.scalar(
                select(MaintenanceWorkItemRow)
                .where(MaintenanceWorkItemRow.id == work_item_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("maintenance work item not found")
            work_item = MaintenanceWorkItem.model_validate(row.document)
            if work_item.status != "blocked":
                raise ValueError(
                    f"maintenance work item cannot be requeued from {work_item.status}"
                )
            entity = session.get(KnowledgeEntityRow, work_item.entity.id)
            if entity is None or entity.current_revision_id != work_item.revision_id:
                superseded = self._supersede_maintenance_work_item(
                    session,
                    row,
                    work_item,
                    superseded_at=now,
                )
                return superseded, None
            requeued = work_item.model_copy(
                update={
                    "status": "queued",
                    "blocked_reason": None,
                    "updated_at": now,
                }
            )
            self._write_maintenance_work_item_row(row, requeued)
            event_id = str(uuid4())
            session.add(
                OutboxRow(
                    id=event_id,
                    topic="maintenance.work.requested",
                    aggregate_id=work_item.id,
                    payload={
                        "workItemId": work_item.id,
                        "maintenanceTaskId": work_item.maintenance_task_id,
                        "route": work_item.route,
                        "revisionId": work_item.revision_id,
                        "requeued": True,
                    },
                    occurred_at=now,
                )
            )
            return requeued, event_id

    def list_entities(self, type_id: str | None = None) -> list[KnowledgeEntity]:
        with self.sessions() as session:
            statement = select(KnowledgeEntityRow).order_by(KnowledgeEntityRow.slug)
            if type_id is not None:
                statement = statement.where(KnowledgeEntityRow.entity_type_id == type_id)
            rows = session.scalars(statement).all()
            entities: list[KnowledgeEntity] = []
            for row in rows:
                if row.current_revision_id is None:
                    continue
                revision = session.get(EntityRevisionRow, row.current_revision_id)
                if revision is not None:
                    entities.append(KnowledgeEntity.model_validate(revision.document))
            return entities

    def current_revision_ids(self, entity_ids: list[str]) -> dict[str, str]:
        with self.sessions() as session:
            rows = session.scalars(
                select(KnowledgeEntityRow).where(KnowledgeEntityRow.id.in_(entity_ids))
            ).all()
            return {
                row.id: row.current_revision_id
                for row in rows
                if row.current_revision_id is not None
            }

    def activate_entity_revisions(self, revisions: dict[str, str]) -> None:
        with self.sessions.begin() as session:
            for entity_id, revision_id in revisions.items():
                entity = session.get(KnowledgeEntityRow, entity_id)
                revision = session.get(EntityRevisionRow, revision_id)
                if entity is None or revision is None:
                    raise ValueError("entity rollback revision does not exist")
                if revision.entity_id != entity_id:
                    raise ValueError("entity rollback revision belongs to another entity")
                entity.current_revision_id = revision_id
                self._sync_relation_edges(
                    session,
                    KnowledgeEntity.model_validate(revision.document),
                )
                session.add(
                    OutboxRow(
                        id=str(uuid4()),
                        topic="knowledge.entity.revised",
                        aggregate_id=entity_id,
                        payload={
                            "entityId": entity_id,
                            "revisionId": revision_id,
                            "dataVersion": revision.data_version,
                            "rollback": True,
                        },
                        occurred_at=datetime.now(UTC),
                    )
                )

    def deactivate_entities(self, entity_ids: list[str]) -> None:
        if not entity_ids:
            return
        with self.sessions.begin() as session:
            rows = session.scalars(
                select(KnowledgeEntityRow).where(KnowledgeEntityRow.id.in_(entity_ids))
            ).all()
            if len(rows) != len(set(entity_ids)):
                raise ValueError("entity to deactivate does not exist")
            for entity in rows:
                edges = session.scalars(
                    select(RelationEdgeRow).where(
                        (RelationEdgeRow.source_entity_id == entity.id)
                        | (RelationEdgeRow.target_entity_id == entity.id)
                    )
                ).all()
                for edge in edges:
                    session.delete(edge)
                entity.current_revision_id = None
                entity.publication_status = "archived"
                session.add(
                    OutboxRow(
                        id=str(uuid4()),
                        topic="knowledge.entity.revised",
                        aggregate_id=entity.id,
                        payload={
                            "entityId": entity.id,
                            "revisionId": None,
                            "dataVersion": "rollback",
                            "deactivated": True,
                        },
                        occurred_at=datetime.now(UTC),
                    )
                )

    def entities_at_revisions(
        self,
        revisions: dict[str, str],
    ) -> list[KnowledgeEntity]:
        with self.sessions() as session:
            entities: list[KnowledgeEntity] = []
            for entity_id, revision_id in revisions.items():
                revision = session.get(EntityRevisionRow, revision_id)
                if revision is None or revision.entity_id != entity_id:
                    raise ValueError("entity revision snapshot does not exist")
                entities.append(KnowledgeEntity.model_validate(revision.document))
            return entities

    def save_governed_proposal(self, governed: GovernedProposal) -> None:
        self.save_governed_proposals([governed])

    def save_governed_proposals(
        self,
        governed_items: list[GovernedProposal],
    ) -> None:
        next_versions: dict[str, int] = {}
        with self.sessions.begin() as session:
            for governed in governed_items:
                next_versions[governed.proposal.id] = self._write_governed_proposal(
                    session, governed
                )
        for governed in governed_items:
            governed.version = next_versions[governed.proposal.id]

    def get_governed_proposal(self, proposal_id: str) -> GovernedProposal | None:
        with self.sessions() as session:
            row = session.get(GovernedProposalRow, proposal_id)
            return GovernedProposal.model_validate(row.document) if row else None

    def list_governed_proposals(self) -> list[GovernedProposal]:
        with self.sessions() as session:
            rows = session.scalars(
                select(GovernedProposalRow).order_by(GovernedProposalRow.updated_at.desc())
            ).all()
            return [GovernedProposal.model_validate(row.document) for row in rows]

    def save_agent_graph_run(
        self,
        *,
        run_id: str,
        graph_id: str,
        status: str,
        document: dict[str, object],
    ) -> None:
        with self.sessions.begin() as session:
            row = session.get(AgentGraphRunRow, run_id)
            if row is None:
                session.add(
                    AgentGraphRunRow(
                        id=run_id,
                        graph_id=graph_id,
                        status=status,
                        document=document,
                        updated_at=datetime.now(UTC),
                    )
                )
            else:
                if row.graph_id != graph_id:
                    raise ValueError("agent graph run id belongs to another graph")
                row.status = status
                row.document = document
                row.updated_at = datetime.now(UTC)

    def get_agent_graph_run(self, run_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.get(AgentGraphRunRow, run_id)
            return dict(row.document) if row else None

    def save_agent_graph_schedule(self, schedule: GraphRunSchedule) -> None:
        with self.sessions.begin() as session:
            row = session.get(AgentGraphScheduleRow, schedule.id)
            document = schedule.model_dump(mode="json", by_alias=True)
            if row is None:
                self._insert_agent_graph_schedule(session, schedule)
                return

            existing = GraphRunSchedule.model_validate(row.document)
            self._validate_agent_graph_schedule_identity(existing, schedule)
            allowed_transitions = {
                "queued": {"queued", "dispatched", "canceled"},
                "dispatched": {"dispatched", "running", "failed", "canceled"},
                "running": {"running", "completed", "failed", "canceled"},
                "completed": {"completed"},
                "failed": {"failed", "dispatched", "canceled"},
                "canceled": {"canceled"},
            }
            if schedule.status not in allowed_transitions[existing.status]:
                raise ValueError(
                    f"invalid agent graph schedule transition: "
                    f"{existing.status} -> {schedule.status}"
                )
            row.status = schedule.status
            row.document = document
            row.updated_at = schedule.updated_at

    @staticmethod
    def _validate_agent_graph_schedule_identity(
        existing: GraphRunSchedule,
        schedule: GraphRunSchedule,
    ) -> None:
        if (
            existing.graph_id != schedule.graph_id
            or existing.graph_version != schedule.graph_version
            or existing.input != schedule.input
            or existing.idempotency_key != schedule.idempotency_key
        ):
            raise ValueError("agent graph schedule identity is immutable")

    @staticmethod
    def _insert_agent_graph_schedule(
        session: Session,
        schedule: GraphRunSchedule,
    ) -> None:
        conflict = session.scalar(
            select(AgentGraphScheduleRow).where(
                AgentGraphScheduleRow.idempotency_key == schedule.idempotency_key
            )
        )
        if conflict is not None:
            raise ValueError("agent graph schedule idempotency key already exists")
        session.add(
            AgentGraphScheduleRow(
                id=schedule.id,
                graph_id=schedule.graph_id,
                status=schedule.status,
                idempotency_key=schedule.idempotency_key,
                requested_by=schedule.requested_by,
                document=schedule.model_dump(mode="json", by_alias=True),
                created_at=schedule.created_at,
                updated_at=schedule.updated_at,
            )
        )
        session.add(
            OutboxRow(
                id=_agent_schedule_outbox_id(schedule.id),
                topic="agent.graph.scheduled",
                aggregate_id=schedule.id,
                payload={
                    "scheduleId": schedule.id,
                    "graphId": schedule.graph_id,
                    "graphVersion": schedule.graph_version,
                },
                occurred_at=schedule.created_at,
            )
        )

    def get_agent_graph_schedule(
        self,
        schedule_id: str,
    ) -> GraphRunSchedule | None:
        with self.sessions() as session:
            row = session.get(AgentGraphScheduleRow, schedule_id)
            return GraphRunSchedule.model_validate(row.document) if row else None

    def get_agent_graph_schedule_by_idempotency(
        self,
        idempotency_key: str,
    ) -> GraphRunSchedule | None:
        with self.sessions() as session:
            row = session.scalar(
                select(AgentGraphScheduleRow).where(
                    AgentGraphScheduleRow.idempotency_key == idempotency_key
                )
            )
            return GraphRunSchedule.model_validate(row.document) if row else None

    def list_agent_graph_schedules(
        self,
        *,
        graph_id: str | None = None,
        limit: int = 100,
    ) -> list[GraphRunSchedule]:
        with self.sessions() as session:
            statement = select(AgentGraphScheduleRow).order_by(
                AgentGraphScheduleRow.created_at.desc()
            )
            if graph_id:
                statement = statement.where(AgentGraphScheduleRow.graph_id == graph_id)
            statement = statement.limit(limit)
            rows = session.scalars(statement).all()
            return [GraphRunSchedule.model_validate(row.document) for row in rows]

    def count_agent_graph_schedules(
        self,
        *,
        statuses: set[str] | None = None,
    ) -> int:
        with self.sessions() as session:
            statement = select(func.count()).select_from(AgentGraphScheduleRow)
            if statuses:
                statement = statement.where(AgentGraphScheduleRow.status.in_(statuses))
            return int(session.scalar(statement) or 0)

    def save_schema_migration(
        self,
        manifest: SchemaMigrationManifest,
    ) -> None:
        with self.sessions.begin() as session:
            row = session.get(SchemaMigrationRow, manifest.id)
            document = manifest.model_dump(mode="json", by_alias=True)
            if row is None:
                conflict = session.scalar(
                    select(SchemaMigrationRow).where(
                        SchemaMigrationRow.proposal_id == manifest.proposal_id
                    )
                )
                if conflict is not None:
                    raise ValueError("schema migration proposal is already linked")
                if manifest.status != "planned":
                    raise ValueError("new schema migration must be planned")
                session.add(
                    SchemaMigrationRow(
                        id=manifest.id,
                        schema_id=manifest.schema_id,
                        schema_kind=manifest.schema_kind,
                        status=manifest.status,
                        proposal_id=manifest.proposal_id,
                        document=document,
                        created_at=manifest.created_at,
                        updated_at=datetime.now(UTC),
                    )
                )
                return
            existing = SchemaMigrationManifest.model_validate(row.document)
            immutable_fields = (
                "schema_kind",
                "schema_id",
                "from_version",
                "to_version",
                "proposed_document",
                "operations",
                "proposal_id",
                "frozen_revision_ids",
                "data_version",
                "created_by",
            )
            if any(
                getattr(existing, field) != getattr(manifest, field) for field in immutable_fields
            ):
                raise ValueError("schema migration identity is immutable")
            allowed = {
                "planned": {"planned", "applied", "failed"},
                "applied": {"applied", "rolled-back"},
                "rolled-back": {"rolled-back"},
                "failed": {"failed", "planned"},
            }
            if manifest.status not in allowed[existing.status]:
                raise ValueError(
                    f"invalid schema migration transition: {existing.status} -> {manifest.status}"
                )
            row.status = manifest.status
            row.document = document
            row.updated_at = datetime.now(UTC)

    def get_schema_migration(
        self,
        migration_id: str,
    ) -> SchemaMigrationManifest | None:
        with self.sessions() as session:
            row = session.get(SchemaMigrationRow, migration_id)
            return SchemaMigrationManifest.model_validate(row.document) if row else None

    def list_schema_migrations(self) -> list[SchemaMigrationManifest]:
        with self.sessions() as session:
            rows = session.scalars(
                select(SchemaMigrationRow).order_by(SchemaMigrationRow.created_at.desc())
            ).all()
            return [SchemaMigrationManifest.model_validate(row.document) for row in rows]

    def apply_schema_migration(
        self,
        manifest: SchemaMigrationManifest,
        entities: list[KnowledgeEntity],
    ) -> None:
        if manifest.status != "applied":
            raise ValueError("migration apply requires an applied manifest")
        with self.sessions.begin() as session:
            row = session.get(SchemaMigrationRow, manifest.id)
            if row is None:
                raise ValueError("schema migration does not exist")
            existing = SchemaMigrationManifest.model_validate(row.document)
            if existing.status != "planned":
                raise ValueError("only planned schema migration can be applied")
            activation = session.get(
                SchemaActivationRow,
                {"id": manifest.schema_id, "kind": manifest.schema_kind},
            )
            if activation is None or activation.schema_version != manifest.from_version:
                raise ValueError("active schema version changed after migration planning")
            current_rows = session.scalars(
                select(KnowledgeEntityRow).where(
                    KnowledgeEntityRow.id.in_(list(manifest.frozen_revision_ids))
                )
            ).all()
            current = {item.id: item.current_revision_id for item in current_rows}
            if current != manifest.frozen_revision_ids:
                raise ValueError("affected entity revisions changed after planning")
            self._save_schema_document_in_session(
                session,
                document_id=manifest.schema_id,
                schema_version=manifest.to_version,
                kind=manifest.schema_kind,
                document=manifest.proposed_document,
                activate=True,
            )
            for entity in entities:
                self._save_entity_in_session(session, entity)
            row.status = manifest.status
            row.document = manifest.model_dump(mode="json", by_alias=True)
            row.updated_at = datetime.now(UTC)
            session.add(
                OutboxRow(
                    id=str(uuid4()),
                    topic="schema.migrated",
                    aggregate_id=manifest.id,
                    payload={
                        "migrationId": manifest.id,
                        "schemaId": manifest.schema_id,
                        "schemaVersion": manifest.to_version,
                        "entityCount": len(entities),
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

    def rollback_schema_migration(
        self,
        manifest: SchemaMigrationManifest,
    ) -> None:
        if manifest.status != "rolled-back":
            raise ValueError("migration rollback requires a rolled-back manifest")
        with self.sessions.begin() as session:
            row = session.get(SchemaMigrationRow, manifest.id)
            if row is None:
                raise ValueError("schema migration does not exist")
            existing = SchemaMigrationManifest.model_validate(row.document)
            if existing.status != "applied":
                raise ValueError("only applied schema migration can be rolled back")
            activation = session.get(
                SchemaActivationRow,
                {"id": manifest.schema_id, "kind": manifest.schema_kind},
            )
            if activation is None or activation.schema_version != manifest.to_version:
                raise ValueError("active schema version is not the migration target")
            current_rows = session.scalars(
                select(KnowledgeEntityRow).where(
                    KnowledgeEntityRow.id.in_(list(manifest.applied_revision_ids))
                )
            ).all()
            current = {item.id: item.current_revision_id for item in current_rows}
            if current != manifest.applied_revision_ids:
                raise ValueError("migrated entities changed after migration apply")
            activation.schema_version = manifest.from_version
            activation.updated_at = datetime.now(UTC)
            for entity_id, revision_id in manifest.frozen_revision_ids.items():
                entity_row = session.get(KnowledgeEntityRow, entity_id)
                revision = session.get(EntityRevisionRow, revision_id)
                if entity_row is None or revision is None:
                    raise ValueError("migration rollback revision does not exist")
                entity_row.current_revision_id = revision_id
                restored = KnowledgeEntity.model_validate(revision.document)
                self._sync_relation_edges(session, restored)
                session.add(
                    OutboxRow(
                        id=str(uuid4()),
                        topic="knowledge.entity.revised",
                        aggregate_id=entity_id,
                        payload={
                            "entityId": entity_id,
                            "revisionId": revision_id,
                            "dataVersion": restored.revision.data_version,
                            "schemaMigrationRollback": manifest.id,
                        },
                        occurred_at=datetime.now(UTC),
                    )
                )
            row.status = manifest.status
            row.document = manifest.model_dump(mode="json", by_alias=True)
            row.updated_at = datetime.now(UTC)
            session.add(
                OutboxRow(
                    id=str(uuid4()),
                    topic="schema.migration.rolled-back",
                    aggregate_id=manifest.id,
                    payload={
                        "migrationId": manifest.id,
                        "schemaId": manifest.schema_id,
                        "schemaVersion": manifest.from_version,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

    def save_release(self, release: ReleaseManifest) -> None:
        with self.sessions.begin() as session:
            row = session.get(ReleaseManifestRow, release.id)
            document = release.model_dump(mode="json", by_alias=True)
            if row is None:
                session.add(
                    ReleaseManifestRow(
                        id=release.id,
                        data_version=release.data_version,
                        status=release.status,
                        previous_release_id=release.previous_release_id,
                        document=document,
                        created_at=release.created_at,
                    )
                )
            else:
                row.status = release.status
                row.document = document

    def get_release(self, release_id: str) -> ReleaseManifest | None:
        with self.sessions() as session:
            row = session.get(ReleaseManifestRow, release_id)
            return ReleaseManifest.model_validate(row.document) if row is not None else None

    def begin_release_publication(
        self,
        release: ReleaseManifest,
        entities: list[KnowledgeEntity],
    ) -> None:
        if release.status != "publishing":
            raise ValueError("release must be publishing before entity commit")
        entity_ids = [entity.ref.id for entity in entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("release contains duplicate entity ids")
        with self.sessions.begin() as session:
            release_row = session.get(ReleaseManifestRow, release.id)
            if release_row is None:
                raise ValueError("release does not exist")
            existing_release = ReleaseManifest.model_validate(release_row.document)
            if existing_release.status != "staged":
                raise ValueError("only staged releases can begin publication")
            if (
                existing_release.proposal_ids != release.proposal_ids
                or existing_release.data_version != release.data_version
            ):
                raise ValueError("release identity changed before publication")
            current_rows = {
                row.id: row
                for row in session.scalars(
                    select(KnowledgeEntityRow).where(KnowledgeEntityRow.id.in_(entity_ids))
                ).all()
            }
            expected_existing = set(entity_ids) - set(release.created_entity_ids)
            if set(release.entity_revisions_before) != expected_existing:
                raise ValueError("release revision snapshot does not cover existing entities")
            for entity_id, revision_id in release.entity_revisions_before.items():
                row = current_rows.get(entity_id)
                if row is None or row.current_revision_id != revision_id:
                    raise ValueError("entity revision changed after release staging")
            for entity_id in release.created_entity_ids:
                if entity_id in current_rows:
                    raise ValueError("new release entity was created concurrently")
            for entity in entities:
                self._save_entity_in_session(
                    session,
                    entity,
                    outbox_metadata={"releaseId": release.id},
                )
            release_row.status = release.status
            release_row.document = release.model_dump(
                mode="json",
                by_alias=True,
            )

    def complete_release_publication(
        self,
        release: ReleaseManifest,
        proposals: list[GovernedProposal],
    ) -> list[str]:
        if release.status != "published":
            raise ValueError("release completion requires published status")
        next_versions: dict[str, int] = {}
        with self.sessions.begin() as session:
            release_row = session.get(ReleaseManifestRow, release.id)
            if release_row is None:
                raise ValueError("release does not exist")
            existing = ReleaseManifest.model_validate(release_row.document)
            if existing.status != "publishing":
                raise ValueError("only publishing releases can be completed")
            release_row.status = release.status
            release_row.document = release.model_dump(
                mode="json",
                by_alias=True,
            )
            for governed in proposals:
                proposal_row = session.get(
                    GovernedProposalRow,
                    governed.proposal.id,
                )
                if proposal_row is None:
                    raise ValueError("release proposal does not exist")
                next_versions[governed.proposal.id] = self._write_governed_proposal(
                    session,
                    governed,
                )
            published_event_id = _release_outbox_id(
                "published",
                release.id,
            )
            if session.get(OutboxRow, published_event_id) is None:
                session.add(
                    OutboxRow(
                        id=published_event_id,
                        topic="release.published",
                        aggregate_id=release.id,
                        payload={
                            "releaseId": release.id,
                            "dataVersion": release.data_version,
                            "trigger": release.trigger,
                            "searchIndex": release.search_index,
                            "entityRevisionCount": len(release.entity_revisions_after),
                            "verificationStatus": (release.verification.status),
                        },
                        occurred_at=datetime.now(UTC),
                    )
                )
            pending_rows = session.scalars(
                select(OutboxRow).where(
                    OutboxRow.topic == "knowledge.entity.revised",
                    OutboxRow.published_at.is_(None),
                )
            ).all()
            published_ids = [
                row.id for row in pending_rows if row.payload.get("releaseId") == release.id
            ]
            published_at = datetime.now(UTC)
            for row in pending_rows:
                if row.id in published_ids:
                    row.published_at = published_at
                    row.error = None
        for governed in proposals:
            governed.version = next_versions[governed.proposal.id]
        return published_ids

    def update_release_verification(
        self,
        release_id: str,
        verification: ReleaseVerification,
        *,
        expected_statuses: set[str],
    ) -> ReleaseManifest:
        with self.sessions.begin() as session:
            row = session.get(ReleaseManifestRow, release_id)
            if row is None:
                raise ValueError("release does not exist")
            current = ReleaseManifest.model_validate(row.document)
            if current.status != "published":
                raise ValueError("only a published release can be verified")
            if current.verification.status not in expected_statuses:
                raise ValueError(
                    "release verification state changed concurrently: "
                    f"{current.verification.status}"
                )
            updated = current.model_copy(
                deep=True,
                update={"verification": verification},
            )
            row.document = updated.model_dump(
                mode="json",
                by_alias=True,
            )
            return updated

    def begin_release_rollback(self, release: ReleaseManifest) -> None:
        if release.status != "rolling-back":
            raise ValueError("release must be rolling-back before database restore")
        with self.sessions.begin() as session:
            release_row = session.get(ReleaseManifestRow, release.id)
            if release_row is None:
                raise ValueError("release does not exist")
            existing = ReleaseManifest.model_validate(release_row.document)
            if existing.status != "published":
                raise ValueError("only published releases can begin rollback")
            for entity_id, revision_id in release.entity_revisions_after.items():
                entity_row = session.get(KnowledgeEntityRow, entity_id)
                if entity_row is None or entity_row.current_revision_id != revision_id:
                    raise ValueError("released entity changed after publication")
            for entity_id, revision_id in release.entity_revisions_before.items():
                entity_row = session.get(KnowledgeEntityRow, entity_id)
                revision = session.get(EntityRevisionRow, revision_id)
                if entity_row is None or revision is None or revision.entity_id != entity_id:
                    raise ValueError("release rollback revision does not exist")
                entity_row.current_revision_id = revision_id
                restored = KnowledgeEntity.model_validate(revision.document)
                entity_row.slug = restored.ref.slug
                entity_row.entity_type_id = restored.ref.type_id
                entity_row.publication_status = restored.publication_status
                self._sync_relation_edges(session, restored)
                session.add(
                    OutboxRow(
                        id=str(uuid4()),
                        topic="knowledge.entity.revised",
                        aggregate_id=entity_id,
                        payload={
                            "entityId": entity_id,
                            "revisionId": revision_id,
                            "dataVersion": restored.revision.data_version,
                            "rollback": True,
                            "releaseId": release.id,
                        },
                        occurred_at=datetime.now(UTC),
                    )
                )
            for entity_id in release.created_entity_ids:
                entity_row = session.get(KnowledgeEntityRow, entity_id)
                if entity_row is None:
                    raise ValueError("created release entity does not exist")
                edges = session.scalars(
                    select(RelationEdgeRow).where(
                        (RelationEdgeRow.source_entity_id == entity_id)
                        | (RelationEdgeRow.target_entity_id == entity_id)
                    )
                ).all()
                for edge in edges:
                    session.delete(edge)
                entity_row.current_revision_id = None
                entity_row.publication_status = "archived"
                session.add(
                    OutboxRow(
                        id=str(uuid4()),
                        topic="knowledge.entity.revised",
                        aggregate_id=entity_id,
                        payload={
                            "entityId": entity_id,
                            "revisionId": None,
                            "dataVersion": f"rollback-{release.id}",
                            "deactivated": True,
                            "releaseId": release.id,
                        },
                        occurred_at=datetime.now(UTC),
                    )
                )
            release_row.status = release.status
            release_row.document = release.model_dump(
                mode="json",
                by_alias=True,
            )

    def complete_release_rollback(
        self,
        release: ReleaseManifest,
    ) -> list[str]:
        if release.status != "rolled-back":
            raise ValueError("release rollback completion requires rolled-back status")
        with self.sessions.begin() as session:
            release_row = session.get(ReleaseManifestRow, release.id)
            if release_row is None:
                raise ValueError("release does not exist")
            existing = ReleaseManifest.model_validate(release_row.document)
            if existing.status != "rolling-back":
                raise ValueError("only rolling-back releases can be completed")
            release_row.status = release.status
            release_row.document = release.model_dump(
                mode="json",
                by_alias=True,
            )
            pending_rows = session.scalars(
                select(OutboxRow).where(
                    OutboxRow.topic == "knowledge.entity.revised",
                    OutboxRow.published_at.is_(None),
                )
            ).all()
            published_ids = [
                row.id
                for row in pending_rows
                if row.payload.get("releaseId") == release.id
                and (row.payload.get("rollback") is True or row.payload.get("deactivated") is True)
            ]
            published_at = datetime.now(UTC)
            for row in pending_rows:
                if row.id in published_ids:
                    row.published_at = published_at
                    row.error = None
            return published_ids

    def list_releases(self) -> list[ReleaseManifest]:
        with self.sessions() as session:
            rows = session.scalars(
                select(ReleaseManifestRow).order_by(ReleaseManifestRow.created_at.desc())
            ).all()
            return [ReleaseManifest.model_validate(row.document) for row in rows]

    def save_source_definition(self, source: SourceDefinition) -> None:
        with self.sessions.begin() as session:
            row = session.get(SourceDefinitionRow, (source.id, source.version))
            document = source.model_dump(mode="json", by_alias=True)
            if row is not None and row.document != document:
                raise ValueError("source definition versions are immutable")
            if row is None:
                session.add(
                    SourceDefinitionRow(
                        id=source.id,
                        version=source.version,
                        status=source.status,
                        license_status=source.license_status,
                        document=document,
                        created_at=source.created_at,
                    )
                )

    def get_source_definition(
        self,
        source_id: str,
        *,
        version: str | None = None,
    ) -> SourceDefinition | None:
        with self.sessions() as session:
            statement = select(SourceDefinitionRow).where(SourceDefinitionRow.id == source_id)
            if version is not None:
                statement = statement.where(SourceDefinitionRow.version == version)
            statement = statement.order_by(SourceDefinitionRow.created_at.desc())
            row = session.scalar(statement)
            return SourceDefinition.model_validate(row.document) if row else None

    def list_source_definitions(self) -> list[SourceDefinition]:
        with self.sessions() as session:
            rows = session.scalars(
                select(SourceDefinitionRow).order_by(
                    SourceDefinitionRow.id,
                    SourceDefinitionRow.created_at.desc(),
                )
            ).all()
            latest: dict[str, SourceDefinition] = {}
            for row in rows:
                latest.setdefault(
                    row.id,
                    SourceDefinition.model_validate(row.document),
                )
            return list(latest.values())

    def save_source_snapshot(self, snapshot: SourceSnapshot) -> None:
        with self.sessions.begin() as session:
            row = session.get(SourceSnapshotRow, snapshot.id)
            document = snapshot.model_dump(mode="json", by_alias=True)
            if row is not None and row.document != document:
                raise ValueError("source snapshots are immutable")
            if row is None:
                session.add(
                    SourceSnapshotRow(
                        id=snapshot.id,
                        source_id=snapshot.source_id,
                        source_version=snapshot.source_version,
                        content_sha256=snapshot.content_sha256,
                        storage_key=snapshot.storage_key,
                        document=document,
                        retrieved_at=snapshot.retrieved_at,
                    )
                )
                session.add(
                    OutboxRow(
                        id=str(uuid4()),
                        topic="source.snapshot.captured",
                        aggregate_id=snapshot.id,
                        payload={
                            "snapshotId": snapshot.id,
                            "sourceId": snapshot.source_id,
                            "sourceVersion": snapshot.source_version,
                        },
                        occurred_at=datetime.now(UTC),
                    )
                )

    def create_source_acquisition_job(self, job: SourceAcquisitionJob) -> None:
        if job.status != "queued":
            raise ValueError("new source acquisition jobs must be queued")
        with self.sessions.begin() as session:
            if session.get(SourceAcquisitionJobRow, job.id) is not None:
                raise ValueError("source acquisition job id already exists")
            conflict = session.scalar(
                select(SourceAcquisitionJobRow).where(
                    SourceAcquisitionJobRow.idempotency_key == job.idempotency_key
                )
            )
            if conflict is not None:
                raise ValueError("source acquisition idempotency key already exists")
            if (
                session.get(
                    SourceDefinitionRow,
                    (job.source_id, job.source_version),
                )
                is None
            ):
                raise ValueError("pinned source definition does not exist")
            session.add(
                SourceAcquisitionJobRow(
                    id=job.id,
                    source_id=job.source_id,
                    source_version=job.source_version,
                    status=job.status,
                    idempotency_key=job.idempotency_key,
                    requested_by=job.requested_by,
                    document=job.model_dump(mode="json", by_alias=True),
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                )
            )
            session.add(
                OutboxRow(
                    id=str(uuid4()),
                    topic="source.acquisition.requested",
                    aggregate_id=job.id,
                    payload={
                        "jobId": job.id,
                        "sourceId": job.source_id,
                        "sourceVersion": job.source_version,
                        "url": job.url,
                        "maintenanceWorkItemId": job.maintenance_work_item_id,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

    def get_source_acquisition_job(
        self,
        job_id: str,
    ) -> SourceAcquisitionJob | None:
        with self.sessions() as session:
            row = session.get(SourceAcquisitionJobRow, job_id)
            return SourceAcquisitionJob.model_validate(row.document) if row else None

    def get_source_acquisition_job_by_idempotency(
        self,
        idempotency_key: str,
    ) -> SourceAcquisitionJob | None:
        with self.sessions() as session:
            row = session.scalar(
                select(SourceAcquisitionJobRow).where(
                    SourceAcquisitionJobRow.idempotency_key == idempotency_key
                )
            )
            return SourceAcquisitionJob.model_validate(row.document) if row else None

    def list_source_acquisition_jobs(
        self,
        *,
        source_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceAcquisitionJob]:
        with self.sessions() as session:
            statement = select(SourceAcquisitionJobRow).order_by(
                SourceAcquisitionJobRow.created_at.desc()
            )
            if source_id is not None:
                statement = statement.where(SourceAcquisitionJobRow.source_id == source_id)
            rows = session.scalars(statement.limit(limit)).all()
            return [SourceAcquisitionJob.model_validate(row.document) for row in rows]

    def count_source_acquisition_jobs(
        self,
        *,
        statuses: set[str] | None = None,
    ) -> int:
        with self.sessions() as session:
            statement = select(func.count()).select_from(SourceAcquisitionJobRow)
            if statuses:
                statement = statement.where(SourceAcquisitionJobRow.status.in_(statuses))
            return int(session.scalar(statement) or 0)

    def save_source_acquisition_job(
        self,
        job: SourceAcquisitionJob,
    ) -> None:
        with self.sessions.begin() as session:
            row = session.get(SourceAcquisitionJobRow, job.id)
            if row is None:
                raise ValueError("source acquisition job does not exist")
            existing = SourceAcquisitionJob.model_validate(row.document)
            immutable_fields = (
                "source_id",
                "source_version",
                "url",
                "requested_by",
                "idempotency_key",
                "maintenance_work_item_id",
                "created_at",
            )
            if any(getattr(existing, field) != getattr(job, field) for field in immutable_fields):
                raise ValueError("source acquisition job identity is immutable")
            allowed = {
                "queued": {"queued", "dispatched", "canceled"},
                "dispatched": {"dispatched", "running", "failed", "canceled"},
                "running": {"running", "completed", "failed", "canceled"},
                "completed": {"completed"},
                "failed": {"failed", "dispatched", "canceled"},
                "canceled": {"canceled"},
            }
            if job.status not in allowed[existing.status]:
                raise ValueError(
                    f"invalid source acquisition transition: {existing.status} -> {job.status}"
                )
            row.status = job.status
            row.document = job.model_dump(mode="json", by_alias=True)
            row.updated_at = job.updated_at

    def get_source_snapshot(self, snapshot_id: str) -> SourceSnapshot | None:
        with self.sessions() as session:
            row = session.get(SourceSnapshotRow, snapshot_id)
            return SourceSnapshot.model_validate(row.document) if row else None

    def list_source_snapshots(
        self,
        *,
        source_id: str | None = None,
    ) -> list[SourceSnapshot]:
        with self.sessions() as session:
            statement = select(SourceSnapshotRow).order_by(SourceSnapshotRow.retrieved_at.desc())
            if source_id is not None:
                statement = statement.where(SourceSnapshotRow.source_id == source_id)
            rows = session.scalars(statement).all()
            return [SourceSnapshot.model_validate(row.document) for row in rows]

    def save_extraction_result(
        self,
        batch: ExtractionBatch,
        candidates: list[ExtractionCandidate],
    ) -> None:
        if batch.candidate_count != len(candidates):
            raise ValueError("extraction batch candidate count does not match candidates")
        if batch.candidate_ids != [candidate.id for candidate in candidates]:
            raise ValueError("extraction batch candidate ids do not match candidates")
        if any(
            candidate.batch_id != batch.id
            or candidate.snapshot_id != batch.snapshot_id
            or candidate.source_id != batch.source_id
            for candidate in candidates
        ):
            raise ValueError("extraction candidates do not belong to the batch")
        with self.sessions.begin() as session:
            batch_document = batch.model_dump(mode="json", by_alias=True)
            row = session.get(ExtractionBatchRow, batch.id)
            if row is not None and row.document != batch_document:
                raise ValueError("extraction batch ids are immutable")
            if row is None:
                if session.get(SourceSnapshotRow, batch.snapshot_id) is None:
                    raise ValueError("extraction snapshot does not exist")
                session.add(
                    ExtractionBatchRow(
                        id=batch.id,
                        snapshot_id=batch.snapshot_id,
                        source_id=batch.source_id,
                        parser_id=batch.parser_id,
                        parser_version=batch.parser_version,
                        status=batch.status,
                        document=batch_document,
                        created_at=batch.created_at,
                    )
                )
                session.add(
                    OutboxRow(
                        id=str(uuid4()),
                        topic="source.extraction.completed",
                        aggregate_id=batch.id,
                        payload={
                            "batchId": batch.id,
                            "snapshotId": batch.snapshot_id,
                            "sourceId": batch.source_id,
                            "parserId": batch.parser_id,
                            "parserVersion": batch.parser_version,
                        },
                        occurred_at=datetime.now(UTC),
                    )
                )
            for candidate in candidates:
                document = candidate.model_dump(mode="json", by_alias=True)
                candidate_row = session.get(ExtractionCandidateRow, candidate.id)
                if candidate_row is not None:
                    existing = ExtractionCandidate.model_validate(candidate_row.document)
                    immutable_fields = (
                        "batch_id",
                        "snapshot_id",
                        "source_id",
                        "source_version",
                        "parser_id",
                        "parser_version",
                        "entity_type_id",
                        "external_id",
                        "labels",
                        "fields",
                        "citation",
                        "record_locator",
                        "created_at",
                    )
                    if any(
                        getattr(existing, field) != getattr(candidate, field)
                        for field in immutable_fields
                    ):
                        raise ValueError("extraction candidate ids are immutable")
                if candidate_row is None:
                    session.add(
                        ExtractionCandidateRow(
                            id=candidate.id,
                            batch_id=candidate.batch_id,
                            snapshot_id=candidate.snapshot_id,
                            source_id=candidate.source_id,
                            entity_type_id=candidate.entity_type_id,
                            status=candidate.status,
                            document=document,
                            created_at=candidate.created_at,
                        )
                    )

    def record_extraction_resolution(
        self,
        candidate_id: str,
        matches: list[EntityResolutionMatch],
        *,
        resolved_entity_id: str | None = None,
    ) -> ExtractionCandidate:
        with self.sessions.begin() as session:
            row = session.get(ExtractionCandidateRow, candidate_id)
            if row is None:
                raise ValueError("extraction candidate does not exist")
            candidate = ExtractionCandidate.model_validate(row.document)
            if candidate.status not in {"parsed", "resolved"}:
                raise ValueError(f"candidate cannot be resolved from status {candidate.status}")
            if candidate.entity_id and resolved_entity_id:
                if candidate.entity_id != resolved_entity_id:
                    raise ValueError("candidate entity resolution is immutable")
            if resolved_entity_id:
                entity_row = session.get(KnowledgeEntityRow, resolved_entity_id)
                if entity_row is None:
                    raise ValueError("resolved entity does not exist")
                if entity_row.entity_type_id != candidate.entity_type_id:
                    raise ValueError("resolved entity type does not match candidate")
                if resolved_entity_id not in {match.entity_id for match in matches}:
                    raise ValueError("resolved entity must be present in resolution matches")
            updated = candidate.model_copy(
                update={
                    "entity_id": resolved_entity_id,
                    "status": "resolved" if resolved_entity_id else "parsed",
                    "resolution_matches": matches,
                }
            )
            row.status = updated.status
            row.document = updated.model_dump(mode="json", by_alias=True)
            return updated

    def mark_extraction_candidate_scheduled(
        self,
        candidate_id: str,
        schedule_ids: list[str],
    ) -> ExtractionCandidate:
        if not schedule_ids:
            raise ValueError("scheduled candidate requires at least one schedule")
        with self.sessions.begin() as session:
            row = session.get(ExtractionCandidateRow, candidate_id)
            if row is None:
                raise ValueError("extraction candidate does not exist")
            candidate = ExtractionCandidate.model_validate(row.document)
            if candidate.status not in {"parsed", "resolved", "scheduled"}:
                raise ValueError(f"candidate cannot be scheduled from status {candidate.status}")
            merged_schedule_ids = sorted(set(candidate.schedule_ids).union(schedule_ids))
            updated = candidate.model_copy(
                update={
                    "status": "scheduled",
                    "schedule_ids": merged_schedule_ids,
                }
            )
            row.status = updated.status
            row.document = updated.model_dump(mode="json", by_alias=True)
            return updated

    def get_extraction_batch(self, batch_id: str) -> ExtractionBatch | None:
        with self.sessions() as session:
            row = session.get(ExtractionBatchRow, batch_id)
            return ExtractionBatch.model_validate(row.document) if row else None

    def list_extraction_batches(
        self,
        *,
        source_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> list[ExtractionBatch]:
        with self.sessions() as session:
            statement = select(ExtractionBatchRow).order_by(ExtractionBatchRow.created_at.desc())
            if source_id is not None:
                statement = statement.where(ExtractionBatchRow.source_id == source_id)
            if snapshot_id is not None:
                statement = statement.where(ExtractionBatchRow.snapshot_id == snapshot_id)
            rows = session.scalars(statement).all()
            return [ExtractionBatch.model_validate(row.document) for row in rows]

    def get_extraction_candidate(
        self,
        candidate_id: str,
    ) -> ExtractionCandidate | None:
        with self.sessions() as session:
            row = session.get(ExtractionCandidateRow, candidate_id)
            return ExtractionCandidate.model_validate(row.document) if row else None

    def list_extraction_candidates(
        self,
        *,
        batch_id: str | None = None,
        source_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ExtractionCandidate]:
        with self.sessions() as session:
            statement = select(ExtractionCandidateRow).order_by(
                ExtractionCandidateRow.created_at.desc(),
                ExtractionCandidateRow.id,
            )
            if batch_id is not None:
                statement = statement.where(ExtractionCandidateRow.batch_id == batch_id)
            if source_id is not None:
                statement = statement.where(ExtractionCandidateRow.source_id == source_id)
            if status is not None:
                statement = statement.where(ExtractionCandidateRow.status == status)
            rows = session.scalars(statement.limit(limit)).all()
            return [ExtractionCandidate.model_validate(row.document) for row in rows]

    def append_audit_event(
        self,
        *,
        actor: Principal,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        request_id: str,
        metadata: dict[str, object] | None = None,
    ) -> AuditEvent:
        with self.sessions.begin() as session:
            previous = session.scalar(
                select(AuditEventRow)
                .order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc())
                .limit(1)
                .with_for_update()
            )
            event = build_audit_event(
                event_id=f"audit-{uuid4()}",
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                request_id=request_id,
                metadata=metadata,
                previous_hash=previous.event_hash if previous else None,
            )
            session.add(
                AuditEventRow(
                    id=event.id,
                    actor_id=event.actor.subject,
                    action=event.action,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    outcome=event.outcome,
                    event_hash=event.event_hash,
                    previous_hash=event.previous_hash,
                    document=event.model_dump(mode="json", by_alias=True),
                    occurred_at=event.occurred_at,
                )
            )
            return event

    def list_audit_events(self, *, limit: int = 100) -> list[AuditEvent]:
        with self.sessions() as session:
            rows = session.scalars(
                select(AuditEventRow)
                .order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc())
                .limit(limit)
            ).all()
            return [AuditEvent.model_validate(row.document) for row in rows]

    def audit_event_count(self) -> int:
        with self.sessions() as session:
            return int(session.scalar(select(func.count()).select_from(AuditEventRow)) or 0)

    def verify_audit_chain(self) -> bool:
        with self.sessions() as session:
            rows = session.scalars(
                select(AuditEventRow).order_by(
                    AuditEventRow.occurred_at,
                    AuditEventRow.id,
                )
            ).all()
            previous_hash: str | None = None
            for row in rows:
                event = AuditEvent.model_validate(row.document)
                if event.previous_hash != previous_hash:
                    return False
                if (
                    audit_event_hash(event.model_dump(mode="json", by_alias=True))
                    != event.event_hash
                ):
                    return False
                previous_hash = event.event_hash
            return True

    def outbox_size(self, session: Session | None = None) -> int:
        own_session = session is None
        active_session = session or self.sessions()
        try:
            return len(active_session.scalars(select(OutboxRow)).all())
        finally:
            if own_session:
                active_session.close()

    def pending_outbox_ids(
        self,
        *,
        topic: str | None = None,
        aggregate_id: str | None = None,
        limit: int = 1000,
    ) -> list[str]:
        return [
            event.id
            for event in self.pending_outbox_records(
                topic=topic,
                aggregate_id=aggregate_id,
                limit=limit,
            )
        ]

    def pending_outbox_records(
        self,
        *,
        topic: str | None = None,
        aggregate_id: str | None = None,
        limit: int = 1000,
    ) -> list[OutboxRecord]:
        with self.sessions() as session:
            statement = select(OutboxRow).where(OutboxRow.published_at.is_(None))
            if topic:
                statement = statement.where(OutboxRow.topic == topic)
            if aggregate_id:
                statement = statement.where(OutboxRow.aggregate_id == aggregate_id)
            statement = statement.order_by(OutboxRow.occurred_at).limit(limit)
            return [
                OutboxRecord(
                    id=row.id,
                    topic=row.topic,
                    aggregate_id=row.aggregate_id,
                    payload=dict(row.payload),
                    occurred_at=row.occurred_at,
                )
                for row in session.scalars(statement).all()
            ]

    def mark_outbox_published(self, event_ids: list[str]) -> None:
        if not event_ids:
            return
        with self.sessions.begin() as session:
            rows = session.scalars(select(OutboxRow).where(OutboxRow.id.in_(event_ids))).all()
            published_at = datetime.now(UTC)
            for row in rows:
                row.published_at = published_at
                row.error = None

    def mark_outbox_failed(self, event_ids: list[str], error: str) -> None:
        if not event_ids:
            return
        with self.sessions.begin() as session:
            rows = session.scalars(select(OutboxRow).where(OutboxRow.id.in_(event_ids))).all()
            for row in rows:
                row.error = error[:2000]

    def requeue_outbox_event(
        self,
        *,
        topic: str,
        aggregate_id: str,
        payload: dict[str, object],
    ) -> str:
        """Create or reactivate one durable event for an explicit replay."""
        with self.sessions.begin() as session:
            row = session.scalar(
                select(OutboxRow)
                .where(
                    OutboxRow.topic == topic,
                    OutboxRow.aggregate_id == aggregate_id,
                )
                .order_by(OutboxRow.occurred_at.desc())
            )
            if row is None:
                row = OutboxRow(
                    id=str(uuid4()),
                    topic=topic,
                    aggregate_id=aggregate_id,
                    payload=payload,
                    occurred_at=datetime.now(UTC),
                )
                session.add(row)
            else:
                row.payload = payload
                row.occurred_at = datetime.now(UTC)
                row.published_at = None
                row.error = None
            return row.id
