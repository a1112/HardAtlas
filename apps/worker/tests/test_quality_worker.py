import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from hardatlas_ai import load_agent_pack
from hardatlas_data import KnowledgeRepository
from hardatlas_domain import (
    KnowledgeEntity,
    QualityProfile,
    SourceDefinition,
    SourceSnapshot,
)
from hardatlas_ingestion import AcquisitionError
from hardatlas_worker.tasks import (
    activate_maintenance_work_once,
    assess_knowledge_quality_once,
    dispatch_maintenance_work_events,
    dispatch_source_acquisition_events,
    dispatch_quality_maintenance_events,
    execute_agent_schedule_once,
    execute_source_acquisition_once,
    request_source_acquisition_for_work_once,
    schedule_quality_maintenance_task_once,
)
from sqlalchemy import create_engine


def repository() -> KnowledgeRepository:
    result = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    result.create_schema()
    return result


def test_worker_quality_scan_uses_active_profile_and_audits_result() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    profile = QualityProfile.model_validate(
        {
            "id": "quality-plant",
            "entityTypeId": "type-plant",
            "schemaVersion": "1.0.0",
            "requiredLocales": ["zh-CN"],
            "requiredAttributeIds": ["attr-scientific-name"],
            "minimumCitations": 1,
            "minimumSections": 1,
        }
    )
    repository.save_schema_document(
        document_id=profile.id,
        schema_version=profile.schema_version,
        kind="quality-profile",
        document=profile.model_dump(mode="json", by_alias=True),
    )
    repository.save_entity(
        KnowledgeEntity.model_validate(
            {
                "ref": {
                    "id": "entity-ginkgo",
                    "slug": "ginkgo",
                    "typeId": "type-plant",
                    "canonicalName": "银杏",
                },
                "names": [{"locale": "zh-CN", "value": "银杏"}],
                "aliases": [],
                "description": [{"locale": "zh-CN", "value": "古老树种"}],
                "taxonomyNodeIds": [],
                "claims": [],
                "sections": [],
                "relationships": [],
                "citations": [],
                "revision": {
                    "revisionId": "revision-ginkgo-1",
                    "dataVersion": "data-1",
                    "schemaVersion": "1.0.0",
                    "policyVersion": "1.0.0",
                    "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
                },
                "publicationStatus": "published",
            }
        )
    )

    result = assess_knowledge_quality_once(
        repository,
        assessed_at=datetime(2026, 7, 29, 12, tzinfo=UTC),
    )

    assert len(result.assessed) == 1
    assert result.open_task_count == 4
    assert next(
        event for event in repository.list_audit_events() if event.action == "quality.scan"
    ).action == "quality.scan"
    assert repository.verify_audit_chain()

    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = next(item for item in loaded_pack.graphs if item.id == "quality-maintenance-triage")
    dispatched = dispatch_quality_maintenance_events(repository, graph)
    assert dispatched == {"scheduled": 4, "skipped": 0, "failed": 0}
    assert repository.list_maintenance_tasks(status="open") == []
    scheduled_tasks = repository.list_maintenance_tasks(status="scheduled")
    assert len(scheduled_tasks) == 4
    assert len(repository.pending_outbox_ids(topic="agent.graph.scheduled")) == 4

    first_task = scheduled_tasks[0]
    repeated = schedule_quality_maintenance_task_once(
        repository,
        graph,
        first_task.id,
    )
    assert repeated
    assert repeated.id == first_task.agent_graph_schedule_id
    completed = execute_agent_schedule_once(
        repository,
        loaded_pack.registry(),
        repeated.id,
    )
    assert completed.status == "completed"
    run = repository.get_agent_graph_run(completed.run_id or "")
    assert run
    assert run["proposalIds"] == []
    assert run["usage"]["modelCalls"] == 0
    assert run["nodes"]["triage"]["output"]["requiresEvidence"] is True
    assert run["nodes"]["triage"]["output"]["proposalEligible"] is False
    assert repository.list_governed_proposals() == []
    work_items = repository.list_maintenance_work_items()
    assert len(work_items) == 1
    work_item = work_items[0]
    assert work_item.status == "queued"
    assert work_item.maintenance_task_id == first_task.id
    assert work_item.revision_id == "revision-ginkgo-1"
    assert work_item.requires_evidence is True
    assert work_item.proposal_eligible is False
    assert repository.pending_outbox_ids(topic="maintenance.work.requested")

    enqueued_work: list[str] = []
    assert dispatch_maintenance_work_events(
        repository,
        enqueued_work.append,
    ) == {"dispatched": 1, "failed": 0}
    assert enqueued_work == [work_item.id]
    ready = activate_maintenance_work_once(repository, work_item.id)
    assert ready.status == "ready"

    claim_time = datetime(2026, 7, 29, 14, tzinfo=UTC)
    claimed = repository.claim_maintenance_work_item(
        work_item.id,
        assignee_id="agent-a",
        lease_seconds=60,
        claimed_at=claim_time,
    )
    assert claimed.status == "claimed"
    assert claimed.attempt == 1
    repeated_claim = repository.claim_maintenance_work_item(
        work_item.id,
        assignee_id="agent-a",
        lease_seconds=60,
        claimed_at=claim_time,
    )
    assert repeated_claim.lease_token == claimed.lease_token
    with pytest.raises(ValueError, match="another assignee"):
        repository.claim_maintenance_work_item(
            work_item.id,
            assignee_id="agent-b",
            lease_seconds=60,
            claimed_at=claim_time,
        )
    reclaimed = repository.claim_maintenance_work_item(
        work_item.id,
        assignee_id="agent-b",
        lease_seconds=60,
        claimed_at=datetime(2026, 7, 29, 14, 2, tzinfo=UTC),
    )
    assert reclaimed.assignee_id == "agent-b"
    assert reclaimed.attempt == 2
    assert reclaimed.lease_token != claimed.lease_token
    current = repository.get_entity_by_id("entity-ginkgo")
    assert current
    revised = current.model_copy(deep=True)
    revised.revision = revised.revision.model_copy(
        update={
            "revision_id": "revision-ginkgo-2",
            "data_version": "data-2",
            "created_at": datetime(2026, 7, 29, 14, 3, tzinfo=UTC),
        }
    )
    repository.save_entity(revised)
    superseded = repository.claim_maintenance_work_item(
        work_item.id,
        assignee_id="agent-c",
        lease_seconds=60,
        claimed_at=datetime(2026, 7, 29, 14, 4, tzinfo=UTC),
    )
    assert superseded.status == "superseded"
    task_after_revision = repository.get_maintenance_task(first_task.id)
    assert task_after_revision
    assert task_after_revision.status == "superseded"
    assert dispatch_quality_maintenance_events(repository, graph) == {
        "scheduled": 0,
        "skipped": 0,
        "failed": 0,
    }


