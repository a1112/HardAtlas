import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .knowledge import (
    AttributeDefinition,
    EntityType,
    KnowledgeEntity,
    KnowledgeModel,
    RelationshipType,
    ViewDefinition,
)

SchemaKind = Literal[
    "attribute-definition",
    "entity-type",
    "relationship-type",
    "view-definition",
]
SchemaChangeClassification = Literal[
    "additive",
    "compatible",
    "migratory",
    "breaking",
]
SchemaDocument = (
    AttributeDefinition | EntityType | RelationshipType | ViewDefinition
)


class SchemaFieldChange(KnowledgeModel):
    field: str
    before: object | None = None
    after: object | None = None
    severity: SchemaChangeClassification
    reason: str


class SchemaChangeAnalysis(KnowledgeModel):
    schema_kind: SchemaKind
    schema_id: str
    from_version: str
    to_version: str
    classification: SchemaChangeClassification
    changes: list[SchemaFieldChange]
    affected_entity_ids: list[str]
    affected_relationship_count: int = Field(ge=0)
    requires_migration: bool
    reversible: bool
    blockers: list[str] = Field(default_factory=list)


class SchemaMigrationOperation(KnowledgeModel):
    operation: Literal[
        "set-default",
        "remove-claim",
        "rename-claim",
        "map-enum",
        "cast-claim",
        "remove-relationship",
        "remap-taxonomy",
    ]
    attribute_id: str | None = None
    target_attribute_id: str | None = None
    relationship_type_id: str | None = None
    from_taxonomy_node_id: str | None = None
    to_taxonomy_node_id: str | None = None
    value: object | None = None
    mapping: dict[str, object] = Field(default_factory=dict)
    target_data_type: Literal["text", "integer", "decimal", "boolean"] | None = None
    citation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_parameters(self) -> "SchemaMigrationOperation":
        if self.operation in {
            "set-default",
            "remove-claim",
            "rename-claim",
            "map-enum",
            "cast-claim",
        } and not self.attribute_id:
            raise ValueError(f"{self.operation} requires attribute_id")
        if self.operation == "rename-claim" and not self.target_attribute_id:
            raise ValueError("rename-claim requires target_attribute_id")
        if self.operation == "map-enum" and not self.mapping:
            raise ValueError("map-enum requires a non-empty mapping")
        if self.operation == "cast-claim" and not self.target_data_type:
            raise ValueError("cast-claim requires target_data_type")
        if self.operation == "remove-relationship" and not self.relationship_type_id:
            raise ValueError("remove-relationship requires relationship_type_id")
        if self.operation == "remap-taxonomy" and (
            not self.from_taxonomy_node_id or not self.to_taxonomy_node_id
        ):
            raise ValueError("remap-taxonomy requires from and to taxonomy ids")
        if self.operation == "set-default" and not self.citation_ids:
            raise ValueError("set-default requires evidence citations")
        return self


class SchemaMigrationManifest(KnowledgeModel):
    id: str
    schema_kind: SchemaKind
    schema_id: str
    from_version: str
    to_version: str
    proposed_document: dict[str, object]
    analysis: SchemaChangeAnalysis
    operations: list[SchemaMigrationOperation]
    proposal_id: str
    status: Literal["planned", "applied", "rolled-back", "failed"] = "planned"
    frozen_revision_ids: dict[str, str]
    applied_revision_ids: dict[str, str] = Field(default_factory=dict)
    data_version: str
    created_by: str
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    applied_at: datetime | None = None
    rolled_back_at: datetime | None = None


_SEVERITY = {
    "additive": 0,
    "compatible": 1,
    "migratory": 2,
    "breaking": 3,
}


def _version(document: SchemaDocument) -> str:
    return document.schema_version


def _change(
    changes: list[SchemaFieldChange],
    *,
    field: str,
    before: object,
    after: object,
    severity: SchemaChangeClassification,
    reason: str,
) -> None:
    if before != after:
        changes.append(
            SchemaFieldChange(
                field=field,
                before=before,
                after=after,
                severity=severity,
                reason=reason,
            )
        )


