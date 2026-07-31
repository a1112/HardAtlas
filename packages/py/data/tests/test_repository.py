from datetime import UTC, datetime

import pytest
from hardatlas_data import KnowledgeRepository, ProposalConcurrencyError
from hardatlas_domain import (
    AgentProposal,
    ChangeOperation,
    GovernanceService,
    KnowledgeEntity,
    KnowledgeSpace,
    LocalizedText,
    SavedCollection,
    SavedCollectionItem,
    SourceAcquisitionJob,
    SourceDefinition,
    TaxonomyNode,
)
from sqlalchemy import create_engine


def entity(revision_id: str, description: str) -> KnowledgeEntity:
    return KnowledgeEntity.model_validate(
        {
            "ref": {
                "id": "entity-snow-leopard",
                "slug": "snow-leopard",
                "typeId": "type-animal",
                "canonicalName": "雪豹",
            },
            "names": [{"locale": "zh-CN", "value": "雪豹"}],
            "aliases": [{"locale": "la", "value": "Panthera uncia"}],
            "description": [{"locale": "zh-CN", "value": description}],
            "taxonomyNodeIds": ["tax-animals"],
            "claims": [],
            "sections": [],
            "relationships": [],
            "citations": [],
            "revision": {
                "revisionId": revision_id,
                "dataVersion": revision_id,
                "schemaVersion": "schema-2",
                "policyVersion": "policy-1",
                "createdAt": datetime(2026, 7, 28, tzinfo=UTC),
            },
            "publicationStatus": "published",
        }
    )


def source_definition() -> SourceDefinition:
    return SourceDefinition.model_validate(
        {
            "id": "test-source-replay",
            "version": "1.0.0",
            "name": "Replay source",
            "kind": "dataset",
            "baseUrl": "https://knowledge.example.org/records.json",
            "allowedHosts": ["knowledge.example.org"],
            "trustTier": "secondary",
            "licenseId": "CC-BY-4.0",
            "licenseStatus": "allowed",
            "robotsPolicy": "not-applicable",
            "robotsStatus": "allowed",
            "allowedMediaTypes": ["application/json"],
            "status": "active",
        }
    )


def test_requeue_outbox_event_reuses_existing_record() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_source_definition(source_definition())
    repository.create_source_acquisition_job(
        SourceAcquisitionJob(
            id="acq-replay-test",
            source_id="test-source-replay",
            source_version="1.0.0",
            status="queued",
            requested_by="worker",
            idempotency_key="acq-replay-test-key",
            url="https://knowledge.example.org/records.json",
        )
    )

    created = repository.pending_outbox_records(
        topic="source.acquisition.requested",
        aggregate_id="acq-replay-test",
    )
    assert len(created) == 1
    created_id = created[0].id
    assert created_id

    replay_event_id = repository.requeue_outbox_event(
        topic="source.acquisition.requested",
        aggregate_id="acq-replay-test",
        payload={
            "jobId": "acq-replay-test",
            "sourceId": "test-source-replay",
            "sourceVersion": "1.0.0",
            "url": "https://knowledge.example.org/records.json",
        },
    )
    pending = repository.pending_outbox_records(
        topic="source.acquisition.requested",
        aggregate_id="acq-replay-test",
    )
    assert len(pending) == 1
    assert pending[0].id == created_id == replay_event_id
    assert pending[0].payload["url"] == "https://knowledge.example.org/records.json"

    same_replay_event_id = repository.requeue_outbox_event(
        topic="source.acquisition.requested",
        aggregate_id="acq-replay-test",
        payload={
            "jobId": "acq-replay-test",
            "sourceId": "test-source-replay",
            "sourceVersion": "1.0.0",
            "url": "https://knowledge.example.org/records.json?retry=true",
            "maintenanceWorkItemId": "work-item-replay-1",
        },
    )
    second = repository.pending_outbox_records(
        topic="source.acquisition.requested",
        aggregate_id="acq-replay-test",
    )
    assert same_replay_event_id == replay_event_id
    assert len(second) == 1
    assert second[0].id == replay_event_id
    assert second[0].payload["url"] == "https://knowledge.example.org/records.json?retry=true"
    assert second[0].payload["maintenanceWorkItemId"] == "work-item-replay-1"