def test_quality_dispatch_retains_invalid_event_for_repair() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    event_id = repository.requeue_outbox_event(
        topic="quality.maintenance.requested",
        aggregate_id="entity-missing",
        payload={"taskId": "task-missing"},
    )
    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = next(item for item in loaded_pack.graphs if item.id == "quality-maintenance-triage")

    assert dispatch_quality_maintenance_events(repository, graph) == {
        "scheduled": 0,
        "skipped": 0,
        "failed": 1,
    }
    assert repository.pending_outbox_ids(topic="quality.maintenance.requested") == [event_id]


def test_source_maintenance_route_creates_evidence_and_completes_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    profile = QualityProfile(
        id="quality-source-route",
        entity_type_id="type-plant",
        schema_version="1.0.0",
        minimum_citations=1,
        minimum_sections=0,
    )
    repository.save_schema_document(
        document_id=profile.id,
        schema_version=profile.schema_version,
        kind="quality-profile",
        document=profile.model_dump(mode="json", by_alias=True),
    )
    repository.save_entity(
        KnowledgeEntity.model_validate(
            {
                "ref": {
                    "id": "entity-source-route",
                    "slug": "source-route",
                    "typeId": "type-plant",
                    "canonicalName": "证据采集测试条目",
                },
                "names": [{"locale": "zh-CN", "value": "证据采集测试条目"}],
                "aliases": [],
                "description": [],
                "taxonomyNodeIds": ["taxonomy-plants"],
                "claims": [],
                "sections": [],
                "relationships": [],
                "citations": [],
                "revision": {
                    "revisionId": "revision-source-route-1",
                    "dataVersion": "data-source-route-1",
                    "schemaVersion": "1.0.0",
                    "policyVersion": "1.0.0",
                    "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
                },
                "publicationStatus": "published",
            }
        )
    )
    source = SourceDefinition(
        id="source-plant-authority",
        version="1.0.0",
        name="Plant authority fixture",
        kind="api",
        base_url="https://plants.example.org/records",
        allowed_hosts=["plants.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        entity_type_ids=["type-plant"],
        taxonomy_node_ids=["taxonomy-plants"],
        status="active",
    )
    repository.save_source_definition(source)
    assess_knowledge_quality_once(
        repository,
        assessed_at=datetime(2026, 7, 29, 12, tzinfo=UTC),
    )
    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = next(item for item in loaded_pack.graphs if item.id == "quality-maintenance-triage")
    assert dispatch_quality_maintenance_events(repository, graph) == {
        "scheduled": 1,
        "skipped": 0,
        "failed": 0,
    }
    schedule = repository.list_agent_graph_schedules(graph_id=graph.id)[0]
    execute_agent_schedule_once(
        repository,
        loaded_pack.registry(),
        schedule.id,
    )
    work_item = repository.list_maintenance_work_items()[0]
    activated = activate_maintenance_work_once(repository, work_item.id)
    assert activated.status == "ready"

    routed, job = request_source_acquisition_for_work_once(
        repository,
        work_item.id,
        requested_at=datetime(2026, 7, 29, 12, 1, tzinfo=UTC),
    )
    assert routed.status == "ready"
    assert job is not None
    assert job.source_id == source.id
    assert job.source_version == source.version
    assert job.maintenance_work_item_id == work_item.id
    assert job.url == source.base_url
    assert repository.pending_outbox_ids(topic="source.acquisition.requested")
    route_audit = next(
        event
        for event in reversed(repository.list_audit_events())
        if event.action == "maintenance.source.route" and event.resource_id == work_item.id
    )
    assert route_audit.action == "maintenance.source.route"
    assert route_audit.outcome == "success"
    assert route_audit.metadata["sourceId"] == source.id
    assert route_audit.metadata["sourceVersion"] == source.version
    assert route_audit.metadata["acquisitionJobId"] == job.id
    assert route_audit.metadata["revisionId"] == work_item.revision_id

    content = b'{"records":[]}'
    digest = hashlib.sha256(content).hexdigest()
    snapshot = SourceSnapshot(
        id=f"snapshot-{source.id}-{digest[:16]}",
        source_id=source.id,
        source_version=source.version,
        url=source.base_url,
        content_sha256=digest,
        storage_key=f"sources/{source.id}/{digest}",
        media_type="application/json",
        byte_size=len(content),
        http_status=200,
        license_id=source.license_id,
        capture_status="captured",
        retrieved_at=datetime(2026, 7, 29, 12, 2, tzinfo=UTC),
    )

    def fake_acquire(
        selected_source: SourceDefinition,
        *,
        url: str | None = None,
    ) -> SimpleNamespace:
        assert selected_source == source
        assert url == source.base_url
        return SimpleNamespace(snapshot=snapshot, content=content)

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        fake_acquire,
    )

    class MemoryStore:
        def put(self, *, key: str, **_: object) -> None:
            assert key == snapshot.storage_key

        def get(self, *, key: str) -> bytes:
            raise AssertionError(f"unexpected read: {key}")

    completed_job = execute_source_acquisition_once(
        repository,
        MemoryStore(),
        job.id,
    )
    assert completed_job.status == "completed"
    completed_work = repository.get_maintenance_work_item(work_item.id)
    assert completed_work is not None
    assert completed_work.status == "completed"
    assert [item.id for item in completed_work.evidence_refs] == [snapshot.id]
    assert [item.id for item in completed_work.output_refs] == [job.id]
    assert completed_work.assignee_id is None
    acquisition_audit = next(
        event
        for event in reversed(repository.list_audit_events())
        if event.action == "source.acquisition.execute" and event.resource_id == completed_job.id
    )
    assert acquisition_audit.action == "source.acquisition.execute"
    assert acquisition_audit.outcome == "success"
    assert acquisition_audit.metadata["url"] == source.base_url
    complete_audit = next(
        event
        for event in repository.list_audit_events()
        if event.action == "maintenance.source.complete"
        and event.resource_id == work_item.id
    )
    assert complete_audit.action == "maintenance.source.complete"
    assert complete_audit.outcome == "success"
    assert complete_audit.metadata["acquisitionJobId"] == completed_job.id
    assert complete_audit.metadata["snapshotId"] == snapshot.id
    assert complete_audit.metadata["revisionId"] == work_item.revision_id
    assert repository.verify_audit_chain()


