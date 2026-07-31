import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from .knowledge import (
    AgentProposal,
    AttributeDataType,
    AttributeDefinition,
    ChangeOperation,
    Citation,
    ClaimValue,
    ContentSection,
    EntityRef,
    EntityType,
    KnowledgeEntity,
    KnowledgeModel,
    LocalizedText,
    Relationship,
    RevisionContext,
)


class DraftSection(KnowledgeModel):
    key: str
    heading: list[LocalizedText]
    body: list[LocalizedText]
    citation_ids: list[str] = Field(default_factory=list)


class EntityDraft(KnowledgeModel):
    id: str
    slug: str
    type_id: str
    names: list[LocalizedText]
    aliases: list[LocalizedText] = Field(default_factory=list)
    description: list[LocalizedText]
    taxonomy_node_ids: list[str]
    attribute_values: dict[str, Any]
    sections: list[DraftSection] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    citations: list[Citation]


class AuthoringDraftRecord(KnowledgeModel):
    id: str
    workspace_id: str
    created_by: str
    mode: Literal["create", "revise"]
    draft: EntityDraft
    base_revision_id: str | None = None
    status: Literal["editing", "submitted", "abandoned"] = "editing"
    proposal_id: str | None = None
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def validate_state(self) -> None:
        if self.mode == "create" and self.base_revision_id is not None:
            raise ValueError("creation draft cannot pin a base revision")
        if self.mode == "revise" and not self.base_revision_id:
            raise ValueError("revision draft requires a base revision")
        if self.status == "submitted" and not self.proposal_id:
            raise ValueError("submitted draft requires a proposal id")
        if self.status != "submitted" and self.proposal_id is not None:
            raise ValueError("only submitted drafts may link a proposal")


class DraftIssue(KnowledgeModel):
    severity: Literal["error", "warning"]
    code: str
    path: str
    message: str


class DraftValidation(KnowledgeModel):
    valid: bool
    entity_type_id: str
    schema_version: str
    issues: list[DraftIssue]


def _matches_type(value: Any, data_type: AttributeDataType) -> bool:
    if data_type in {AttributeDataType.TEXT, AttributeDataType.RICH_TEXT}:
        return isinstance(value, str)
    if data_type == AttributeDataType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type == AttributeDataType.DECIMAL:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if data_type == AttributeDataType.BOOLEAN:
        return isinstance(value, bool)
    if data_type in {AttributeDataType.DATE, AttributeDataType.DATE_RANGE}:
        return isinstance(value, (str, dict))
    if data_type == AttributeDataType.MEASUREMENT:
        return (
            isinstance(value, dict)
            and isinstance(value.get("unit"), str)
            and (
                isinstance(value.get("value"), (int, float))
                or (
                    isinstance(value.get("min"), (int, float))
                    and isinstance(value.get("max"), (int, float))
                )
            )
        )
    if data_type == AttributeDataType.ENUM:
        return isinstance(value, str)
    if data_type in {AttributeDataType.ENTITY_REF, AttributeDataType.MEDIA_REF}:
        return isinstance(value, (str, dict))
    if data_type == AttributeDataType.GEO:
        return isinstance(value, dict)
    return True


