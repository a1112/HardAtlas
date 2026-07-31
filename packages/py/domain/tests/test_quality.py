from datetime import UTC, datetime, timedelta

from hardatlas_domain import (
    KnowledgeEntity,
    QualityProfile,
    assess_entity_quality,
    build_taxonomy_work_proposal,
    build_translation_work_proposal,
    maintenance_tasks_from_assessment,
    maintenance_work_item_from_triage,
)


def entity(*, sparse: bool = False) -> KnowledgeEntity:
    now = datetime(2026, 7, 1, tzinfo=UTC)
    citation = {
        "id": "citation-1",
        "sourceId": "source-1",
        "sourceTitle": "权威资料",
        "sourceTier": "authoritative",
        "retrievedAt": now.isoformat(),
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
            "taxonomyNodeIds": [] if sparse else ["taxonomy-animals"],
            "claims": []
            if sparse
            else [
                {
                    "id": "claim-scientific-name",
                    "attributeDefinitionId": "attr-scientific-name",
                    "originalValue": "Panthera uncia",
                    "displayValue": [{"locale": "zh-CN", "value": "Panthera uncia"}],
                    "confidence": 0.99,
                    "citationIds": ["citation-1"],
                    "revisionId": "revision-1",
                }
            ],
            "sections": []
            if sparse
            else [
                {
                    "id": "section-intro",
                    "key": "intro",
                    "heading": [{"locale": "zh-CN", "value": "概述"}],
                    "body": [{"locale": "zh-CN", "value": "生活在高山地区。"}],
                    "citationIds": ["citation-1"],
                    "order": 1,
                }
            ],
            "relationships": [],
            "citations": [] if sparse else [citation],
            "revision": {
                "revisionId": "revision-1",
                "dataVersion": "data-1",
                "schemaVersion": "1.0.0",
                "policyVersion": "1.0.0",
                "createdAt": now.isoformat(),
            },
            "publicationStatus": "published",
        }
    )


def profile(**overrides: object) -> QualityProfile:
    payload: dict[str, object] = {
        "id": "quality-animal",
        "entityTypeId": "type-animal",
        "schemaVersion": "1.0.0",
        "requiredLocales": ["zh-CN"],
        "requiredAttributeIds": ["attr-scientific-name"],
        "minimumCitations": 1,
        "minimumSections": 1,
        "freshnessDays": 365,
    }
    payload.update(overrides)
    return QualityProfile.model_validate(payload)


def test_assessment_is_healthy_when_profile_is_satisfied() -> None:
    assessment = assess_entity_quality(
        entity(),
        profile(),
        assessed_at=datetime(2026, 7, 29, tzinfo=UTC),
    )

    assert assessment.status == "healthy"
    assert assessment.score == 100
    assert assessment.issues == []
    assert maintenance_tasks_from_assessment(assessment) == []


def test_assessment_exposes_deterministic_agent_tasks() -> None:
    assessed_at = datetime(2026, 7, 29, tzinfo=UTC)
    first = assess_entity_quality(entity(sparse=True), profile(), assessed_at=assessed_at)
    second = assess_entity_quality(entity(sparse=True), profile(), assessed_at=assessed_at)

    assert first.id == second.id
    assert first.status == "critical"
    assert {issue.code for issue in first.issues} == {
        "missing-required-attribute",
        "insufficient-citations",
        "insufficient-sections",
        "missing-taxonomy",
    }
    tasks = maintenance_tasks_from_assessment(first)
    assert [task.id for task in tasks] == [
        task.id for task in maintenance_tasks_from_assessment(second)
    ]
    assert all(task.status == "open" for task in tasks)
    assert {task.action for task in tasks} == {
        "enrich-attribute",
        "add-citation",
        "add-section",
        "classify",
    }