def test_source_maintenance_route_failure_records_acquisition_url_audit_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_repository = repository()
    profile = QualityProfile(
        id="quality-source-route",
        entity_type_id="type-plant",
        schema_version="1.0.0",
        minimum_citations=1,
        minimum_sections=0,
    )
    local_repository.save_schema_document(
        document_id=profile.id,
        schema_version=profile.schema_version,
        kind="quality-profile",
        document=profile.model_dump(mode="json", by_alias=True),
    )
    local_repository.save_entity(
        KnowledgeEntity.model_validate(
            {
                "ref": {
                    "id": "entity-source-route",
                    "slug": "source-route",
                    "typeId": "type-plant",
                    "canonicalName": "证据采集测试条目",
                },
                "names": [{"locale": "zh-CN", "value": "证据采集测试条目"}],
                "aliases": [],
                "description": [],
                "taxonomyNodeIds": ["taxonomy-plants"],
                "claims": [],
                "sections": [],
                "relationships": [],
                "citations": [],
                "revision": {
                    "revisionId": "revision-source-route-1",
                    "dataVersion": "data-source-route-1",
                    "schemaVersion": "1.0.0",
                    "policyVersion": "1.0.0",
                    "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
                },
                "publicationStatus": "published",
            }
        )
    )
    source = SourceDefinition(
        id="source-plant-authority-failed",
        version="1.0.0",
        name="Plant authority fixture",
        kind="api",
        base_url="https://plants.example.org/records",
        allowed_hosts=["plants.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        entity_type_ids=["type-plant"],
        taxonomy_node_ids=["taxonomy-plants"],
        status="active",
    )
    local_repository.save_source_definition(source)
    assess_knowledge_quality_once(
        local_repository,
        assessed_at=datetime(2026, 7, 29, 12, tzinfo=UTC),
    )
    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = next(item for item in loaded_pack.graphs if item.id == "quality-maintenance-triage")
    assert dispatch_quality_maintenance_events(local_repository, graph) == {
        "scheduled": 1,
        "skipped": 0,
        "failed": 0,
    }
    schedule = local_repository.list_agent_graph_schedules(graph_id=graph.id)[0]
    execute_agent_schedule_once(
        local_repository,
        loaded_pack.registry(),
        schedule.id,
    )
    work_item = local_repository.list_maintenance_work_items()[0]
    activate_maintenance_work_once(local_repository, work_item.id)

    _, job = request_source_acquisition_for_work_once(
        local_repository,
        work_item.id,
        requested_at=datetime(2026, 7, 29, 12, 1, tzinfo=UTC),
    )
    assert job is not None
    route_audit = next(
        event
        for event in reversed(local_repository.list_audit_events())
        if event.action == "maintenance.source.route" and event.resource_id == work_item.id
    )
    assert route_audit.outcome == "success"

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AcquisitionError("network unreachable")
        ),
    )

    with pytest.raises(AcquisitionError):
        execute_source_acquisition_once(
            local_repository,
            MemoryStore(),
            job.id,
        )
    failed_job = local_repository.get_source_acquisition_job(job.id)
    assert failed_job is not None
    assert failed_job.status == "failed"
    failed_work = local_repository.get_maintenance_work_item(work_item.id)
    assert failed_work is not None
    assert failed_work.status == "ready"
    failed_audit = next(
        event
        for event in reversed(local_repository.list_audit_events())
        if event.action == "source.acquisition.execute" and event.resource_id == job.id
    )
    assert failed_audit.action == "source.acquisition.execute"
    assert failed_audit.outcome == "failed"
    assert failed_audit.metadata["url"] == source.base_url


