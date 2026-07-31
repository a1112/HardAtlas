from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from .knowledge import (
    AttributeDefinition,
    EntityType,
    KnowledgeModel,
    KnowledgeSpace,
    RelationshipType,
    TaxonomyNode,
    ViewDefinition,
)
from .quality import QualityProfile


class DomainPackRollbackSnapshot(KnowledgeModel):
    schema_activations: dict[str, str | None] = Field(default_factory=dict)
    spaces: dict[str, KnowledgeSpace | None] = Field(default_factory=dict)
    taxonomy_nodes: dict[str, TaxonomyNode | None] = Field(default_factory=dict)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DomainPackRelease(KnowledgeModel):
    id: str
    name: str
    version: str
    kernel_version: str
    locales: list[str] = Field(min_length=1)
    status: Literal["draft", "published", "archived", "rolled-back"] = "draft"
    spaces: list[KnowledgeSpace] = Field(default_factory=list)
    taxonomy_nodes: list[TaxonomyNode] = Field(default_factory=list)
    entity_types: list[EntityType] = Field(default_factory=list)
    attributes: list[AttributeDefinition] = Field(default_factory=list)
    relationship_types: list[RelationshipType] = Field(default_factory=list)
    views: list[ViewDefinition] = Field(default_factory=list)
    quality_profiles: list[QualityProfile] = Field(default_factory=list)
    citation_ids: list[str] = Field(min_length=1)
    proposal_id: str
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None
    rollback_snapshot: DomainPackRollbackSnapshot | None = None
    rolled_back_at: datetime | None = None
    rollback_to_version: str | None = None


class DomainPackDiffEntry(KnowledgeModel):
    collection: Literal[
        "spaces",
        "taxonomyNodes",
        "entityTypes",
        "attributes",
        "relationshipTypes",
        "views",
        "qualityProfiles",
    ]
    document_id: str
    change: Literal["added", "removed", "modified"]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class DomainPackDiff(KnowledgeModel):
    pack_id: str
    from_version: str | None
    to_version: str
    entries: list[DomainPackDiffEntry]
    counts: dict[str, int]


class DomainPackIssue(KnowledgeModel):
    severity: Literal["error", "warning"]
    code: str
    path: str
    message: str


class DomainPackValidation(KnowledgeModel):
    valid: bool
    issues: list[DomainPackIssue]
    counts: dict[str, int]


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def compare_domain_packs(
    before: DomainPackRelease | None,
    after: DomainPackRelease,
) -> DomainPackDiff:
    if before is not None and before.id != after.id:
        raise ValueError("domain pack comparison requires the same pack id")
    collections = (
        ("spaces", "spaces"),
        ("taxonomyNodes", "taxonomy_nodes"),
        ("entityTypes", "entity_types"),
        ("attributes", "attributes"),
        ("relationshipTypes", "relationship_types"),
        ("views", "views"),
        ("qualityProfiles", "quality_profiles"),
    )
    entries: list[DomainPackDiffEntry] = []
    counts = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    for collection, attribute in collections:
        before_documents = {
            item.id: item.model_dump(mode="json", by_alias=True)
            for item in getattr(before, attribute, [])
        }
        after_documents = {
            item.id: item.model_dump(mode="json", by_alias=True)
            for item in getattr(after, attribute)
        }
        for document_id in sorted(before_documents.keys() | after_documents.keys()):
            before_document = before_documents.get(document_id)
            after_document = after_documents.get(document_id)
            if before_document is None:
                change = "added"
            elif after_document is None:
                change = "removed"
            elif before_document != after_document:
                change = "modified"
            else:
                counts["unchanged"] += 1
                continue
            counts[change] += 1
            entries.append(
                DomainPackDiffEntry(
                    collection=collection,
                    document_id=document_id,
                    change=change,
                    before=before_document,
                    after=after_document,
                )
            )
    return DomainPackDiff(
        pack_id=after.id,
        from_version=before.version if before else None,
        to_version=after.version,
        entries=entries,
        counts=counts,
    )


