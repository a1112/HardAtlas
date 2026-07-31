from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from .governance import GovernedProposal
from .knowledge import (
    AgentProposal,
    ChangeOperation,
    EntityRef,
    KnowledgeEntity,
    KnowledgeModel,
)

QualityIssueCode = Literal[
    "missing-required-attribute",
    "missing-locale",
    "insufficient-citations",
    "insufficient-sections",
    "uncited-content",
    "stale-revision",
    "missing-taxonomy",
]
QualityAction = Literal[
    "enrich-attribute",
    "translate",
    "add-citation",
    "add-section",
    "refresh-evidence",
    "classify",
]
MaintenanceRoute = Literal[
    "source-acquisition",
    "translation-evidence",
    "taxonomy-review",
]
MaintenanceWorkStatus = Literal[
    "queued",
    "ready",
    "claimed",
    "blocked",
    "completed",
    "superseded",
]


class QualityProfile(KnowledgeModel):
    """Versioned, declarative quality requirements for one entity type."""

    id: str
    entity_type_id: str
    schema_version: str
    required_locales: list[str] = Field(default_factory=list)
    required_attribute_ids: list[str] = Field(default_factory=list)
    minimum_citations: int = Field(default=1, ge=0)
    minimum_sections: int = Field(default=1, ge=0)
    freshness_days: int | None = Field(default=None, ge=1)
    healthy_score: int = Field(default=90, ge=0, le=100)
    critical_score: int = Field(default=50, ge=0, le=100)


class QualityIssue(KnowledgeModel):
    id: str
    code: QualityIssueCode
    severity: Literal["warning", "error"]
    path: str
    message: str
    action: QualityAction
    penalty: int = Field(ge=0, le=100)


class QualityAssessment(KnowledgeModel):
    id: str
    entity: EntityRef
    revision_id: str
    data_version: str
    profile_id: str
    profile_version: str
    status: Literal["healthy", "attention", "critical"]
    score: int = Field(ge=0, le=100)
    issues: list[QualityIssue]
    assessed_at: datetime


class MaintenanceTask(KnowledgeModel):
    id: str
    assessment_id: str
    entity: EntityRef
    revision_id: str
    profile_id: str
    profile_version: str
    issue: QualityIssue
    action: QualityAction
    status: Literal["open", "scheduled", "resolved", "superseded"] = "open"
    priority: Literal["low", "medium", "high", "critical"]
    agent_graph_schedule_id: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


class MaintenanceEvidenceLink(KnowledgeModel):
    kind: Literal["citation", "source-snapshot"]
    id: str


class MaintenanceOutputLink(KnowledgeModel):
    kind: Literal["governed-proposal", "source-acquisition-job"]
    id: str


class MaintenanceWorkItem(KnowledgeModel):
    """Leaseable, revision-pinned work produced by safe quality triage."""

    id: str
    maintenance_task_id: str
    assessment_id: str
    entity: EntityRef
    revision_id: str
    action: QualityAction
    issue: QualityIssue
    route: MaintenanceRoute
    required_capabilities: list[str]
    requires_evidence: Literal[True] = True
    proposal_eligible: Literal[False] = False
    status: MaintenanceWorkStatus = "queued"
    priority: Literal["low", "medium", "high", "critical"]
    triage_schedule_id: str
    triage_run_id: str
    assignee_id: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    attempt: int = Field(default=0, ge=0)
    blocked_reason: str | None = None
    evidence_refs: list[MaintenanceEvidenceLink] = Field(default_factory=list)
    output_refs: list[MaintenanceOutputLink] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_lease_state(self) -> "MaintenanceWorkItem":
        lease_values = (
            self.assignee_id,
            self.lease_token,
            self.lease_expires_at,
        )
        if self.status == "claimed" and any(value is None for value in lease_values):
            raise ValueError("claimed maintenance work requires a full lease")
        if self.status != "claimed" and any(value is not None for value in lease_values):
            raise ValueError("only claimed maintenance work may retain lease state")
        if self.status == "completed" and self.completed_at is None:
            raise ValueError("completed maintenance work requires completed_at")
        if self.status == "completed" and not self.evidence_refs:
            raise ValueError("completed maintenance work requires verified evidence")
        if self.status == "blocked" and not (self.blocked_reason and self.blocked_reason.strip()):
            raise ValueError("blocked maintenance work requires a reason")
        return self