def test_requeued_maintenance_work_requested_event_is_dispatched_and_reactivates_work() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    profile = QualityProfile(
        id="quality-source-route-requeue",
        entity_type_id="type-plant",
        schema_version="1.0.0",
        minimum_citations=1,
        minimum_sections=0,
    )
    repository.save_schema_document(
        document_id=profile.id,
        schema_version=profile.schema_version,
        kind="quality-profile",
        document=profile.model_dump(mode="json", by_alias=True),
    )
    repository.save_entity(
        KnowledgeEntity.model_validate(
            {
                "ref": {
                    "id": "entity-source-route-requeue",
                    "slug": "source-route-requeue",
                    "typeId": "type-plant",
                    "canonicalName": "证据采集重排测试条目",
                },
                "names": [{"locale": "zh-CN", "value": "证据采集重排测试条目"}],
                "aliases": [],
                "description": [],
                "taxonomyNodeIds": ["taxonomy-plants"],
                "claims": [],
                "sections": [],
                "relationships": [],
                "citations": [],
                "revision": {
                    "revisionId": "revision-source-route-requeue-1",
                    "dataVersion": "data-source-route-requeue-1",
                    "schemaVersion": "1.0.0",
                    "policyVersion": "1.0.0",
                    "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
                },
                "publicationStatus": "published",
            }
        )
    )
    assess_knowledge_quality_once(
        repository,
        assessed_at=datetime(2026, 7, 29, 12, tzinfo=UTC),
    )
    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = next(item for item in loaded_pack.graphs if item.id == "quality-maintenance-triage")
    assert dispatch_quality_maintenance_events(repository, graph) == {
        "scheduled": 1,
        "skipped": 0,
        "failed": 0,
    }
    schedule = repository.list_agent_graph_schedules(graph_id=graph.id)[0]
    execute_agent_schedule_once(
        repository,
        loaded_pack.registry(),
        schedule.id,
    )
    work_item = repository.list_maintenance_work_items()[0]
    activated = activate_maintenance_work_once(repository, work_item.id)
    assert activated.status == "ready"

    claim = repository.claim_maintenance_work_item(
        work_item.id,
        assignee_id="agent-a",
        lease_seconds=300,
        claimed_at=datetime.now(UTC),
    )
    blocked = repository.block_maintenance_work_item(
        work_item.id,
        assignee_id="agent-a",
        lease_token=claim.lease_token,
        reason="requires manual verification",
    )
    assert blocked.status == "blocked"
    requeued, outbox_event_id = repository.requeue_blocked_maintenance_work_item(
        work_item.id,
    )
    assert requeued.status == "queued"
    pending_before_dispatch = repository.pending_outbox_ids(topic="maintenance.work.requested")
    assert outbox_event_id in pending_before_dispatch

    dispatched = dispatch_maintenance_work_events(
        repository,
        lambda item_id: activate_maintenance_work_once(repository, item_id),
    )
    assert dispatched["dispatched"] == len(pending_before_dispatch)
    assert dispatched["failed"] == 0
    assert repository.get_maintenance_work_item(work_item.id).status == "ready"
    assert outbox_event_id not in repository.pending_outbox_ids(topic="maintenance.work.requested")
    assert repository.verify_audit_chain()