def test_requeue_outbox_event_creates_record_when_missing() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    event_id = repository.requeue_outbox_event(
        topic="source.acquisition.requested",
        aggregate_id="acq-missing-outbox",
        payload={
            "jobId": "acq-missing-outbox",
            "sourceId": "missing-source",
            "sourceVersion": "1.0.0",
            "url": "https://knowledge.example.org/records.json",
            "maintenanceWorkItemId": "work-item-missing",
        },
    )

    pending = repository.pending_outbox_records(
        topic="source.acquisition.requested",
        aggregate_id="acq-missing-outbox",
    )
    assert len(pending) == 1
    assert pending[0].id == event_id
    assert pending[0].payload["jobId"] == "acq-missing-outbox"
    assert pending[0].payload["maintenanceWorkItemId"] == "work-item-missing"


def test_entity_revision_switch_is_transactional_and_outboxed() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_entity(entity("revision-1", "first"))
    assert repository.get_entity("snow-leopard").description[0].value == "first"
    repository.save_entity(entity("revision-2", "second"))
    assert repository.get_entity("snow-leopard").description[0].value == "second"
    first = repository.get_entity_revision("snow-leopard", "revision-1")
    assert first is not None
    assert first.description[0].value == "first"
    assert [
        item.revision.revision_id
        for item in repository.list_entity_revisions("entity-snow-leopard")
    ] == ["revision-2", "revision-1"]
    assert (
        repository.get_entity_revision("snow-leopard", "revision-missing")
        is None
    )
    assert repository.outbox_size() == 2


def test_revision_content_is_immutable() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_entity(entity("revision-1", "first"))
    with pytest.raises(ValueError, match="immutable"):
        repository.save_entity(entity("revision-1", "changed under same id"))


def test_space_and_taxonomy_documents_round_trip() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_space(
        KnowledgeSpace(
            id="space-life",
            slug="life",
            name=[LocalizedText(locale="zh-CN", value="生命科学")],
            description=[LocalizedText(locale="zh-CN", value="生命知识")],
            root_taxonomy_node_ids=["tax-animals"],
            icon_key="life",
            status="published",
        )
    )
    repository.save_taxonomy_node(
        TaxonomyNode(
            id="tax-animals",
            space_id="space-life",
            slug="animals",
            name=[LocalizedText(locale="zh-CN", value="动物")],
            parent_ids=[],
            child_count=0,
            entity_count=1,
            path_keys=["life", "animals"],
        )
    )
    assert repository.list_spaces()[0].slug == "life"
    assert repository.list_taxonomy_nodes("space-life")[0].slug == "animals"


def test_saved_collections_are_workspace_scoped_and_pin_entity_version() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_entity(entity("revision-1", "first"))
    workspace_a = "00000000-0000-0000-0000-000000000001"
    workspace_b = "00000000-0000-0000-0000-000000000002"
    repository.ensure_workspace(workspace_a, "A")
    repository.ensure_workspace(workspace_b, "B")
    collection = SavedCollection(
        id="collection-a",
        workspace_id=workspace_a,
        name="高山动物",
        created_by="user-a",
    )
    repository.save_collection(collection)
    item = SavedCollectionItem(
        collection_id=collection.id,
        workspace_id=workspace_a,
        entity_id="entity-snow-leopard",
        entity_ref=entity("revision-1", "first").ref,
        entity_revision_id="revision-1",
        data_version="revision-1",
        tags=["动物", "高山"],
        saved_by="user-a",
    )
    repository.save_collection_item(item)

    restored_collection = repository.get_collection(workspace_a, collection.id)
    assert restored_collection is not None
    assert restored_collection.id == collection.id
    assert restored_collection.workspace_id == workspace_a
    assert restored_collection.updated_at >= collection.updated_at
    assert repository.list_collections(workspace_b) == []
    assert repository.list_collection_items(workspace_a, collection.id)[
        0
    ].entity_revision_id == "revision-1"
    with pytest.raises(ValueError, match="not found in workspace"):
        repository.save_collection_item(
            item.model_copy(update={"workspace_id": workspace_b})
        )
    assert repository.remove_collection_item(
        workspace_a,
        collection.id,
        item.entity_id,
    )
    assert repository.list_collection_items(workspace_a, collection.id) == []