def _attribute_changes(
    before: AttributeDefinition,
    after: AttributeDefinition,
) -> list[SchemaFieldChange]:
    changes: list[SchemaFieldChange] = []
    _change(
        changes,
        field="key",
        before=before.key,
        after=after.key,
        severity="breaking",
        reason="字段键改变会使现有 Claim 无法按原语义解析",
    )
    _change(
        changes,
        field="dataType",
        before=before.data_type,
        after=after.data_type,
        severity="breaking",
        reason="数据类型改变需要显式值转换器",
    )
    _change(
        changes,
        field="cardinality",
        before=before.cardinality,
        after=after.cardinality,
        severity=(
            "migratory"
            if before.cardinality == "many" and after.cardinality == "one"
            else "additive"
        ),
        reason="基数收紧需要处理已有多值 Claim",
    )
    _change(
        changes,
        field="required",
        before=before.required,
        after=after.required,
        severity="migratory" if after.required else "additive",
        reason="新增必填约束需要为缺失实体回填值",
    )
    removed_enum = sorted(
        set(before.enum_values or []) - set(after.enum_values or [])
    )
    if removed_enum:
        changes.append(
            SchemaFieldChange(
                field="enumValues",
                before=before.enum_values,
                after=after.enum_values,
                severity="migratory",
                reason=f"枚举值被移除：{', '.join(removed_enum)}",
            )
        )
    elif before.enum_values != after.enum_values:
        changes.append(
            SchemaFieldChange(
                field="enumValues",
                before=before.enum_values,
                after=after.enum_values,
                severity="additive",
                reason="只增加枚举值不要求重写现有数据",
            )
        )
    for field, old, new in [
        ("unitFamily", before.unit_family, after.unit_family),
        ("validation", before.validation, after.validation),
        ("name", before.name, after.name),
    ]:
        _change(
            changes,
            field=field,
            before=old,
            after=new,
            severity="compatible",
            reason="显示或校验元数据变化不改变存储结构",
        )
    return changes


def _entity_type_changes(
    before: EntityType,
    after: EntityType,
) -> list[SchemaFieldChange]:
    changes: list[SchemaFieldChange] = []
    removed_attributes = sorted(
        set(before.attribute_definition_ids)
        - set(after.attribute_definition_ids)
    )
    added_attributes = sorted(
        set(after.attribute_definition_ids)
        - set(before.attribute_definition_ids)
    )
    if removed_attributes:
        changes.append(
            SchemaFieldChange(
                field="attributeDefinitionIds",
                before=before.attribute_definition_ids,
                after=after.attribute_definition_ids,
                severity="migratory",
                reason=f"字段被移除：{', '.join(removed_attributes)}",
            )
        )
    elif added_attributes:
        changes.append(
            SchemaFieldChange(
                field="attributeDefinitionIds",
                before=before.attribute_definition_ids,
                after=after.attribute_definition_ids,
                severity="additive",
                reason=f"新增字段：{', '.join(added_attributes)}",
            )
        )
    for field, old, new, severity, reason in [
        (
            "key",
            before.key,
            after.key,
            "breaking",
            "实体类型键改变会破坏领域路由",
        ),
        (
            "spaceId",
            before.space_id,
            after.space_id,
            "breaking",
            "跨知识空间移动需要独立迁移",
        ),
        (
            "allowedTaxonomyNodeIds",
            before.allowed_taxonomy_node_ids,
            after.allowed_taxonomy_node_ids,
            "migratory",
            "分类边界改变可能使现有条目失效",
        ),
        (
            "allowedRelationshipTypeIds",
            before.allowed_relationship_type_ids,
            after.allowed_relationship_type_ids,
            "migratory",
            "关系白名单改变可能使当前关系边失效",
        ),
        (
            "defaultViewDefinitionId",
            before.default_view_definition_id,
            after.default_view_definition_id,
            "compatible",
            "视图切换不重写知识数据",
        ),
    ]:
        _change(
            changes,
            field=field,
            before=old,
            after=new,
            severity=severity,
            reason=reason,
        )
    return changes