def test_requeued_source_work_is_rerouted_and_completed_via_worker_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_repository = repository()
    profile = QualityProfile(
        id="quality-source-route-replay",
        entity_type_id="type-plant",
        schema_version="1.0.0",
        minimum_citations=1,
        minimum_sections=0,
    )
    local_repository.save_schema_document(
        document_id=profile.id,
        schema_version=profile.schema_version,
        kind="quality-profile",
        document=profile.model_dump(mode="json", by_alias=True),
    )
    local_repository.save_entity(
        KnowledgeEntity.model_validate(
            {
                "ref": {
                    "id": "entity-source-route-replay",
                    "slug": "source-route-replay",
                    "typeId": "type-plant",
                    "canonicalName": "可恢复来源采集条目",
                },
                "names": [{"locale": "zh-CN", "value": "可恢复来源采集条目"}],
                "aliases": [],
                "description": [],
                "taxonomyNodeIds": ["taxonomy-plants"],
                "claims": [],
                "sections": [],
                "relationships": [],
                "citations": [],
                "revision": {
                    "revisionId": "revision-source-route-replay-1",
                    "dataVersion": "data-source-route-replay-1",
                    "schemaVersion": "1.0.0",
                    "policyVersion": "1.0.0",
                    "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
                },
                "publicationStatus": "published",
            }
        )
    )
    source = SourceDefinition(
        id="source-plant-authority-replay",
        version="1.0.0",
        name="Plant authority replay",
        kind="api",
        base_url="https://plants.example.org/records",
        allowed_hosts=["plants.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        entity_type_ids=["type-plant"],
        taxonomy_node_ids=["taxonomy-plants"],
        status="active",
    )
    local_repository.save_source_definition(source)

    assess_knowledge_quality_once(
        local_repository,
        assessed_at=datetime(2026, 7, 29, 12, tzinfo=UTC),
    )
    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = next(item for item in loaded_pack.graphs if item.id == "quality-maintenance-triage")
    assert dispatch_quality_maintenance_events(local_repository, graph) == {
        "scheduled": 1,
        "skipped": 0,
        "failed": 0,
    }
    schedule = local_repository.list_agent_graph_schedules(graph_id=graph.id)[0]
    execute_agent_schedule_once(
        local_repository,
        loaded_pack.registry(),
        schedule.id,
    )
    work_item = local_repository.list_maintenance_work_items()[0]
    assert activate_maintenance_work_once(local_repository, work_item.id).status == "ready"
    assert dispatch_maintenance_work_events(
        local_repository,
        lambda item_id: activate_maintenance_work_once(local_repository, item_id),
    ) == {"dispatched": 1, "failed": 0}

    claim = local_repository.claim_maintenance_work_item(
        work_item.id,
        assignee_id="agent-a",
        lease_seconds=300,
        claimed_at=datetime.now(UTC),
    )
    blocked = local_repository.block_maintenance_work_item(
        work_item.id,
        assignee_id="agent-a",
        lease_token=claim.lease_token,
        reason="人工核查来源策略",
    )
    assert blocked.status == "blocked"

    requeued, outbox_event_id = local_repository.requeue_blocked_maintenance_work_item(
        work_item.id,
    )
    assert requeued.status == "queued"
    assert outbox_event_id in local_repository.pending_outbox_ids(topic="maintenance.work.requested")

    assert dispatch_maintenance_work_events(
        local_repository,
        lambda item_id: activate_maintenance_work_once(local_repository, item_id),
    ) == {"dispatched": 1, "failed": 0}
    assert local_repository.get_maintenance_work_item(work_item.id).status == "ready"
    assert outbox_event_id not in local_repository.pending_outbox_ids(topic="maintenance.work.requested")

    routed, job = request_source_acquisition_for_work_once(
        local_repository,
        work_item.id,
        requested_at=datetime(2026, 7, 29, 14, 30, tzinfo=UTC),
    )
    assert routed.status == "ready"
    assert job is not None
    assert job.maintenance_work_item_id == work_item.id
    assert local_repository.pending_outbox_ids(topic="source.acquisition.requested")

    content = b'{"records":[{"id":"x"}]}'
    digest = hashlib.sha256(content).hexdigest()
    snapshot = SourceSnapshot(
        id=f"snapshot-{source.id}-{digest[:16]}",
        source_id=source.id,
        source_version=source.version,
        url=source.base_url,
        content_sha256=digest,
        storage_key=f"sources/{source.id}/{digest}",
        media_type="application/json",
        byte_size=len(content),
        http_status=200,
        license_id=source.license_id,
        capture_status="captured",
        retrieved_at=datetime(2026, 7, 29, 14, 35, tzinfo=UTC),
    )

    def fake_acquire(
        selected_source: SourceDefinition,
        *,
        url: str | None = None,
    ) -> SimpleNamespace:
        assert selected_source == source
        assert url == source.base_url
        return SimpleNamespace(snapshot=snapshot, content=content)

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        fake_acquire,
    )
    class WorkerStore:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def put(self, *, key: str, **_: object) -> None:
            self.keys.append(key)

        def get(self, *, key: str) -> bytes:
            raise AssertionError(f"unexpected read: {key}")

    store = WorkerStore()
    assert dispatch_source_acquisition_events(
        local_repository,
        lambda job_id: execute_source_acquisition_once(
            local_repository,
            store,
            job_id,
        ),
    ) == {"dispatched": 1, "failed": 0}
    assert store.keys == [snapshot.storage_key]
    assert local_repository.pending_outbox_ids(topic="source.acquisition.requested") == []

    completed = local_repository.get_maintenance_work_item(work_item.id)
    assert completed is not None
    assert completed.status == "completed"
    assert [item.id for item in completed.evidence_refs] == [snapshot.id]
    assert [item.id for item in completed.output_refs] == [job.id]
    assert local_repository.verify_audit_chain()

    route_audit = next(
        event
        for event in reversed(local_repository.list_audit_events())
        if event.action == "maintenance.source.route" and event.resource_id == work_item.id
    )
    assert route_audit.outcome == "success"
    complete_audit = next(
        event
        for event in reversed(local_repository.list_audit_events())
        if event.action == "maintenance.source.complete" and event.resource_id == work_item.id
    )
    assert complete_audit.outcome == "success"
    assert complete_audit.metadata["acquisitionJobId"] == job.id
    acquisition_audit = next(
        event
        for event in reversed(local_repository.list_audit_events())
        if event.action == "source.acquisition.execute"
        and event.resource_id == job.id
    )
    assert acquisition_audit.outcome == "success"


