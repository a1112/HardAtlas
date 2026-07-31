from datetime import UTC, datetime

from hardatlas_ai import GraphRunSchedule, RunBudget
from hardatlas_data import KnowledgeRepository, QualityMaintenanceService
from hardatlas_domain import KnowledgeEntity, QualityProfile
from sqlalchemy import create_engine


def entity(revision_id: str, *, complete: bool) -> KnowledgeEntity:
    citation = {
        "id": "citation-1",
        "sourceId": "source-1",
        "sourceTitle": "权威资料",
        "sourceTier": "authoritative",
        "retrievedAt": datetime(2026, 7, 29, tzinfo=UTC),
    }
    return KnowledgeEntity.model_validate(
        {
            "ref": {
                "id": "entity-snow-leopard",
                "slug": "snow-leopard",
                "typeId": "type-animal",
                "canonicalName": "雪豹",
            },
            "names": [{"locale": "zh-CN", "value": "雪豹"}],
            "aliases": [],
            "description": [{"locale": "zh-CN", "value": "高山猫科动物"}],
            "taxonomyNodeIds": ["tax-animals"] if complete else [],
            "claims": [
                {
                    "id": "claim-scientific-name",
                    "attributeDefinitionId": "attr-scientific-name",
                    "originalValue": "Panthera uncia",
                    "displayValue": [{"locale": "zh-CN", "value": "Panthera uncia"}],
                    "confidence": 0.99,
                    "citationIds": ["citation-1"],
                    "revisionId": revision_id,
                }
            ]
            if complete
            else [],
            "sections": [
                {
                    "id": "section-overview",
                    "key": "overview",
                    "heading": [{"locale": "zh-CN", "value": "概述"}],
                    "body": [{"locale": "zh-CN", "value": "高山物种"}],
                    "citationIds": ["citation-1"],
                    "order": 1,
                }
            ]
            if complete
            else [],
            "relationships": [],
            "citations": [citation] if complete else [],
            "revision": {
                "revisionId": revision_id,
                "dataVersion": revision_id,
                "schemaVersion": "1.0.0",
                "policyVersion": "1.0.0",
                "createdAt": datetime(2026, 7, 29, tzinfo=UTC),
            },
            "publicationStatus": "published",
        }
    )


def profile() -> QualityProfile:
    return QualityProfile.model_validate(
        {
            "id": "quality-animal",
            "entityTypeId": "type-animal",
            "schemaVersion": "1.0.0",
            "requiredLocales": ["zh-CN"],
            "requiredAttributeIds": ["attr-scientific-name"],
            "minimumCitations": 1,
            "minimumSections": 1,
        }
    )


def test_quality_scan_is_idempotent_and_persists_agent_work() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_entity(entity("revision-1", complete=False))
    service = QualityMaintenanceService(repository, [profile()])
    assessed_at = datetime(2026, 7, 29, 12, tzinfo=UTC)

    first = service.scan(assessed_at=assessed_at)
    outbox_size = repository.outbox_size()
    second = service.scan(assessed_at=assessed_at)

    assert len(first.assessed) == 1
    assert first.open_task_count == 4
    assert second.open_task_count == 4
    assert repository.outbox_size() == outbox_size
    assert len(repository.list_quality_assessments()) == 1
    assert {task.issue.code for task in repository.list_maintenance_tasks(status="open")} == {
        "missing-required-attribute",
        "insufficient-citations",
        "insufficient-sections",
        "missing-taxonomy",
    }


def test_new_revision_supersedes_obsolete_tasks() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_entity(entity("revision-1", complete=False))
    service = QualityMaintenanceService(repository, [profile()])
    service.scan(assessed_at=datetime(2026, 7, 29, 12, tzinfo=UTC))

    repository.save_entity(entity("revision-2", complete=True))
    result = service.scan(assessed_at=datetime(2026, 7, 29, 13, tzinfo=UTC))

    assert result.assessed[0].status == "healthy"
    assert result.open_task_count == 0
    assert len(repository.list_maintenance_tasks(status="superseded")) == 4
    assert repository.list_quality_assessments()[0].revision_id == "revision-2"


def test_stale_task_is_superseded_without_graph_schedule() -> None:
    repository = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    repository.save_entity(entity("revision-1", complete=False))
    QualityMaintenanceService(repository, [profile()]).scan(
        assessed_at=datetime(2026, 7, 29, 12, tzinfo=UTC)
    )
    task = repository.list_maintenance_tasks(status="open")[0]
    repository.save_entity(entity("revision-2", complete=True))
    schedule = GraphRunSchedule(
        id="schedule-stale-quality",
        graph_id="quality-maintenance-triage",
        graph_version="graph-1.0.0",
        trigger_type="schedule",
        input={
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
        },
        requested_by="quality-maintenance:test",
        idempotency_key="quality-maintenance-stale-test",
        budget=RunBudget(
            maxAttemptsPerNode=2,
            maxTotalAttempts=2,
            maxModelCalls=0,
            maxInputTokens=0,
            maxOutputTokens=0,
            maxCostMicrousd=0,
            deadlineSeconds=60,
        ),
    )

    persisted_task, persisted_schedule = repository.schedule_maintenance_task(
        task.id,
        schedule,
    )

    assert persisted_task.status == "superseded"
    assert persisted_schedule is None
    assert repository.get_agent_graph_schedule(schedule.id) is None
    assert repository.pending_outbox_ids(topic="agent.graph.scheduled") == []