def validate_entity_draft(
    draft: EntityDraft,
    entity_type: EntityType,
    attributes: list[AttributeDefinition],
    *,
    known_taxonomy_node_ids: set[str],
    taxonomy_space_by_id: dict[str, str] | None = None,
) -> DraftValidation:
    issues: list[DraftIssue] = []
    definitions = {item.id: item for item in attributes}

    if draft.type_id != entity_type.id:
        issues.append(
            DraftIssue(
                severity="error",
                code="entity-type-mismatch",
                path="/typeId",
                message="草稿类型与所选 Schema 不一致",
            )
        )
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", draft.slug):
        issues.append(
            DraftIssue(
                severity="error",
                code="invalid-slug",
                path="/slug",
                message="slug 只能使用小写字母、数字和连字符",
            )
        )
    if not draft.names or any(not item.value.strip() for item in draft.names):
        issues.append(
            DraftIssue(
                severity="error",
                code="missing-name",
                path="/names",
                message="至少需要一个非空名称",
            )
        )
    if not draft.description:
        issues.append(
            DraftIssue(
                severity="warning",
                code="missing-description",
                path="/description",
                message="建议提供条目摘要",
            )
        )
    unknown_taxonomy = sorted(set(draft.taxonomy_node_ids) - known_taxonomy_node_ids)
    if unknown_taxonomy:
        issues.append(
            DraftIssue(
                severity="error",
                code="unknown-taxonomy",
                path="/taxonomyNodeIds",
                message=f"未知分类节点：{', '.join(unknown_taxonomy)}",
            )
        )
    mismatched_taxonomy = sorted(
        node_id
        for node_id in draft.taxonomy_node_ids
        if taxonomy_space_by_id
        and taxonomy_space_by_id.get(node_id) not in {None, entity_type.space_id}
    )
    if mismatched_taxonomy:
        issues.append(
            DraftIssue(
                severity="error",
                code="taxonomy-space-mismatch",
                path="/taxonomyNodeIds",
                message=f"分类节点不属于当前知识空间：{', '.join(mismatched_taxonomy)}",
            )
        )
    disallowed_taxonomy = sorted(
        set(draft.taxonomy_node_ids) - set(entity_type.allowed_taxonomy_node_ids)
        if entity_type.allowed_taxonomy_node_ids
        else set()
    )
    if disallowed_taxonomy:
        issues.append(
            DraftIssue(
                severity="error",
                code="taxonomy-not-allowed",
                path="/taxonomyNodeIds",
                message=f"实体类型不允许该分类节点：{', '.join(disallowed_taxonomy)}",
            )
        )
    if not draft.citations:
        issues.append(
            DraftIssue(
                severity="error",
                code="missing-evidence",
                path="/citations",
                message="新条目至少需要一个来源证据",
            )
        )
    known_citation_ids = {citation.id for citation in draft.citations}
    relationship_ids: set[str] = set()
    for index, relationship in enumerate(draft.relationships):
        path = f"/relationships/{index}"
        if relationship.id in relationship_ids:
            issues.append(
                DraftIssue(
                    severity="error",
                    code="duplicate-relationship-id",
                    path=f"{path}/id",
                    message="关系 ID 在条目内必须唯一",
                )
            )
        relationship_ids.add(relationship.id)
        if relationship.source.id != draft.id or relationship.source.type_id != draft.type_id:
            issues.append(
                DraftIssue(
                    severity="error",
                    code="relationship-source-mismatch",
                    path=f"{path}/source",
                    message="关系源必须是当前条目",
                )
            )
        if relationship.type_id not in entity_type.allowed_relationship_type_ids:
            issues.append(
                DraftIssue(
                    severity="error",
                    code="relationship-not-allowed",
                    path=f"{path}/typeId",
                    message="当前实体类型不允许该关系",
                )
            )
        unknown_citations = sorted(
            set(relationship.citation_ids) - known_citation_ids
        )
        if unknown_citations:
            issues.append(
                DraftIssue(
                    severity="error",
                    code="relationship-unknown-citation",
                    path=f"{path}/citationIds",
                    message=f"关系引用未知证据：{', '.join(unknown_citations)}",
                )
            )

    allowed_ids = set(entity_type.attribute_definition_ids)
    for attribute_id in sorted(set(draft.attribute_values) - allowed_ids):
        issues.append(
            DraftIssue(
                severity="error",
                code="attribute-not-allowed",
                path=f"/attributeValues/{attribute_id}",
                message="字段不属于当前实体类型",
            )
        )

    for attribute_id in entity_type.attribute_definition_ids:
        definition = definitions.get(attribute_id)
        if definition is None:
            issues.append(
                DraftIssue(
                    severity="error",
                    code="schema-reference-missing",
                    path=f"/attributeValues/{attribute_id}",
                    message="实体类型引用了未注册字段",
                )
            )
            continue
        value = draft.attribute_values.get(attribute_id)
        if value is None:
            if definition.required:
                issues.append(
                    DraftIssue(
                        severity="error",
                        code="required",
                        path=f"/attributeValues/{attribute_id}",
                        message="缺少必填字段",
                    )
                )
            continue
        values = value if definition.cardinality == "many" else [value]
        if definition.cardinality == "many" and not isinstance(value, list):
            issues.append(
                DraftIssue(
                    severity="error",
                    code="cardinality",
                    path=f"/attributeValues/{attribute_id}",
                    message="该字段需要数组值",
                )
            )
            continue
        for item in values:
            if not _matches_type(item, definition.data_type):
                issues.append(
                    DraftIssue(
                        severity="error",
                        code="data-type",
                        path=f"/attributeValues/{attribute_id}",
                        message=f"值不符合 {definition.data_type.value} 类型",
                    )
                )
                break
            if definition.data_type == AttributeDataType.ENUM and (
                definition.enum_values is None or item not in definition.enum_values
            ):
                issues.append(
                    DraftIssue(
                        severity="error",
                        code="enum",
                        path=f"/attributeValues/{attribute_id}",
                        message="值不在允许的枚举范围内",
                    )
                )
                break
            if isinstance(item, str):
                min_length = definition.validation.get("minLength")
                max_length = definition.validation.get("maxLength")
                if isinstance(min_length, int) and len(item) < min_length:
                    issues.append(
                        DraftIssue(
                            severity="error",
                            code="min-length",
                            path=f"/attributeValues/{attribute_id}",
                            message=f"文本长度不能少于 {min_length}",
                        )
                    )
                if isinstance(max_length, int) and len(item) > max_length:
                    issues.append(
                        DraftIssue(
                            severity="error",
                            code="max-length",
                            path=f"/attributeValues/{attribute_id}",
                            message=f"文本长度不能超过 {max_length}",
                        )
                    )

    return DraftValidation(
        valid=not any(issue.severity == "error" for issue in issues),
        entity_type_id=entity_type.id,
        schema_version=entity_type.schema_version,
        issues=issues,
    )