def test_failed_source_acquisition_can_be_requeued_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_repository = repository()
    profile = QualityProfile(
        id="quality-source-route-retry",
        entity_type_id="type-plant",
        schema_version="1.0.0",
        minimum_citations=1,
        minimum_sections=0,
    )
    local_repository.save_schema_document(
        document_id=profile.id,
        schema_version=profile.schema_version,
        kind="quality-profile",
        document=profile.model_dump(mode="json", by_alias=True),
    )
    local_repository.save_entity(
        KnowledgeEntity.model_validate(
            {
                "ref": {
                    "id": "entity-source-route-retry",
                    "slug": "source-route-retry",
                    "typeId": "type-plant",
                    "canonicalName": "采集重试条目",
                },
                "names": [{"locale": "zh-CN", "value": "采集重试条目"}],
                "aliases": [],
                "description": [],
                "taxonomyNodeIds": ["taxonomy-plants"],
                "claims": [],
                "sections": [],
                "relationships": [],
                "citations": [],
                "revision": {
                    "revisionId": "revision-source-route-retry-1",
                    "dataVersion": "data-source-route-retry-1",
                    "schemaVersion": "1.0.0",
                    "policyVersion": "1.0.0",
                    "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
                },
                "publicationStatus": "published",
            }
        )
    )
    source = SourceDefinition(
        id="source-plant-authority-retry",
        version="1.0.0",
        name="Plant authority retry",
        kind="api",
        base_url="https://plants.example.org/records",
        allowed_hosts=["plants.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        entity_type_ids=["type-plant"],
        taxonomy_node_ids=["taxonomy-plants"],
        status="active",
    )
    local_repository.save_source_definition(source)

    assess_knowledge_quality_once(
        local_repository,
        assessed_at=datetime(2026, 7, 29, 12, tzinfo=UTC),
    )
    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = next(item for item in loaded_pack.graphs if item.id == "quality-maintenance-triage")
    assert dispatch_quality_maintenance_events(local_repository, graph) == {
        "scheduled": 1,
        "skipped": 0,
        "failed": 0,
    }
    schedule = local_repository.list_agent_graph_schedules(graph_id=graph.id)[0]
    execute_agent_schedule_once(
        local_repository,
        loaded_pack.registry(),
        schedule.id,
    )
    work_item = local_repository.list_maintenance_work_items()[0]
    assert activate_maintenance_work_once(local_repository, work_item.id).status == "ready"
    routed, job = request_source_acquisition_for_work_once(
        local_repository,
        work_item.id,
        requested_at=datetime(2026, 7, 29, 14, tzinfo=UTC),
    )
    assert job is not None
    assert routed.status == "ready"
    assert job.maintenance_work_item_id == work_item.id

    def fail_acquire(
        selected_source: SourceDefinition,
        *,
        url: str | None = None,
    ) -> None:
        raise AcquisitionError("temporary failure")

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        fail_acquire,
    )
    class FailingStore:
        def put(self, *, key: str, **_: object) -> None:
            raise AssertionError(f"unexpected put: {key}")

        def get(self, *, key: str) -> bytes:
            raise AssertionError(f"unexpected get: {key}")

    failed_store = FailingStore()
    assert dispatch_source_acquisition_events(
        local_repository,
        lambda job_id: execute_source_acquisition_once(
            local_repository,
            failed_store,
            job_id,
        ),
    ) == {"dispatched": 0, "failed": 1}
    failed_job = local_repository.get_source_acquisition_job(job.id)
    assert failed_job is not None
    assert failed_job.status == "failed"
    assert local_repository.get_maintenance_work_item(work_item.id).status == "ready"
    assert local_repository.verify_audit_chain()
    failure_audit = next(
        event
        for event in reversed(local_repository.list_audit_events())
        if event.action == "source.acquisition.execute"
        and event.resource_id == job.id
    )
    assert failure_audit.outcome == "failed"
    pending_requeue = local_repository.pending_outbox_ids(topic="source.acquisition.requested")
    assert len(pending_requeue) == 1

    def success_acquire(
        selected_source: SourceDefinition,
        *,
        url: str | None = None,
    ) -> SimpleNamespace:
        assert selected_source == source
        assert url == source.base_url
        return SimpleNamespace(
            snapshot=SourceSnapshot(
                id="snapshot-source-route-retry-success",
                source_id=source.id,
                source_version=source.version,
                url=source.base_url,
                content_sha256=hashlib.sha256(b"ok").hexdigest(),
                storage_key="sources/retry/ok",
                media_type="application/json",
                byte_size=2,
                http_status=200,
                license_id=source.license_id,
                capture_status="captured",
                retrieved_at=datetime(2026, 7, 29, 14, 20, tzinfo=UTC),
            ),
            content=b"ok",
        )

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        success_acquire,
    )
    replay_event_id = local_repository.requeue_outbox_event(
        topic="source.acquisition.requested",
        aggregate_id=job.id,
        payload={
            "jobId": job.id,
            "sourceId": source.id,
            "sourceVersion": source.version,
            "url": job.url,
            "maintenanceWorkItemId": work_item.id,
        },
    )
    requeued_event = next(
        item
        for item in local_repository.pending_outbox_records(topic="source.acquisition.requested")
        if item.id == replay_event_id
    )
    assert requeued_event.payload["maintenanceWorkItemId"] == work_item.id
    assert replay_event_id == pending_requeue[0]
    assert replay_event_id in local_repository.pending_outbox_ids(
        topic="source.acquisition.requested"
    )
    class SuccessStore:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def put(self, *, key: str, **_: object) -> None:
            self.keys.append(key)

        def get(self, *, key: str) -> bytes:
            raise AssertionError(f"unexpected read: {key}")

    success_store = SuccessStore()
    assert dispatch_source_acquisition_events(
        local_repository,
        lambda job_id: execute_source_acquisition_once(
            local_repository,
            success_store,
            job_id,
        ),
    ) == {"dispatched": 1, "failed": 0}
    assert success_store.keys == ["sources/retry/ok"]
    completed = local_repository.get_maintenance_work_item(work_item.id)
    assert completed is not None
    assert completed.status == "completed"
    assert [item.id for item in completed.output_refs] == [job.id]
    assert local_repository.get_source_snapshot("snapshot-source-route-retry-success") is not None
    replay_audit = next(
        event
        for event in reversed(local_repository.list_audit_events())
        if event.action == "source.acquisition.execute"
        and event.resource_id == job.id
        and event.outcome == "success"
    )
    assert replay_audit.metadata["snapshotId"] == "snapshot-source-route-retry-success"


class MemoryStore:
    def put(self, *, key: str, **_: object) -> None:
        raise AssertionError(f"unexpected put: {key}")

    def get(self, *, key: str) -> bytes:
        raise AssertionError(f"unexpected get: {key}")