def _relationship_type_changes(
    before: RelationshipType,
    after: RelationshipType,
) -> list[SchemaFieldChange]:
    changes: list[SchemaFieldChange] = []
    for field, old, new, severity, reason in [
        ("key", before.key, after.key, "breaking", "关系语义键不可原地改变"),
        (
            "directed",
            before.directed,
            after.directed,
            "breaking",
            "有向性改变会重解释全部关系边",
        ),
        (
            "sourceEntityTypeIds",
            before.source_entity_type_ids,
            after.source_entity_type_ids,
            "migratory",
            "源类型约束变化需要重验当前关系",
        ),
        (
            "targetEntityTypeIds",
            before.target_entity_type_ids,
            after.target_entity_type_ids,
            "migratory",
            "目标类型约束变化需要重验当前关系",
        ),
        (
            "sourceCardinality",
            before.source_cardinality,
            after.source_cardinality,
            "migratory",
            "源基数变化需要冲突分析",
        ),
        (
            "targetCardinality",
            before.target_cardinality,
            after.target_cardinality,
            "migratory",
            "目标基数变化需要冲突分析",
        ),
        (
            "qualifierSchema",
            before.qualifier_schema,
            after.qualifier_schema,
            "migratory",
            "限定字段变化需要重验关系载荷",
        ),
        (
            "evidenceRequired",
            before.evidence_required,
            after.evidence_required,
            "migratory" if after.evidence_required else "additive",
            "证据要求收紧需要检查无引用关系",
        ),
        ("name", before.name, after.name, "compatible", "正向显示名称变化"),
        (
            "inverseName",
            before.inverse_name,
            after.inverse_name,
            "compatible",
            "反向显示名称变化",
        ),
    ]:
        _change(
            changes,
            field=field,
            before=old,
            after=new,
            severity=severity,
            reason=reason,
        )
    return changes


def analyze_schema_change(
    *,
    schema_kind: SchemaKind,
    before: SchemaDocument,
    after: SchemaDocument,
    entities: list[KnowledgeEntity],
) -> SchemaChangeAnalysis:
    if before.id != after.id:
        raise ValueError("schema change cannot alter document id")
    if _version(before) == _version(after):
        raise ValueError("schema change requires a new version")
    expected_types: dict[SchemaKind, type[BaseModel]] = {
        "attribute-definition": AttributeDefinition,
        "entity-type": EntityType,
        "relationship-type": RelationshipType,
        "view-definition": ViewDefinition,
    }
    expected = expected_types[schema_kind]
    if not isinstance(before, expected) or not isinstance(after, expected):
        raise ValueError("schema kind does not match documents")

    if schema_kind == "attribute-definition":
        changes = _attribute_changes(before, after)
        affected = [
            entity.ref.id
            for entity in entities
            if any(
                claim.attribute_definition_id == before.id
                for claim in entity.claims
            )
        ]
        affected_relationship_count = 0
    elif schema_kind == "entity-type":
        changes = _entity_type_changes(before, after)
        affected = [
            entity.ref.id
            for entity in entities
            if entity.ref.type_id == before.id
        ]
        affected_relationship_count = sum(
            len(entity.relationships) for entity in entities if entity.ref.id in affected
        )
    elif schema_kind == "relationship-type":
        changes = _relationship_type_changes(before, after)
        affected = sorted(
            {
                entity.ref.id
                for entity in entities
                if any(
                    relationship.type_id == before.id
                    for relationship in entity.relationships
                )
            }
        )
        affected_relationship_count = sum(
            relationship.type_id == before.id
            for entity in entities
            for relationship in entity.relationships
        )
    else:
        changes = [
            SchemaFieldChange(
                field="blocks",
                before=before.blocks,
                after=after.blocks,
                severity="compatible",
                reason="安全页面块变化不重写实体数据",
            )
        ] if before.blocks != after.blocks else []
        affected = [
            entity.ref.id
            for entity in entities
            if entity.ref.type_id == before.entity_type_id
        ]
        affected_relationship_count = 0

    classification: SchemaChangeClassification = max(
        (change.severity for change in changes),
        key=_SEVERITY.__getitem__,
        default="compatible",
    )
    blockers = []
    if classification == "breaking":
        blockers.append("breaking 变更必须提供显式转换器和回滚夹具")
    return SchemaChangeAnalysis(
        schema_kind=schema_kind,
        schema_id=before.id,
        from_version=_version(before),
        to_version=_version(after),
        classification=classification,
        changes=changes,
        affected_entity_ids=sorted(set(affected)),
        affected_relationship_count=affected_relationship_count,
        requires_migration=classification in {"migratory", "breaking"},
        reversible=classification in {"additive", "compatible"},
        blockers=blockers,
    )