def validate_domain_pack(
    pack: DomainPackRelease,
    *,
    existing_spaces: list[KnowledgeSpace] | None = None,
    existing_taxonomy_nodes: list[TaxonomyNode] | None = None,
    existing_entity_types: list[EntityType] | None = None,
    existing_attributes: list[AttributeDefinition] | None = None,
    existing_relationship_types: list[RelationshipType] | None = None,
    existing_views: list[ViewDefinition] | None = None,
    existing_quality_profiles: list[QualityProfile] | None = None,
) -> DomainPackValidation:
    issues: list[DomainPackIssue] = []

    def issue(
        severity: Literal["error", "warning"],
        code: str,
        path: str,
        message: str,
    ) -> None:
        issues.append(
            DomainPackIssue(
                severity=severity,
                code=code,
                path=path,
                message=message,
            )
        )

    collections = {
        "spaces": pack.spaces,
        "taxonomyNodes": pack.taxonomy_nodes,
        "entityTypes": pack.entity_types,
        "attributes": pack.attributes,
        "relationshipTypes": pack.relationship_types,
        "views": pack.views,
        "qualityProfiles": pack.quality_profiles,
    }
    for path, documents in collections.items():
        for duplicate in sorted(_duplicates(document.id for document in documents)):
            issue(
                "error",
                "duplicate-id",
                f"/{path}",
                f"ID {duplicate} 在 Domain Pack 中重复",
            )

    if not pack.spaces and not pack.entity_types:
        issue(
            "error",
            "empty-pack",
            "/",
            "Domain Pack 至少需要新增一个知识空间或实体类型",
        )

    current_spaces = {item.id: item for item in existing_spaces or []}
    current_nodes = {item.id: item for item in existing_taxonomy_nodes or []}
    current_entity_types = {
        item.id: item for item in existing_entity_types or []
    }
    current_attributes = {item.id: item for item in existing_attributes or []}
    current_relationships = {
        item.id: item for item in existing_relationship_types or []
    }
    current_views = {item.id: item for item in existing_views or []}
    current_quality_profiles = {
        item.id: item for item in existing_quality_profiles or []
    }

    pack_spaces = {item.id: item for item in pack.spaces}
    pack_nodes = {item.id: item for item in pack.taxonomy_nodes}
    pack_entity_types = {item.id: item for item in pack.entity_types}
    pack_attributes = {item.id: item for item in pack.attributes}
    pack_relationships = {item.id: item for item in pack.relationship_types}
    pack_views = {item.id: item for item in pack.views}
    pack_quality_profiles = {
        item.id: item for item in pack.quality_profiles
    }

    all_spaces = current_spaces | pack_spaces
    all_nodes = current_nodes | pack_nodes
    all_entity_types = current_entity_types | pack_entity_types
    all_attributes = current_attributes | pack_attributes
    all_relationships = current_relationships | pack_relationships
    all_views = current_views | pack_views
    all_quality_profiles = current_quality_profiles | pack_quality_profiles

    for space in pack.spaces:
        existing = current_spaces.get(space.id)
        if existing and (
            existing.slug != space.slug
            or existing.name != space.name
            or existing.description != space.description
            or existing.icon_key != space.icon_key
            or not set(existing.root_taxonomy_node_ids).issubset(
                space.root_taxonomy_node_ids
            )
        ):
            issue(
                "error",
                "space-change-requires-migration",
                f"/spaces/{space.id}",
                "已有知识空间只能通过 Domain Pack 增加根分类；其它修改需要迁移",
            )
        for root_id in space.root_taxonomy_node_ids:
            root = all_nodes.get(root_id)
            if root is None or root.space_id != space.id or root.parent_ids:
                issue(
                    "error",
                    "invalid-root-taxonomy",
                    f"/spaces/{space.id}/rootTaxonomyNodeIds",
                    f"{root_id} 必须是同一空间内存在且没有父节点的分类",
                )

    for node in pack.taxonomy_nodes:
        existing = current_nodes.get(node.id)
        if existing and existing != node:
            issue(
                "error",
                "taxonomy-change-requires-migration",
                f"/taxonomyNodes/{node.id}",
                "已有分类节点不可由扩展包直接改写",
            )
        if node.space_id not in all_spaces:
            issue(
                "error",
                "unknown-space",
                f"/taxonomyNodes/{node.id}/spaceId",
                f"知识空间 {node.space_id} 不存在",
            )
        for parent_id in node.parent_ids:
            parent = all_nodes.get(parent_id)
            if parent is None or parent.space_id != node.space_id:
                issue(
                    "error",
                    "invalid-taxonomy-parent",
                    f"/taxonomyNodes/{node.id}/parentIds",
                    f"父分类 {parent_id} 不存在或不属于同一空间",
                )

    def visit(node_id: str, stack: tuple[str, ...]) -> None:
        if node_id in stack:
            issue(
                "error",
                "taxonomy-cycle",
                f"/taxonomyNodes/{node_id}/parentIds",
                "分类层级不能形成循环",
            )
            return
        node = pack_nodes.get(node_id)
        if node is None:
            return
        for parent_id in node.parent_ids:
            visit(parent_id, (*stack, node_id))

    for node_id in pack_nodes:
        visit(node_id, ())

    schema_sets = (
        ("attributes", pack_attributes, current_attributes),
        ("relationshipTypes", pack_relationships, current_relationships),
        ("entityTypes", pack_entity_types, current_entity_types),
        ("views", pack_views, current_views),
        (
            "qualityProfiles",
            pack_quality_profiles,
            current_quality_profiles,
        ),
    )
    for path, proposed, current in schema_sets:
        for document_id, document in proposed.items():
            existing = current.get(document_id)
            if existing is not None and existing != document:
                issue(
                    "error",
                    "schema-change-requires-migration",
                    f"/{path}/{document_id}",
                    "已有 Schema 不可由扩展包直接改写，请使用 Schema Migration",
                )
            elif existing is not None:
                issue(
                    "warning",
                    "existing-schema-noop",
                    f"/{path}/{document_id}",
                    "Schema 已存在且内容相同，发布时将保持现状",
                )

    for entity_type in pack.entity_types:
        if entity_type.space_id not in all_spaces:
            issue(
                "error",
                "unknown-space",
                f"/entityTypes/{entity_type.id}/spaceId",
                f"知识空间 {entity_type.space_id} 不存在",
            )
        for taxonomy_id in entity_type.allowed_taxonomy_node_ids:
            node = all_nodes.get(taxonomy_id)
            if node is None or node.space_id != entity_type.space_id:
                issue(
                    "error",
                    "invalid-allowed-taxonomy",
                    f"/entityTypes/{entity_type.id}/allowedTaxonomyNodeIds",
                    f"分类 {taxonomy_id} 不存在或不属于实体类型空间",
                )
        for attribute_id in entity_type.attribute_definition_ids:
            if attribute_id not in all_attributes:
                issue(
                    "error",
                    "unknown-attribute",
                    f"/entityTypes/{entity_type.id}/attributeDefinitionIds",
                    f"属性 {attribute_id} 不存在",
                )
        for relationship_id in entity_type.allowed_relationship_type_ids:
            if relationship_id not in all_relationships:
                issue(
                    "error",
                    "unknown-relationship-type",
                    f"/entityTypes/{entity_type.id}/allowedRelationshipTypeIds",
                    f"关系类型 {relationship_id} 不存在",
                )
        view = all_views.get(entity_type.default_view_definition_id)
        if view is None or view.entity_type_id != entity_type.id:
            issue(
                "error",
                "invalid-default-view",
                f"/entityTypes/{entity_type.id}/defaultViewDefinitionId",
                "默认视图不存在或属于其它实体类型",
            )

    for view in pack.views:
        if view.entity_type_id not in all_entity_types:
            issue(
                "error",
                "unknown-view-entity-type",
                f"/views/{view.id}/entityTypeId",
                f"实体类型 {view.entity_type_id} 不存在",
            )

    profile_targets: dict[str, str] = {}
    for profile in all_quality_profiles.values():
        previous_id = profile_targets.get(profile.entity_type_id)
        if previous_id is not None and previous_id != profile.id:
            issue(
                "error",
                "multiple-quality-profiles",
                f"/qualityProfiles/{profile.id}/entityTypeId",
                f"实体类型 {profile.entity_type_id} 只能有一个质量档案",
            )
        profile_targets[profile.entity_type_id] = profile.id
    for profile in pack.quality_profiles:
        entity_type = all_entity_types.get(profile.entity_type_id)
        if entity_type is None:
            issue(
                "error",
                "unknown-quality-profile-entity-type",
                f"/qualityProfiles/{profile.id}/entityTypeId",
                f"实体类型 {profile.entity_type_id} 不存在",
            )
            continue
        invalid_attributes = (
            set(profile.required_attribute_ids)
            - set(entity_type.attribute_definition_ids)
        )
        if invalid_attributes:
            issue(
                "error",
                "invalid-quality-required-attribute",
                f"/qualityProfiles/{profile.id}/requiredAttributeIds",
                f"属性不属于实体类型：{sorted(invalid_attributes)}",
            )

    for relationship in pack.relationship_types:
        for entity_type_id in (
            relationship.source_entity_type_ids
            + relationship.target_entity_type_ids
        ):
            if entity_type_id not in all_entity_types:
                issue(
                    "error",
                    "unknown-relationship-entity-type",
                    f"/relationshipTypes/{relationship.id}",
                    f"实体类型 {entity_type_id} 不存在",
                )

    counts = {
        "spaces": len(pack.spaces),
        "taxonomyNodes": len(pack.taxonomy_nodes),
        "entityTypes": len(pack.entity_types),
        "attributes": len(pack.attributes),
        "relationshipTypes": len(pack.relationship_types),
        "views": len(pack.views),
        "qualityProfiles": len(pack.quality_profiles),
    }
    return DomainPackValidation(
        valid=not any(item.severity == "error" for item in issues),
        issues=issues,
        counts=counts,
    )
