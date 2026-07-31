import hashlib
import logging
import os
import time
from datetime import UTC, datetime, timedelta

from croniter import croniter
from hardatlas_data import KnowledgeRepository, QualityMaintenanceService
from hardatlas_domain import (
    QualityProfile,
    SourceAcquisitionJob,
    evaluate_source_policy,
)
from sqlalchemy import create_engine

logger = logging.getLogger("hardatlas.source-scheduler")


def _occurrence_key(source_id: str, version: str, occurrence: datetime) -> str:
    timestamp = occurrence.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"source-schedule:{source_id}:{version}:{timestamp}"


def schedule_due_sources_once(
    repository: KnowledgeRepository,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    scheduled: list[str] = []
    existing: list[str] = []
    skipped: dict[str, str] = {}
    for source in repository.list_source_definitions():
        decision = evaluate_source_policy(source)
        if not decision.allowed:
            skipped[source.id] = "; ".join(decision.blockers)
            continue
        if not source.schedule:
            skipped[source.id] = "source has no automatic acquisition schedule"
            continue
        try:
            occurrence = croniter(
                source.schedule,
                observed_at + timedelta(seconds=1),
            ).get_prev(datetime)
        except (TypeError, ValueError, KeyError) as error:
            skipped[source.id] = f"invalid schedule: {error}"
            continue
        if occurrence.tzinfo is None:
            occurrence = occurrence.replace(tzinfo=UTC)
        else:
            occurrence = occurrence.astimezone(UTC)
        if occurrence < source.created_at.astimezone(UTC):
            skipped[source.id] = "next scheduled occurrence has not arrived"
            continue

        idempotency_key = _occurrence_key(
            source.id,
            source.version,
            occurrence,
        )
        prior = repository.get_source_acquisition_job_by_idempotency(
            idempotency_key
        )
        if prior is not None:
            existing.append(prior.id)
            continue
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]
        job = SourceAcquisitionJob(
            id=f"acquisition-scheduled-{digest}",
            source_id=source.id,
            source_version=source.version,
            requested_by="atlas-source-scheduler",
            idempotency_key=idempotency_key,
            created_at=observed_at,
            updated_at=observed_at,
        )
        try:
            repository.create_source_acquisition_job(job)
        except ValueError:
            prior = repository.get_source_acquisition_job_by_idempotency(
                idempotency_key
            )
            if prior is None:
                raise
            existing.append(prior.id)
            continue
        scheduled.append(job.id)
    return {
        "scheduled": scheduled,
        "existing": existing,
        "skipped": skipped,
        "observedAt": observed_at.isoformat(),
    }


def assess_due_quality_once(
    repository: KnowledgeRepository,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    profiles = [
        QualityProfile.model_validate(document)
        for document in repository.list_schema_documents(
            kind="quality-profile"
        )
    ]
    result = QualityMaintenanceService(repository, profiles).scan(
        assessed_at=observed_at
    )
    return result.model_dump(mode="json", by_alias=True)


def _repository_from_environment() -> KnowledgeRepository:
    database_url = os.environ["HARDATLAS_DATABASE_URL"]
    return KnowledgeRepository(
        create_engine(database_url, pool_pre_ping=True)
    )


def main() -> None:
    interval_seconds = max(
        float(os.getenv("HARDATLAS_SOURCE_SCHEDULER_SECONDS", "30")),
        1,
    )
    quality_interval_seconds = max(
        float(os.getenv("HARDATLAS_QUALITY_SCAN_SECONDS", "3600")),
        interval_seconds,
    )
    next_quality_scan = 0.0
    repository = _repository_from_environment()
    while True:
        try:
            result = schedule_due_sources_once(repository)
            if result["scheduled"]:
                logger.info(
                    "scheduled source acquisition jobs: %s",
                    result["scheduled"],
                )
        except Exception:
            logger.exception("source scheduler tick failed")
        monotonic_now = time.monotonic()
        if monotonic_now >= next_quality_scan:
            try:
                result = assess_due_quality_once(repository)
                logger.info(
                    "quality scan assessed=%s openTasks=%s failures=%s",
                    len(result["assessed"]),
                    result["openTaskCount"],
                    len(result["failures"]),
                )
            except Exception:
                logger.exception("quality scheduler tick failed")
            next_quality_scan = (
                monotonic_now + quality_interval_seconds
            )
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