def validate_published_entity_claims(
    entity: KnowledgeEntity,
    entity_type: EntityType,
    attributes: list[AttributeDefinition],
) -> list[DraftIssue]:
    issues: list[DraftIssue] = []
    definitions = {item.id: item for item in attributes}
    allowed = set(entity_type.attribute_definition_ids)
    seen: dict[str, int] = {}
    for claim in entity.claims:
        attribute_id = claim.attribute_definition_id
        seen[attribute_id] = seen.get(attribute_id, 0) + 1
        definition = definitions.get(attribute_id)
        if attribute_id not in allowed or definition is None:
            issues.append(
                DraftIssue(
                    severity="error",
                    code="claim-attribute-not-allowed",
                    path=f"/claims/{claim.id}",
                    message=f"Claim 引用未注册或不允许的字段：{attribute_id}",
                )
            )
            continue
        value = (
            claim.normalized_value
            if claim.normalized_value is not None
            else claim.original_value
        )
        values = value if definition.cardinality == "many" else [value]
        if definition.cardinality == "many" and not isinstance(value, list):
            issues.append(
                DraftIssue(
                    severity="error",
                    code="claim-cardinality",
                    path=f"/claims/{claim.id}",
                    message="多值字段的规范值必须是数组",
                )
            )
            continue
        if any(not _matches_type(item, definition.data_type) for item in values):
            issues.append(
                DraftIssue(
                    severity="error",
                    code="claim-data-type",
                    path=f"/claims/{claim.id}",
                    message=f"Claim 不符合 {definition.data_type.value} 类型",
                )
            )
        if definition.data_type == AttributeDataType.ENUM and any(
            definition.enum_values is None or item not in definition.enum_values
            for item in values
        ):
            issues.append(
                DraftIssue(
                    severity="error",
                    code="claim-enum",
                    path=f"/claims/{claim.id}",
                    message="Claim 值不在当前枚举范围内",
                )
            )
    for attribute_id in entity_type.attribute_definition_ids:
        definition = definitions.get(attribute_id)
        if definition is None:
            continue
        count = seen.get(attribute_id, 0)
        if definition.required and count == 0:
            issues.append(
                DraftIssue(
                    severity="error",
                    code="claim-required",
                    path=f"/claims/{attribute_id}",
                    message="发布实体缺少必填 Claim",
                )
            )
        if definition.cardinality == "one" and count > 1:
            issues.append(
                DraftIssue(
                    severity="error",
                    code="claim-duplicate",
                    path=f"/claims/{attribute_id}",
                    message="单值字段存在多个 Claim",
                )
            )
    return issues