def test_schema_activation_survives_bootstrap_and_can_roll_back_by_version() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_schema_document(
        document_id="attr-test",
        schema_version="schema-1",
        kind="attribute-definition",
        document={"id": "attr-test", "schemaVersion": "schema-1"},
    )
    repository.save_schema_document(
        document_id="attr-test",
        schema_version="schema-2",
        kind="attribute-definition",
        document={"id": "attr-test", "schemaVersion": "schema-2"},
    )
    repository.save_schema_document(
        document_id="attr-test",
        schema_version="schema-1",
        kind="attribute-definition",
        document={"id": "attr-test", "schemaVersion": "schema-1"},
        preserve_activation=True,
    )
    assert repository.get_schema_document(
        document_id="attr-test",
        kind="attribute-definition",
    ) == {"id": "attr-test", "schemaVersion": "schema-2"}
    assert repository.get_schema_document(
        document_id="attr-test",
        kind="attribute-definition",
        schema_version="schema-1",
    ) == {"id": "attr-test", "schemaVersion": "schema-1"}


def test_governed_proposal_and_release_round_trip() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    proposal = AgentProposal(
        id="proposal-1",
        entity_id="entity-snow-leopard",
        proposal_type="content",
        operations=[
            ChangeOperation(
                operation="replace",
                path="/description",
                before="old",
                after="new",
                citation_ids=["citation-1"],
                confidence=0.98,
            )
        ],
        risk="low",
        status="proposed",
        agent_run_id="run-1",
        impact={"entityCount": 1},
    )
    governance = GovernanceService([proposal], "policy-1")
    governed = governance.evaluate("proposal-1")
    repository.save_governed_proposal(governed)
    restored = repository.get_governed_proposal("proposal-1")
    assert restored
    assert governed.version == 1
    assert restored.version == 1
    assert restored.proposal.status == "policy-approved"

    governance.accept_policy_approved("proposal-1")
    release = governance.stage_release(
        release_id="release-1",
        proposal_ids=["proposal-1"],
        data_version="data-1",
        schema_versions=["schema-2"],
        previous_release_id="release-0",
    )
    repository.save_release(release)


def test_governed_proposal_rejects_stale_multi_agent_state() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    proposal = AgentProposal(
        id="proposal-concurrent",
        entity_id="entity-snow-leopard",
        proposal_type="content",
        operations=[
            ChangeOperation(
                operation="replace",
                path="/description",
                before="old",
                after="new",
                citation_ids=["citation-1"],
                confidence=0.98,
            )
        ],
        risk="low",
        status="proposed",
        agent_run_id="run-concurrent",
        impact={"entityCount": 1},
    )
    governed = GovernanceService([proposal], "policy-1").list_proposals()[0]
    repository.save_governed_proposal(governed)
    first_agent = repository.get_governed_proposal(proposal.id)
    second_agent = repository.get_governed_proposal(proposal.id)
    assert first_agent is not None
    assert second_agent is not None

    first_agent.proposal.status = "policy-blocked"
    repository.save_governed_proposal(first_agent)
    assert first_agent.version == 2

    second_agent.proposal.status = "policy-approved"
    with pytest.raises(
        ProposalConcurrencyError,
        match="expected version 1, actual version 2",
    ):
        repository.save_governed_proposal(second_agent)

    persisted = repository.get_governed_proposal(proposal.id)
    assert persisted is not None
    assert persisted.version == 2
    assert persisted.proposal.status == "policy-blocked"