def _display_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _cast_value(value: object, target: str) -> object:
    try:
        if target == "text":
            return str(value)
        if target == "integer":
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if target == "decimal":
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if target == "boolean":
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().casefold()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"cannot cast migration value {value!r} to {target}"
        ) from error
    raise ValueError(f"cannot cast migration value {value!r} to {target}")


def apply_schema_migration_to_entity(
    entity: KnowledgeEntity,
    manifest: SchemaMigrationManifest,
    *,
    policy_version: str,
) -> KnowledgeEntity:
    expected_revision = manifest.frozen_revision_ids.get(entity.ref.id)
    if expected_revision != entity.revision.revision_id:
        raise ValueError("entity revision does not match frozen migration input")
    document = entity.model_dump(mode="json", by_alias=True)
    claims: list[dict[str, object]] = document["claims"]
    citations = {citation["id"] for citation in document["citations"]}

    for operation in manifest.operations:
        claim = next(
            (
                item
                for item in claims
                if item["attributeDefinitionId"] == operation.attribute_id
            ),
            None,
        )
        if operation.operation == "set-default":
            if claim is not None:
                continue
            missing = set(operation.citation_ids) - citations
            if missing:
                raise ValueError(
                    f"migration default references unknown citations: {sorted(missing)}"
                )
            claims.append(
                {
                    "id": f"claim-{manifest.id}-{entity.ref.id}-{operation.attribute_id}",
                    "attributeDefinitionId": operation.attribute_id,
                    "originalValue": operation.value,
                    "normalizedValue": operation.value,
                    "displayValue": [
                        {
                            "locale": "zh-CN",
                            "value": _display_value(operation.value),
                            "machineGenerated": True,
                        }
                    ],
                    "unit": None,
                    "validFrom": None,
                    "validTo": None,
                    "confidence": 1.0,
                    "citationIds": operation.citation_ids,
                    "revisionId": entity.revision.revision_id,
                }
            )
        elif operation.operation == "remove-claim":
            if claim is not None:
                claims.remove(claim)
        elif operation.operation == "rename-claim":
            if claim is not None:
                claim["attributeDefinitionId"] = operation.target_attribute_id
        elif operation.operation == "map-enum":
            if claim is None:
                continue
            key = str(claim["originalValue"])
            if key not in operation.mapping:
                raise ValueError(f"migration enum mapping has no value for {key}")
            value = operation.mapping[key]
            claim["originalValue"] = value
            claim["normalizedValue"] = value
            claim["displayValue"] = [
                {
                    "locale": "zh-CN",
                    "value": _display_value(value),
                    "machineGenerated": True,
                }
            ]
        elif operation.operation == "cast-claim":
            if claim is None:
                continue
            value = _cast_value(
                claim["originalValue"],
                operation.target_data_type,
            )
            claim["originalValue"] = value
            claim["normalizedValue"] = value
            claim["displayValue"] = [
                {
                    "locale": "zh-CN",
                    "value": _display_value(value),
                    "machineGenerated": True,
                }
            ]
        elif operation.operation == "remove-relationship":
            document["relationships"] = [
                relationship
                for relationship in document["relationships"]
                if relationship["typeId"] != operation.relationship_type_id
            ]
        elif operation.operation == "remap-taxonomy":
            document["taxonomyNodeIds"] = [
                operation.to_taxonomy_node_id
                if node_id == operation.from_taxonomy_node_id
                else node_id
                for node_id in document["taxonomyNodeIds"]
            ]

    digest = hashlib.sha256(
        f"{manifest.id}|{entity.revision.revision_id}".encode()
    ).hexdigest()[:12]
    safe_version = re.sub(
        r"[^a-zA-Z0-9]+",
        "-",
        manifest.data_version,
    ).strip("-")
    revision_id = f"rev-{entity.ref.slug}-{safe_version}-{digest}"
    document["revision"] = {
        "revisionId": revision_id,
        "dataVersion": manifest.data_version,
        "schemaVersion": manifest.to_version,
        "policyVersion": policy_version,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    for claim in document["claims"]:
        claim["revisionId"] = revision_id
    for relationship in document["relationships"]:
        relationship["revisionId"] = revision_id
    return KnowledgeEntity.model_validate(document)
