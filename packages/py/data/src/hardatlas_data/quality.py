from datetime import UTC, datetime

from hardatlas_domain import (
    KnowledgeModel,
    QualityAssessment,
    QualityProfile,
    assess_entity_quality,
    maintenance_tasks_from_assessment,
)
from pydantic import Field

from .repository import KnowledgeRepository


class QualityScanFailure(KnowledgeModel):
    entity_id: str
    code: str
    message: str


class QualityScanResult(KnowledgeModel):
    assessed: list[QualityAssessment] = Field(default_factory=list)
    skipped_entity_ids: list[str] = Field(default_factory=list)
    failures: list[QualityScanFailure] = Field(default_factory=list)
    open_task_count: int
    started_at: datetime
    completed_at: datetime


class QualityMaintenanceService:
    """Runs deterministic quality evaluation and reconciles persistent work."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        profiles: list[QualityProfile],
    ) -> None:
        self.repository = repository
        self.profiles = {
            profile.entity_type_id: profile
            for profile in profiles
        }
        if len(self.profiles) != len(profiles):
            raise ValueError(
                "only one active quality profile is allowed per entity type"
            )

    def scan(
        self,
        *,
        entity_ids: list[str] | None = None,
        assessed_at: datetime | None = None,
    ) -> QualityScanResult:
        started_at = assessed_at or datetime.now(UTC)
        selected_ids = set(entity_ids or [])
        assessed: list[QualityAssessment] = []
        skipped: list[str] = []
        failures: list[QualityScanFailure] = []
        observed_ids: set[str] = set()
        for entity in self.repository.list_entities():
            if selected_ids and entity.ref.id not in selected_ids:
                continue
            observed_ids.add(entity.ref.id)
            if entity.publication_status != "published":
                skipped.append(entity.ref.id)
                continue
            profile = self.profiles.get(entity.ref.type_id)
            if profile is None:
                skipped.append(entity.ref.id)
                continue
            try:
                assessment = assess_entity_quality(
                    entity,
                    profile,
                    assessed_at=started_at,
                )
                tasks = maintenance_tasks_from_assessment(
                    assessment,
                    created_at=started_at,
                )
                self.repository.reconcile_quality_assessment(
                    assessment,
                    tasks,
                )
                assessed.append(assessment)
            except (ValueError, TypeError) as error:
                failures.append(
                    QualityScanFailure(
                        entity_id=entity.ref.id,
                        code="quality-assessment-failed",
                        message=str(error),
                    )
                )
        for missing_id in sorted(selected_ids - observed_ids):
            failures.append(
                QualityScanFailure(
                    entity_id=missing_id,
                    code="entity-not-found",
                    message="requested entity does not exist",
                )
            )
        completed_at = datetime.now(UTC)
        return QualityScanResult(
            assessed=assessed,
            skipped_entity_ids=skipped,
            failures=failures,
            open_task_count=len(
                self.repository.list_maintenance_tasks(status="open")
            ),
            started_at=started_at,
            completed_at=completed_at,
        )