def test_assessment_detects_missing_locale_uncited_content_and_staleness() -> None:
    item = entity()
    item.description = []
    item.sections[0].citation_ids = []
    item.revision.created_at = datetime.now(UTC) - timedelta(days=400)

    assessment = assess_entity_quality(
        item,
        profile(requiredLocales=["zh-CN", "en-US"]),
        assessed_at=datetime.now(UTC),
    )

    assert {issue.code for issue in assessment.issues} == {
        "missing-locale",
        "uncited-content",
        "stale-revision",
    }


def test_profile_cannot_assess_another_entity_type() -> None:
    item = entity()
    item.ref.type_id = "type-plant"

    try:
        assess_entity_quality(item, profile())
    except ValueError as error:
        assert "not type-plant" in str(error)
    else:
        raise AssertionError("mismatched profile must fail")


def test_triage_creates_revision_pinned_non_proposal_work() -> None:
    assessment = assess_entity_quality(
        entity(sparse=True),
        profile(),
        assessed_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    task = next(
        item for item in maintenance_tasks_from_assessment(assessment) if item.action == "classify"
    )

    work = maintenance_work_item_from_triage(
        task,
        route="taxonomy-review",
        triage_schedule_id="schedule-quality-1",
        triage_run_id="run-schedule-quality-1",
        created_at=datetime(2026, 7, 29, 13, tzinfo=UTC),
    )

    assert work.status == "queued"
    assert work.revision_id == task.revision_id
    assert work.route == "taxonomy-review"
    assert work.required_capabilities == [
        "taxonomy-read",
        "taxonomy-propose",
        "evidence-create",
    ]
    assert work.requires_evidence is True
    assert work.proposal_eligible is False


def test_translation_work_builds_medium_risk_evidence_proposal() -> None:
    item = entity()
    assessment = assess_entity_quality(
        item,
        profile(requiredLocales=["zh-CN", "en-US"]),
        assessed_at=datetime(2026, 7, 29, 13, tzinfo=UTC),
    )
    task = next(
        task
        for task in maintenance_tasks_from_assessment(assessment)
        if task.action == "translate"
    )
    work = maintenance_work_item_from_triage(
        task,
        route="translation-evidence",
        triage_schedule_id="schedule-translation-1",
        triage_run_id="run-translation-1",
    )

    governed = build_translation_work_proposal(
        work,
        item,
        target_locale="en-US",
        translated_name="Snow leopard",
        translated_description="A high-mountain felid.",
        citation_ids=["citation-1"],
        confidence=0.91,
    )

    assert governed.proposal.proposal_type == "translation"
    assert governed.proposal.risk == "medium"
    assert [operation.path for operation in governed.proposal.operations] == [
        "/names/-",
        "/description/-",
    ]
    assert all(
        operation.citation_ids == ["citation-1"]
        for operation in governed.proposal.operations
    )


def test_taxonomy_work_builds_reviewable_relation_proposal() -> None:
    item = entity()
    item.taxonomy_node_ids = []
    assessment = assess_entity_quality(
        item,
        profile(),
        assessed_at=datetime(2026, 7, 29, 13, tzinfo=UTC),
    )
    task = next(
        task
        for task in maintenance_tasks_from_assessment(assessment)
        if task.action == "classify"
    )
    work = maintenance_work_item_from_triage(
        task,
        route="taxonomy-review",
        triage_schedule_id="schedule-taxonomy-1",
        triage_run_id="run-taxonomy-1",
    )

    governed = build_taxonomy_work_proposal(
        work,
        item,
        taxonomy_node_id="taxonomy-animals",
        allowed_taxonomy_node_ids={"taxonomy-animals"},
        citation_ids=["citation-1"],
        confidence=0.94,
    )

    assert governed.proposal.proposal_type == "relation"
    assert governed.proposal.risk == "medium"
    assert governed.proposal.operations[0].path == "/taxonomyNodeIds"
    assert governed.proposal.operations[0].before == []
    assert governed.proposal.operations[0].after == ["taxonomy-animals"]
