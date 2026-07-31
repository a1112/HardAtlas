from typing import Literal

from pydantic import Field

from .knowledge import EntityType, KnowledgeModel, Relationship, RelationshipType


class RelationshipIssue(KnowledgeModel):
    code: str
    path: str
    message: str


class RelationshipValidation(KnowledgeModel):
    valid: bool
    issues: list[RelationshipIssue] = Field(default_factory=list)


def validate_relationship(
    relationship: Relationship,
    relationship_type: RelationshipType,
    source_entity_type: EntityType,
    *,
    known_citation_ids: set[str],
    outgoing_count: int = 0,
    incoming_count: int = 0,
    target_entity_type_id: str | None = None,
) -> RelationshipValidation:
    issues: list[RelationshipIssue] = []

    def issue(code: str, path: str, message: str) -> None:
        issues.append(RelationshipIssue(code=code, path=path, message=message))

    if relationship.type_id != relationship_type.id:
        issue("relationship-type-mismatch", "/typeId", "关系实例与关系类型不一致")
    if relationship.type_id not in source_entity_type.allowed_relationship_type_ids:
        issue("relationship-not-allowed", "/typeId", "源实体类型不允许该关系")
    if (
        relationship_type.source_entity_type_ids
        and relationship.source.type_id
        not in relationship_type.source_entity_type_ids
    ):
        issue("source-type-not-allowed", "/source/typeId", "关系类型不允许该源实体类型")
    resolved_target_type = target_entity_type_id or relationship.target.type_id
    if (
        relationship_type.target_entity_type_ids
        and resolved_target_type not in relationship_type.target_entity_type_ids
    ):
        issue("target-type-not-allowed", "/target/typeId", "关系类型不允许该目标实体类型")
    if (
        target_entity_type_id is not None
        and relationship.target.type_id != target_entity_type_id
    ):
        issue("target-type-mismatch", "/target/typeId", "目标引用类型与目标实体不一致")
    if relationship_type.evidence_required and not relationship.citation_ids:
        issue("missing-evidence", "/citationIds", "关系必须包含来源证据")
    unknown_citations = sorted(
        set(relationship.citation_ids) - known_citation_ids
    )
    if unknown_citations:
        issue(
            "unknown-citation",
            "/citationIds",
            f"关系引用未知证据：{', '.join(unknown_citations)}",
        )
    if relationship_type.source_cardinality == "one" and outgoing_count >= 1:
        issue("source-cardinality", "/source", "该源实体只能建立一个此类型关系")
    if relationship_type.target_cardinality == "one" and incoming_count >= 1:
        issue("target-cardinality", "/target", "该目标实体只能接收一个此类型关系")
    unknown_qualifiers = sorted(
        set(relationship.qualifiers) - set(relationship_type.qualifier_schema)
    )
    if unknown_qualifiers:
        issue(
            "unknown-qualifier",
            "/qualifiers",
            f"关系包含未声明限定字段：{', '.join(unknown_qualifiers)}",
        )
    for key, value in relationship.qualifiers.items():
        schema = relationship_type.qualifier_schema.get(key)
        if not isinstance(schema, dict):
            continue
        expected_type = schema.get("type")
        type_matches = {
            "string": isinstance(value, str),
            "number": isinstance(value, (int, float))
            and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
        }.get(expected_type, True)
        if not type_matches:
            issue(
                "qualifier-type",
                f"/qualifiers/{key}",
                f"限定字段 {key} 不符合 {expected_type} 类型",
            )
        allowed_values = schema.get("enum")
        if isinstance(allowed_values, list) and value not in allowed_values:
            issue(
                "qualifier-enum",
                f"/qualifiers/{key}",
                f"限定字段 {key} 不在允许的枚举范围内",
            )

    return RelationshipValidation(valid=not issues, issues=issues)


RelationshipDirection = Literal["outgoing", "incoming"]
