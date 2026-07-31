from hardatlas_domain import (
    EntityRef,
    EntityType,
    LocalizedText,
    Relationship,
    RelationshipType,
    validate_relationship,
)


def relationship_type() -> RelationshipType:
    return RelationshipType(
        id="rel-variant-of",
        key="variant_of",
        name=[LocalizedText(locale="zh-CN", value="变体属于")],
        inverse_name=[LocalizedText(locale="zh-CN", value="具有变体")],
        directed=True,
        source_entity_type_ids=["type-component"],
        target_entity_type_ids=["type-component"],
        source_cardinality="one",
        qualifier_schema={"manufacturer": {"type": "string"}},
        schema_version="relations-1",
    )


def entity_type() -> EntityType:
    return EntityType(
        id="type-component",
        space_id="space-engineering",
        key="component",
        name=[LocalizedText(locale="zh-CN", value="元件")],
        description=[],
        attribute_definition_ids=[],
        allowed_relationship_type_ids=["rel-variant-of"],
        default_view_definition_id="view-component",
        schema_version="schema-1",
    )


def relation() -> Relationship:
    return Relationship(
        id="relation-1",
        type_id="rel-variant-of",
        source=EntityRef(
            id="entity-ne555p",
            slug="ne555p",
            type_id="type-component",
            canonical_name="NE555P",
        ),
        target=EntityRef(
            id="entity-ne555",
            slug="ne555",
            type_id="type-component",
            canonical_name="NE555",
        ),
        qualifiers={"manufacturer": "Texas Instruments"},
        confidence=0.99,
        citation_ids=["citation-datasheet"],
        revision_id="revision-1",
    )


def test_relationship_validation_enforces_schema_and_evidence() -> None:
    valid = validate_relationship(
        relation(),
        relationship_type(),
        entity_type(),
        known_citation_ids={"citation-datasheet"},
        target_entity_type_id="type-component",
    )
    assert valid.valid

    invalid = validate_relationship(
        relation().model_copy(
            update={
                "citation_ids": [],
                "qualifiers": {"unsupported": True},
            }
        ),
        relationship_type(),
        entity_type(),
        known_citation_ids=set(),
        outgoing_count=1,
        target_entity_type_id="type-component",
    )
    assert invalid.valid is False
    assert {issue.code for issue in invalid.issues} == {
        "missing-evidence",
        "source-cardinality",
        "unknown-qualifier",
    }