_ROUTE_CAPABILITIES: dict[MaintenanceRoute, list[str]] = {
    "source-acquisition": [
        "source-registry-read",
        "source-acquisition-request",
        "evidence-create",
    ],
    "translation-evidence": [
        "entity-read",
        "translation-propose",
        "evidence-create",
    ],
    "taxonomy-review": [
        "taxonomy-read",
        "taxonomy-propose",
        "evidence-create",
    ],
}


def maintenance_route_capabilities(route: MaintenanceRoute) -> list[str]:
    return list(_ROUTE_CAPABILITIES[route])


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _issue(
    *,
    assessment_key: str,
    code: QualityIssueCode,
    severity: Literal["warning", "error"],
    path: str,
    message: str,
    action: QualityAction,
    penalty: int,
) -> QualityIssue:
    return QualityIssue(
        id=_stable_id("quality-issue", assessment_key, code, path),
        code=code,
        severity=severity,
        path=path,
        message=message,
        action=action,
        penalty=penalty,
    )


def maintenance_work_item_from_triage(
    task: MaintenanceTask,
    *,
    route: MaintenanceRoute,
    triage_schedule_id: str,
    triage_run_id: str,
    created_at: datetime | None = None,
) -> MaintenanceWorkItem:
    """Create immutable work identity from one completed safety triage."""

    now = created_at or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    return MaintenanceWorkItem(
        id=_stable_id(
            "maintenance-work",
            task.id,
            task.revision_id,
            route,
            triage_schedule_id,
            triage_run_id,
        ),
        maintenance_task_id=task.id,
        assessment_id=task.assessment_id,
        entity=task.entity,
        revision_id=task.revision_id,
        action=task.action,
        issue=task.issue,
        route=route,
        required_capabilities=list(_ROUTE_CAPABILITIES[route]),
        priority=task.priority,
        triage_schedule_id=triage_schedule_id,
        triage_run_id=triage_run_id,
        created_at=now,
        updated_at=now,
    )


def _validated_work_citation_ids(
    entity: KnowledgeEntity,
    citation_ids: list[str],
) -> list[str]:
    normalized = sorted({item.strip() for item in citation_ids if item.strip()})
    if not normalized:
        raise ValueError("maintenance proposal requires at least one citation")
    known = {citation.id for citation in entity.citations}
    unknown = sorted(set(normalized) - known)
    if unknown:
        raise ValueError(
            "maintenance proposal references citations outside the pinned revision: "
            + ", ".join(unknown)
        )
    return normalized


def _validate_work_entity(
    work_item: MaintenanceWorkItem,
    entity: KnowledgeEntity,
) -> None:
    if work_item.entity.id != entity.ref.id:
        raise ValueError("maintenance work targets another entity")
    if work_item.revision_id != entity.revision.revision_id:
        raise ValueError("maintenance work revision is no longer current")


def build_translation_work_proposal(
    work_item: MaintenanceWorkItem,
    entity: KnowledgeEntity,
    *,
    target_locale: str,
    translated_name: str | None,
    translated_description: str | None,
    citation_ids: list[str],
    confidence: float,
) -> GovernedProposal:
    """Build a medium-risk translation proposal from an Agent submission."""

    _validate_work_entity(work_item, entity)
    if work_item.route != "translation-evidence":
        raise ValueError("maintenance work is not a translation-evidence route")
    locale = target_locale.strip()
    if not locale or work_item.issue.path != f"/locales/{locale}":
        raise ValueError("translation locale does not match the quality issue")
    citations = _validated_work_citation_ids(entity, citation_ids)
    name = translated_name.strip() if translated_name else None
    description = translated_description.strip() if translated_description else None
    missing_name = locale not in {item.locale for item in entity.names if item.value.strip()}
    missing_description = locale not in {
        item.locale for item in entity.description if item.value.strip()
    }
    if not missing_name and not missing_description:
        raise ValueError("translation locale is already complete")
    if missing_name and not name:
        raise ValueError("translation work requires the missing localized name")
    if missing_description and not description:
        raise ValueError("translation work requires the missing localized description")
    if not 0 <= confidence <= 1:
        raise ValueError("translation confidence must be between 0 and 1")

    operations: list[ChangeOperation] = []
    if missing_name:
        operations.append(
            ChangeOperation(
                operation="add",
                path="/names/-",
                after={
                    "locale": locale,
                    "value": name,
                    "machineGenerated": True,
                },
                citation_ids=citations,
                confidence=confidence,
                machine_generated=True,
            )
        )
    if missing_description:
        operations.append(
            ChangeOperation(
                operation="add",
                path="/description/-",
                after={
                    "locale": locale,
                    "value": description,
                    "machineGenerated": True,
                },
                citation_ids=citations,
                confidence=confidence,
                machine_generated=True,
            )
        )
    proposal_id = _stable_id(
        "proposal-maintenance-translation",
        work_item.id,
        work_item.revision_id,
    )
    return GovernedProposal(
        proposal=AgentProposal(
            id=proposal_id,
            entity_id=entity.ref.id,
            proposal_type="translation",
            operations=operations,
            risk="medium",
            status="proposed",
            agent_run_id=f"maintenance-work:{work_item.id}:attempt:{work_item.attempt}",
            impact={
                "entityCount": 1,
                "operationCount": len(operations),
                "citationCount": len(citations),
            },
        )
    )


