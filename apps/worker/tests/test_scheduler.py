from datetime import UTC, datetime

import pytest
from hardatlas_data import KnowledgeRepository
from hardatlas_domain import SourceDefinition
from hardatlas_worker.scheduler import schedule_due_sources_once
from hardatlas_worker.tasks import dispatch_source_acquisition_events
from sqlalchemy import create_engine


def repository() -> KnowledgeRepository:
    result = KnowledgeRepository(create_engine("sqlite+pysqlite:///:memory:"))
    result.create_schema()
    return result


def source(
    source_id: str,
    *,
    status: str = "active",
    schedule: str | None = "*/5 * * * *",
) -> SourceDefinition:
    return SourceDefinition(
        id=source_id,
        version="1.0.0",
        name=f"{source_id} scheduled source",
        kind="api",
        base_url="https://api.example.org/atlas",
        allowed_hosts=["api.example.org"],
        trust_tier="authoritative",
        license_id="CC-BY-4.0",
        license_status="allowed",
        robots_policy="explicit-api",
        robots_status="allowed",
        allowed_media_types=["application/json"],
        schedule=schedule,
        status=status,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    )


def test_scheduler_creates_one_idempotent_job_per_cron_occurrence() -> None:
    local_repository = repository()
    local_repository.save_source_definition(source("source-scheduled"))
    now = datetime(2026, 7, 29, 12, 11, 30, tzinfo=UTC)

    first = schedule_due_sources_once(local_repository, now=now)
    second = schedule_due_sources_once(local_repository, now=now)

    assert len(first["scheduled"]) == 1
    job_id = first["scheduled"][0]
    assert second["scheduled"] == []
    assert second["existing"] == [job_id]
    job = local_repository.get_source_acquisition_job(job_id)
    assert job
    assert job.requested_by == "atlas-source-scheduler"
    assert (
        job.idempotency_key
        == "source-schedule:source-scheduled:1.0.0:20260729T121000Z"
    )
    assert local_repository.pending_outbox_ids(
        topic="source.acquisition.requested"
    )
    enqueued: list[str] = []
    dispatched = dispatch_source_acquisition_events(
        local_repository,
        enqueued.append,
    )
    assert dispatched == {"dispatched": 1, "failed": 0}
    assert enqueued == [job_id]
    assert (
        local_repository.pending_outbox_ids(
            topic="source.acquisition.requested"
        )
        == []
    )


def test_scheduler_skips_policy_blocked_and_not_yet_due_sources() -> None:
    local_repository = repository()
    local_repository.save_source_definition(
        source("source-paused", status="paused")
    )
    future = source("source-future").model_copy(
        update={"created_at": datetime(2026, 7, 29, 12, 30, tzinfo=UTC)}
    )
    local_repository.save_source_definition(future)

    result = schedule_due_sources_once(
        local_repository,
        now=datetime(2026, 7, 29, 12, 11, 30, tzinfo=UTC),
    )

    assert result["scheduled"] == []
    assert result["skipped"]["source-paused"] == "source status is paused"
    assert (
        result["skipped"]["source-future"]
        == "next scheduled occurrence has not arrived"
    )


def test_source_contract_rejects_non_five_field_cron() -> None:
    with pytest.raises(ValueError, match="five-field cron"):
        source("source-invalid", schedule="0 */5 * * * *")
