import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from hardatlas_ai import GraphRunSchedule, load_agent_pack
from hardatlas_data import KnowledgeRepository
from hardatlas_domain import (
    Citation,
    ExtractionCandidate,
    ExtractionLocator,
    ExtractionParserDefinition,
    GovernanceService,
    KnowledgeEntity,
    LocalizedText,
    SourceAcquisitionJob,
    SourceDefinition,
    SourceSnapshot,
    apply_release_to_entity,
)
from hardatlas_ingestion import AcquisitionError, ParserRegistry
from hardatlas_worker.tasks import (
    dispatch_agent_schedule_events,
    dispatch_source_acquisition_events,
    dispatch_source_extraction_events,
    dispatch_source_snapshot_events,
    execute_agent_schedule_once,
    execute_source_acquisition_once,
    extract_source_snapshot_once,
    resolve_extraction_candidate_once,
    schedule_extraction_candidates,
)
from sqlalchemy import create_engine


def repository() -> KnowledgeRepository:
    result = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    result.create_schema()
    return result


def schedule() -> GraphRunSchedule:
    graph = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core").graphs[0]
    return GraphRunSchedule(
        id="schedule-worker-001",
        graph_id=graph.id,
        graph_version=graph.version,
        trigger_type="source-change",
        input={
            "sourceId": "source-test",
            "snapshotHash": "sha256:worker-test",
            "entityId": "entity-ginkgo",
            "label": "银杏",
            "fieldPath": "/description/0/value",
            "proposedValue": "由受治理 Worker 生成的候选描述。",
            "citationId": "citation-worker-test",
            "citationAlreadyPresent": True,
            "currentRevisionId": "revision-worker-test",
            "currentValuePresent": True,
            "currentValue": "现有描述",
            "confidence": 0.96,
            "risk": "low",
        },
        requested_by="test-user",
        idempotency_key="worker-snapshot-test-001",
        budget=graph.budget,
    )


def test_worker_executes_schedule_and_creates_governed_proposal() -> None:
    local_repository = repository()
    item = schedule()
    local_repository.save_agent_graph_schedule(item)
    registry = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core").registry()

    completed = execute_agent_schedule_once(
        local_repository,
        registry,
        item.id,
    )

    assert completed.status == "completed"
    assert completed.run_id == f"run-{item.id}"
    run = local_repository.get_agent_graph_run(completed.run_id)
    assert run
    assert run["status"] == "completed"
    assert run["nodes"]["verify"]["attempts"] == 1
    governed = local_repository.get_governed_proposal(f"proposal-{completed.run_id}")
    assert governed
    assert governed.proposal.entity_id == "entity-ginkgo"
    assert governed.proposal.operations[0].citation_ids == ["citation-worker-test"]
    assert local_repository.audit_event_count() == 1
    assert local_repository.verify_audit_chain()

    repeated = execute_agent_schedule_once(
        local_repository,
        registry,
        item.id,
    )
    assert repeated == completed
    assert local_repository.audit_event_count() == 1


def test_outbox_dispatch_confirms_only_successful_enqueue() -> None:
    local_repository = repository()
    item = schedule()
    local_repository.save_agent_graph_schedule(item)
    enqueued: list[str] = []

    result = dispatch_agent_schedule_events(
        local_repository,
        lambda schedule_id: enqueued.append(schedule_id),
    )

    assert result == {"dispatched": 1, "failed": 0}
    assert enqueued == [item.id]
    assert local_repository.pending_outbox_ids(topic="agent.graph.scheduled") == []


def test_outbox_dispatch_leaves_failed_enqueue_pending_for_retry() -> None:
    local_repository = repository()
    item = schedule()
    local_repository.save_agent_graph_schedule(item)

    def unavailable_broker(_schedule_id: str) -> None:
        raise ConnectionError("broker unavailable")

    result = dispatch_agent_schedule_events(
        local_repository,
        unavailable_broker,
    )

    assert result == {"dispatched": 0, "failed": 1}
    assert local_repository.pending_outbox_ids(topic="agent.graph.scheduled")