def build_taxonomy_work_proposal(
    work_item: MaintenanceWorkItem,
    entity: KnowledgeEntity,
    *,
    taxonomy_node_id: str,
    allowed_taxonomy_node_ids: set[str],
    citation_ids: list[str],
    confidence: float,
) -> GovernedProposal:
    """Build a medium-risk classification proposal from an Agent submission."""

    _validate_work_entity(work_item, entity)
    if work_item.route != "taxonomy-review":
        raise ValueError("maintenance work is not a taxonomy-review route")
    node_id = taxonomy_node_id.strip()
    if not node_id or node_id not in allowed_taxonomy_node_ids:
        raise ValueError("taxonomy node is not allowed for the entity type")
    if node_id in entity.taxonomy_node_ids:
        raise ValueError("entity is already assigned to the taxonomy node")
    citations = _validated_work_citation_ids(entity, citation_ids)
    if not 0 <= confidence <= 1:
        raise ValueError("taxonomy confidence must be between 0 and 1")
    after = sorted({*entity.taxonomy_node_ids, node_id})
    proposal_id = _stable_id(
        "proposal-maintenance-taxonomy",
        work_item.id,
        work_item.revision_id,
    )
    return GovernedProposal(
        proposal=AgentProposal(
            id=proposal_id,
            entity_id=entity.ref.id,
            proposal_type="relation",
            operations=[
                ChangeOperation(
                    operation="replace",
                    path="/taxonomyNodeIds",
                    before=list(entity.taxonomy_node_ids),
                    after=after,
                    citation_ids=citations,
                    confidence=confidence,
                    machine_generated=True,
                )
            ],
            risk="medium",
            status="proposed",
            agent_run_id=f"maintenance-work:{work_item.id}:attempt:{work_item.attempt}",
            impact={
                "entityCount": 1,
                "operationCount": 1,
                "citationCount": len(citations),
            },
        )
    )