def build_entity_from_draft(
    draft: EntityDraft,
    entity_type: EntityType,
    attributes: list[AttributeDefinition],
) -> KnowledgeEntity:
    definitions = {item.id: item for item in attributes}
    citation_ids = [item.id for item in draft.citations]
    digest = hashlib.sha256(
        json.dumps(
            draft.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()[:12]
    revision_id = f"draft-{draft.slug}-{digest}"
    claims: list[ClaimValue] = []
    for attribute_id in entity_type.attribute_definition_ids:
        if attribute_id not in draft.attribute_values:
            continue
        definition = definitions[attribute_id]
        value = draft.attribute_values[attribute_id]
        display = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
        unit = value.get("unit") if isinstance(value, dict) else None
        claims.append(
            ClaimValue(
                id=f"claim-{draft.id}-{attribute_id}",
                attribute_definition_id=attribute_id,
                original_value=value,
                normalized_value=value,
                display_value=[
                    LocalizedText(
                        locale=draft.names[0].locale,
                        value=display,
                        machine_generated=True,
                    )
                ],
                unit=unit if definition.data_type == AttributeDataType.MEASUREMENT else None,
                confidence=1,
                citation_ids=citation_ids,
                revision_id=revision_id,
            )
        )
    return KnowledgeEntity(
        ref=EntityRef(
            id=draft.id,
            slug=draft.slug,
            type_id=draft.type_id,
            canonical_name=draft.names[0].value,
        ),
        names=draft.names,
        aliases=draft.aliases,
        description=draft.description,
        taxonomy_node_ids=draft.taxonomy_node_ids,
        claims=claims,
        sections=[
            ContentSection(
                id=f"section-{draft.id}-{index + 1}",
                key=section.key,
                heading=section.heading,
                body=section.body,
                citation_ids=section.citation_ids or citation_ids,
                order=index,
            )
            for index, section in enumerate(draft.sections)
        ],
        relationships=[
            relationship.model_copy(
                update={
                    "source": EntityRef(
                        id=draft.id,
                        slug=draft.slug,
                        type_id=draft.type_id,
                        canonical_name=draft.names[0].value,
                    ),
                    "revision_id": revision_id,
                }
            )
            for relationship in draft.relationships
        ],
        citations=draft.citations,
        revision=RevisionContext(
            revision_id=revision_id,
            data_version="draft",
            schema_version=entity_type.schema_version,
            policy_version="unpublished",
            created_at=datetime.now(UTC),
        ),
        publication_status="draft",
    )


def build_creation_proposal(
    *,
    proposal_id: str,
    draft: EntityDraft,
    entity_type: EntityType,
    attributes: list[AttributeDefinition],
    agent_run_id: str,
    confidence: Annotated[float, Field(ge=0, le=1)],
    risk: Literal["low", "medium", "high", "critical"],
) -> AgentProposal:
    entity = build_entity_from_draft(draft, entity_type, attributes)
    return AgentProposal(
        id=proposal_id,
        entity_id=draft.id,
        proposal_type="content",
        operations=[
            ChangeOperation(
                operation="add",
                path="/entity",
                after=entity.model_dump(mode="json", by_alias=True),
                citation_ids=[item.id for item in draft.citations],
                confidence=confidence,
            )
        ],
        risk=risk,
        status="proposed",
        agent_run_id=agent_run_id,
        impact={"entityCount": 1, "claimCount": len(entity.claims)},
    )


def build_draft_from_entity(entity: KnowledgeEntity) -> EntityDraft:
    """Project one immutable entity revision back into the authoring contract."""
    attribute_values: dict[str, Any] = {}
    for claim in entity.claims:
        value = (
            claim.normalized_value
            if claim.normalized_value is not None
            else claim.original_value
        )
        existing = attribute_values.get(claim.attribute_definition_id)
        if existing is None:
            attribute_values[claim.attribute_definition_id] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            attribute_values[claim.attribute_definition_id] = [existing, value]
    return EntityDraft(
        id=entity.ref.id,
        slug=entity.ref.slug,
        type_id=entity.ref.type_id,
        names=entity.names,
        aliases=entity.aliases,
        description=entity.description,
        taxonomy_node_ids=entity.taxonomy_node_ids,
        attribute_values=attribute_values,
        sections=[
            DraftSection(
                key=section.key,
                heading=section.heading,
                body=section.body,
                citation_ids=section.citation_ids,
            )
            for section in sorted(entity.sections, key=lambda item: item.order)
        ],
        relationships=entity.relationships,
        citations=entity.citations,
    )


def build_revision_proposal(
    *,
    proposal_id: str,
    draft: EntityDraft,
    current: KnowledgeEntity,
    entity_type: EntityType,
    attributes: list[AttributeDefinition],
    base_revision_id: str,
    agent_run_id: str,
    confidence: Annotated[float, Field(ge=0, le=1)],
    risk: Literal["low", "medium", "high", "critical"],
) -> AgentProposal:
    """Build an optimistic, evidence-backed top-level revision diff."""
    if current.revision.revision_id != base_revision_id:
        raise ValueError("base entity revision is stale")
    if draft.id != current.ref.id:
        raise ValueError("revision draft cannot change entity id")
    if draft.type_id != current.ref.type_id:
        raise ValueError("revision draft cannot change entity type")
    if draft.slug != current.ref.slug:
        raise ValueError("revision draft cannot change the stable slug")

    proposed = build_entity_from_draft(draft, entity_type, attributes)
    proposed = proposed.model_copy(
        update={
            "relationships": [
                relationship.model_copy(
                    update={"revision_id": current.revision.revision_id}
                )
                for relationship in proposed.relationships
            ],
            "ref": current.ref.model_copy(
                update={"canonical_name": draft.names[0].value}
            ),
        }
    )
    current_document = current.model_dump(mode="json", by_alias=True)
    proposed_document = proposed.model_dump(mode="json", by_alias=True)
    paths = (
        "/ref/canonicalName",
        "/names",
        "/aliases",
        "/description",
        "/taxonomyNodeIds",
        "/claims",
        "/sections",
        "/relationships",
        "/citations",
    )
    citation_ids = [item.id for item in draft.citations]
    operations = [
        ChangeOperation(
            operation="replace",
            path=path,
            before=_pointer_value(current_document, path),
            after=_pointer_value(proposed_document, path),
            citation_ids=citation_ids,
            confidence=confidence,
        )
        for path in paths
        if _pointer_value(current_document, path)
        != _pointer_value(proposed_document, path)
    ]
    if not operations:
        raise ValueError("revision draft does not change the entity")
    return AgentProposal(
        id=proposal_id,
        entity_id=current.ref.id,
        proposal_type="content",
        operations=operations,
        risk=risk,
        status="proposed",
        agent_run_id=agent_run_id,
        impact={
            "entityCount": 1,
            "operationCount": len(operations),
        },
    )


def _pointer_value(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for token in path.removeprefix("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current