def test_snapshot_extraction_schedules_governed_agent_proposals() -> None:
    local_repository = repository()
    definition = ExtractionParserDefinition.model_validate(
        {
            "id": "parser-worker-json",
            "version": "1.0.0",
            "format": "json",
            "mediaTypes": ["application/json"],
            "entityTypeId": "entity-type-plant",
            "locale": "zh-CN",
            "recordsPath": "/items",
            "externalIdPath": "sourceId",
            "entityIdPath": "entityId",
            "labelPath": "name",
            "fieldMappings": [
                {
                    "sourcePath": "summary",
                    "targetPath": "/description/0/value",
                    "confidence": 0.95,
                    "required": True,
                }
            ],
        }
    )
    registered_source = SourceDefinition(
        id="source-worker",
        version="1.0.0",
        name="Worker fixture source",
        kind="api",
        base_url="https://knowledge.example.org/plants",
        allowed_hosts=["knowledge.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        parser_id=definition.id,
        parser_version=definition.version,
        status="active",
    )
    content = (
        '{"items":[{"sourceId":"ginkgo","name":"银杏","summary":"银杏是银杏纲现存物种。"}]}'
    ).encode()
    digest = hashlib.sha256(content).hexdigest()
    captured = SourceSnapshot(
        id=f"snapshot-source-worker-{digest[:16]}",
        source_id=registered_source.id,
        source_version=registered_source.version,
        url=registered_source.base_url,
        content_sha256=digest,
        storage_key=f"sources/source-worker/{digest}",
        media_type="application/json",
        byte_size=len(content),
        http_status=200,
        license_id=registered_source.license_id,
        capture_status="captured",
        retrieved_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    )
    local_repository.save_entity(
        KnowledgeEntity.model_validate(
            {
                "ref": {
                    "id": "entity-ginkgo",
                    "slug": "ginkgo",
                    "typeId": "entity-type-plant",
                    "canonicalName": "银杏",
                },
                "names": [{"locale": "zh-CN", "value": "银杏"}],
                "aliases": [{"locale": "la", "value": "Ginkgo biloba"}],
                "description": [{"locale": "zh-CN", "value": "现有条目"}],
                "taxonomyNodeIds": [],
                "claims": [],
                "sections": [],
                "relationships": [],
                "citations": [],
                "revision": {
                    "revisionId": "revision-ginkgo-worker",
                    "dataVersion": "test",
                    "schemaVersion": "test",
                    "policyVersion": "test",
                    "createdAt": "2026-07-29T12:00:00Z",
                },
                "publicationStatus": "published",
            }
        )
    )
    local_repository.save_source_definition(registered_source)
    local_repository.save_source_snapshot(captured)
    queued_snapshots: list[str] = []
    assert dispatch_source_snapshot_events(
        local_repository,
        queued_snapshots.append,
    ) == {"dispatched": 1, "failed": 0, "skipped": 0}
    assert queued_snapshots == [captured.id]

    class MemoryStore:
        def put(self, **_: object) -> None:
            raise AssertionError("extraction must not rewrite snapshots")

        def get(self, *, key: str) -> bytes:
            assert key == captured.storage_key
            return content

    batch, candidates = extract_source_snapshot_once(
        local_repository,
        MemoryStore(),
        ParserRegistry([definition]),
        captured.id,
    )
    assert local_repository.get_extraction_batch(batch.id) == batch
    assert local_repository.get_extraction_candidate(candidates[0].id) == candidates[0]
    queued_batches: list[str] = []
    assert dispatch_source_extraction_events(
        local_repository,
        queued_batches.append,
    ) == {"dispatched": 1, "failed": 0}
    assert queued_batches == [batch.id]

    loaded_pack = load_agent_pack(Path(__file__).parents[3] / "agent-packs" / "core")
    graph = loaded_pack.graphs[0]
    resolved_candidate = resolve_extraction_candidate_once(
        local_repository,
        candidates[0],
    )
    assert resolved_candidate.entity_id == "entity-ginkgo"
    assert resolved_candidate.status == "resolved"
    assert resolved_candidate.resolution_matches[0].match_basis == "exact-name"
    scheduling = schedule_extraction_candidates(
        local_repository,
        graph,
        batch,
        [resolved_candidate],
    )
    assert scheduling == {"scheduled": 1, "unresolved": 0, "empty": 0}
    scheduled_candidate = local_repository.get_extraction_candidate(candidates[0].id)
    assert scheduled_candidate
    assert scheduled_candidate.status == "scheduled"
    assert len(scheduled_candidate.schedule_ids) == 1

    scheduled = local_repository.get_agent_graph_schedule(scheduled_candidate.schedule_ids[0])
    assert scheduled
    assert scheduled.input["candidateId"] == candidates[0].id
    assert scheduled.input["snapshotId"] == captured.id
    assert scheduled.input["modelProcessingAllowed"] is False
    assert scheduled.input["currentRevisionId"] == "revision-ginkgo-worker"
    assert scheduled.input["currentValue"] == "现有条目"
    assert scheduled.input["currentValuePresent"] is True
    assert scheduled.input["citation"] == candidates[0].citation.model_dump(
        mode="json",
        by_alias=True,
    )
    completed = execute_agent_schedule_once(
        local_repository,
        loaded_pack.registry(),
        scheduled.id,
    )
    governed = local_repository.get_governed_proposal(f"proposal-{completed.run_id}")
    assert governed
    assert governed.proposal.entity_id == "entity-ginkgo"
    assert len(governed.proposal.operations) == 2
    citation_operation, content_operation = governed.proposal.operations
    assert citation_operation.operation == "add"
    assert citation_operation.path == (f"/citations/{candidates[0].citation.id}")
    assert citation_operation.after == scheduled.input["citation"]
    assert citation_operation.citation_ids == []
    assert content_operation.operation == "replace"
    assert content_operation.path == "/description/0/value"
    assert content_operation.before == "现有条目"
    assert content_operation.after == "银杏是银杏纲现存物种。"
    assert content_operation.citation_ids == [candidates[0].citation.id]

    governance = GovernanceService(
        [governed.proposal.model_copy(deep=True)],
        "policy-worker-test",
    )
    evaluated = governance.evaluate(governed.proposal.id)
    assert evaluated.proposal.status == "policy-approved"
    accepted = governance.accept_policy_approved(governed.proposal.id)
    current_entity = local_repository.get_entity_by_id("entity-ginkgo")
    assert current_entity
    published = apply_release_to_entity(
        current_entity,
        [accepted],
        data_version="atlas-worker-publication-test",
        schema_version="schema-worker-test",
        policy_version="policy-worker-test",
    )
    assert published.description[0].value == "银杏是银杏纲现存物种。"
    assert published.description[0].machine_generated is True
    assert published.citations == [candidates[0].citation]
    assert published.revision.data_version == "atlas-worker-publication-test"
    local_repository.save_entity(published)
    persisted = local_repository.get_entity_by_id("entity-ginkgo")
    assert persisted
    assert persisted.revision == published.revision
    assert persisted.citations == [candidates[0].citation]

    repeated = schedule_extraction_candidates(
        local_repository,
        graph,
        batch,
        [resolved_candidate],
    )
    assert repeated == {"scheduled": 0, "unresolved": 0, "empty": 1}


def test_entity_resolution_keeps_ambiguous_exact_matches_unresolved() -> None:
    def matching_entity(entity_id: str, slug: str) -> KnowledgeEntity:
        return KnowledgeEntity.model_validate(
            {
                "ref": {
                    "id": entity_id,
                    "slug": slug,
                    "typeId": "type-concept",
                    "canonicalName": "苹果",
                },
                "names": [{"locale": "zh-CN", "value": "苹果"}],
                "aliases": [],
                "description": [],
                "taxonomyNodeIds": [],
                "claims": [],
                "sections": [],
                "relationships": [],
                "citations": [],
                "revision": {
                    "revisionId": f"revision-{entity_id}",
                    "dataVersion": "test",
                    "schemaVersion": "test",
                    "policyVersion": "test",
                    "createdAt": "2026-07-29T12:00:00Z",
                },
                "publicationStatus": "published",
            }
        )

    candidate = ExtractionCandidate(
        id="candidate-ambiguous",
        batch_id="batch-ambiguous",
        snapshot_id="snapshot-ambiguous",
        source_id="source-ambiguous",
        source_version="1.0.0",
        parser_id="parser-test",
        parser_version="1.0.0",
        entity_type_id="type-concept",
        labels=[LocalizedText(locale="zh-CN", value="苹果")],
        fields=[],
        citation=Citation(
            id="citation-ambiguous",
            source_id="source-ambiguous",
            source_title="Fixture",
            source_tier="authoritative",
            retrieved_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
        ),
        record_locator=ExtractionLocator(kind="json-pointer", value="/items/0"),
    )

    class ResolutionRepository:
        def list_entities(self, type_id: str) -> list[KnowledgeEntity]:
            assert type_id == "type-concept"
            return [
                matching_entity("entity-apple-fruit", "apple-fruit"),
                matching_entity("entity-apple-company", "apple-company"),
            ]

        def record_extraction_resolution(
            self,
            candidate_id: str,
            matches: list[object],
            *,
            resolved_entity_id: str | None = None,
        ) -> ExtractionCandidate:
            assert candidate_id == candidate.id
            assert resolved_entity_id is None
            return candidate.model_copy(update={"resolution_matches": matches})

    result = resolve_extraction_candidate_once(ResolutionRepository(), candidate)  # type: ignore[arg-type]
    assert result.entity_id is None
    assert len(result.resolution_matches) == 2


def test_source_acquisition_job_is_outboxed_retryable_and_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_repository = repository()
    registered_source = SourceDefinition(
        id="source-acquisition-worker",
        version="1.0.0",
        name="Acquisition fixture",
        kind="api",
        base_url="https://knowledge.example.org/data",
        allowed_hosts=["knowledge.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        status="active",
    )
    local_repository.save_source_definition(registered_source)
    job = SourceAcquisitionJob(
        id="acquisition-worker-001",
        source_id=registered_source.id,
        source_version=registered_source.version,
        requested_by="test-user",
        idempotency_key="acquisition-worker-idempotency-001",
    )
    local_repository.create_source_acquisition_job(job)
    pending_before = local_repository.pending_outbox_records(topic="source.acquisition.requested")
    assert [item.payload.get("url") for item in pending_before] == [None]
    enqueued: list[str] = []
    assert dispatch_source_acquisition_events(
        local_repository,
        lambda job_id: enqueued.append(job_id),
    ) == {"dispatched": 1, "failed": 0}
    assert enqueued == [job.id]
    assert local_repository.pending_outbox_ids(topic="source.acquisition.requested") == []

    content = b'{"items":[]}'
    digest = hashlib.sha256(content).hexdigest()
    captured = SourceSnapshot(
        id=f"snapshot-{registered_source.id}-{digest[:16]}",
        source_id=registered_source.id,
        source_version=registered_source.version,
        url=registered_source.base_url,
        content_sha256=digest,
        storage_key=f"sources/{registered_source.id}/{digest}",
        media_type="application/json",
        byte_size=len(content),
        http_status=200,
        license_id=registered_source.license_id,
        capture_status="captured",
    )

    def fake_acquire(
        source: SourceDefinition,
        *,
        url: str | None = None,
    ) -> SimpleNamespace:
        assert source == registered_source
        assert url is None
        return SimpleNamespace(snapshot=captured, content=content)

    monkeypatch.setattr(
        "hardatlas_worker.tasks.acquire_http_source",
        fake_acquire,
    )

    class MemoryStore:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def put(self, *, key: str, **_: object) -> None:
            self.keys.append(key)

        def get(self, *, key: str) -> bytes:
            raise AssertionError(f"unexpected read: {key}")

    store = MemoryStore()
    completed = execute_source_acquisition_once(
        local_repository,
        store,
        job.id,
    )
    assert completed.status == "completed"
    assert completed.snapshot_id == captured.id
    assert store.keys == [captured.storage_key]
    assert local_repository.get_source_snapshot(captured.id) == captured
    assert local_repository.pending_outbox_ids(topic="source.snapshot.captured")
    assert local_repository.verify_audit_chain()
    completion_audit = next(
        event
        for event in local_repository.list_audit_events()
        if event.action == "source.acquisition.execute" and event.resource_id == job.id
    )
    assert completion_audit.action == "source.acquisition.execute"
    assert completion_audit.outcome == "success"
    assert completion_audit.metadata["url"] == registered_source.base_url

    repeated = execute_source_acquisition_once(
        local_repository,
        store,
        job.id,
    )
    assert repeated == completed
    assert store.keys == [captured.storage_key]


def test_source_acquisition_failure_records_url_in_audit_metadata() -> None:
    local_repository = repository()
    source = SourceDefinition(
        id="source-acquisition-failed",
        version="1.0.0",
        name="Failed acquisition source",
        kind="api",
        base_url="https://knowledge.example.org/data",
        allowed_hosts=["knowledge.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        status="active",
    )
    local_repository.save_source_definition(source)
    bad_url = "https://knowledge.example.org/data?download=true"
    local_repository.create_source_acquisition_job(
        SourceAcquisitionJob(
            id="acquisition-unsafe-job",
            source_id=source.id,
            source_version=source.version,
            url=bad_url,
            requested_by="test-user",
            idempotency_key="acquisition-unsafe-idempotency",
        )
    )

    class FailingStore:
        def put(self, *, key: str, **_: object) -> None:
            raise AssertionError(f"unexpected put: {key}")

        def get(self, *, key: str) -> bytes:
            raise AssertionError(f"unexpected get: {key}")

    with pytest.raises(AcquisitionError):
        execute_source_acquisition_once(
            local_repository,
            FailingStore(),
            "acquisition-unsafe-job",
        )
    job = local_repository.get_source_acquisition_job("acquisition-unsafe-job")
    assert job is not None
    assert job.status == "failed"
    assert "acquisition URL must not include query parameters" in (job.error or "")
    audit = next(
        event
        for event in local_repository.list_audit_events()
        if event.action == "source.acquisition.execute"
        and event.resource_id == "acquisition-unsafe-job"
        and event.outcome == "failed"
    )
    assert audit.action == "source.acquisition.execute"
    assert audit.outcome == "failed"
    assert audit.metadata["sourceId"] == source.id
    assert audit.metadata["sourceVersion"] == source.version
    assert audit.metadata["url"] == bad_url


def test_source_acquisition_failure_records_default_url_in_audit_metadata(
    monkeypatch,
) -> None:
    local_repository = repository()
    source = SourceDefinition(
        id="source-acquisition-failed-default",
        version="1.0.0",
        name="Failed acquisition source default url",
        kind="api",
        base_url="https://knowledge.example.org/data",
        allowed_hosts=["knowledge.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        status="active",
    )
    local_repository.save_source_definition(source)
    local_repository.create_source_acquisition_job(
        SourceAcquisitionJob(
            id="acquisition-default-failed-job",
            source_id=source.id,
            source_version=source.version,
            requested_by="test-user",
            idempotency_key="acquisition-default-failed-idempotency",
        )
    )

    class FailingStore:
        def put(self, *, key: str, **_: object) -> None:
            raise AssertionError(f"unexpected put: {key}")

        def get(self, *, key: str) -> bytes:
            raise AssertionError(f"unexpected get: {key}")

    def fail_acquire(*_args, **_kwargs) -> None:
        raise AcquisitionError("transient network failure")
    monkeypatch.setattr("hardatlas_worker.tasks.acquire_http_source", fail_acquire)

    with pytest.raises(AcquisitionError):
        execute_source_acquisition_once(
            local_repository,
            FailingStore(),
            "acquisition-default-failed-job",
        )

    job = local_repository.get_source_acquisition_job("acquisition-default-failed-job")
    assert job is not None
    assert job.status == "failed"
    assert "transient network failure" in (job.error or "")
    audit = next(
        event
        for event in local_repository.list_audit_events()
        if event.action == "source.acquisition.execute"
        and event.resource_id == "acquisition-default-failed-job"
        and event.outcome == "failed"
    )
    assert audit.action == "source.acquisition.execute"
    assert audit.outcome == "failed"
    assert audit.metadata["sourceId"] == source.id
    assert audit.metadata["sourceVersion"] == source.version
    assert audit.metadata["url"] == source.base_url