def assess_entity_quality(
    entity: KnowledgeEntity,
    profile: QualityProfile,
    *,
    assessed_at: datetime | None = None,
) -> QualityAssessment:
    """Evaluate an entity without side effects or executable user rules."""

    if entity.ref.type_id != profile.entity_type_id:
        raise ValueError(
            f"quality profile {profile.id} targets {profile.entity_type_id}, "
            f"not {entity.ref.type_id}"
        )
    now = assessed_at or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("assessed_at must be timezone-aware")
    assessment_id = _stable_id(
        "quality-assessment",
        entity.ref.id,
        entity.revision.revision_id,
        profile.id,
        profile.schema_version,
    )
    issues: list[QualityIssue] = []

    claim_attributes = {claim.attribute_definition_id for claim in entity.claims}
    for attribute_id in sorted(set(profile.required_attribute_ids) - claim_attributes):
        issues.append(
            _issue(
                assessment_key=assessment_id,
                code="missing-required-attribute",
                severity="error",
                path=f"/claims/{attribute_id}",
                message=f"缺少质量档案要求的属性 {attribute_id}",
                action="enrich-attribute",
                penalty=24,
            )
        )

    localized_fields = {
        "names": {item.locale for item in entity.names if item.value.strip()},
        "description": {item.locale for item in entity.description if item.value.strip()},
    }
    for locale in profile.required_locales:
        missing_fields = [
            field for field, locales in localized_fields.items() if locale not in locales
        ]
        if missing_fields:
            issues.append(
                _issue(
                    assessment_key=assessment_id,
                    code="missing-locale",
                    severity="warning",
                    path=f"/locales/{locale}",
                    message=(f"语言 {locale} 缺少字段：" + "、".join(missing_fields)),
                    action="translate",
                    penalty=12,
                )
            )

    if len(entity.citations) < profile.minimum_citations:
        issues.append(
            _issue(
                assessment_key=assessment_id,
                code="insufficient-citations",
                severity="error",
                path="/citations",
                message=(f"引用数量 {len(entity.citations)} 低于要求 {profile.minimum_citations}"),
                action="add-citation",
                penalty=22,
            )
        )

    if len(entity.sections) < profile.minimum_sections:
        issues.append(
            _issue(
                assessment_key=assessment_id,
                code="insufficient-sections",
                severity="warning",
                path="/sections",
                message=(f"章节数量 {len(entity.sections)} 低于要求 {profile.minimum_sections}"),
                action="add-section",
                penalty=14,
            )
        )

    uncited_sections = [section.id for section in entity.sections if not section.citation_ids]
    uncited_claims = [claim.id for claim in entity.claims if not claim.citation_ids]
    if uncited_sections or uncited_claims:
        issues.append(
            _issue(
                assessment_key=assessment_id,
                code="uncited-content",
                severity="error",
                path="/content/evidence",
                message=(f"{len(uncited_sections)} 个章节与 {len(uncited_claims)} 个声明缺少引用"),
                action="add-citation",
                penalty=20,
            )
        )

    if not entity.taxonomy_node_ids:
        issues.append(
            _issue(
                assessment_key=assessment_id,
                code="missing-taxonomy",
                severity="warning",
                path="/taxonomyNodeIds",
                message="条目尚未归入任何分类",
                action="classify",
                penalty=12,
            )
        )

    if profile.freshness_days is not None and entity.revision.created_at < now - timedelta(
        days=profile.freshness_days
    ):
        issues.append(
            _issue(
                assessment_key=assessment_id,
                code="stale-revision",
                severity="warning",
                path="/revision/createdAt",
                message=(f"当前修订已超过 {profile.freshness_days} 天未更新"),
                action="refresh-evidence",
                penalty=10,
            )
        )

    score = max(0, 100 - sum(item.penalty for item in issues))
    if score < profile.critical_score or any(item.severity == "error" for item in issues):
        status: Literal["healthy", "attention", "critical"] = "critical"
    elif score < profile.healthy_score or issues:
        status = "attention"
    else:
        status = "healthy"
    return QualityAssessment(
        id=assessment_id,
        entity=entity.ref,
        revision_id=entity.revision.revision_id,
        data_version=entity.revision.data_version,
        profile_id=profile.id,
        profile_version=profile.schema_version,
        status=status,
        score=score,
        issues=issues,
        assessed_at=now,
    )


def maintenance_tasks_from_assessment(
    assessment: QualityAssessment,
    *,
    created_at: datetime | None = None,
) -> list[MaintenanceTask]:
    now = created_at or assessment.assessed_at
    tasks: list[MaintenanceTask] = []
    for issue in assessment.issues:
        if issue.severity == "error" and assessment.score < 50:
            priority: Literal["low", "medium", "high", "critical"] = "critical"
        elif issue.severity == "error":
            priority = "high"
        elif assessment.score < 75:
            priority = "medium"
        else:
            priority = "low"
        tasks.append(
            MaintenanceTask(
                id=_stable_id(
                    "maintenance-task",
                    assessment.id,
                    issue.code,
                    issue.path,
                ),
                assessment_id=assessment.id,
                entity=assessment.entity,
                revision_id=assessment.revision_id,
                profile_id=assessment.profile_id,
                profile_version=assessment.profile_version,
                issue=issue,
                action=issue.action,
                priority=priority,
                created_at=now,
                updated_at=now,
            )
        )
    return tasks
