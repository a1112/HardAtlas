import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from .knowledge import (
    AttributeDefinition,
    EntityType,
    KnowledgeSpace,
    RelationshipType,
    TaxonomyNode,
    ViewDefinition,
)
from .quality import QualityProfile


class DomainPackModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class DomainPackManifest(DomainPackModel):
    id: str
    name: str
    version: str
    kernel_version: str
    locales: list[str]
    space_files: list[str] = Field(default_factory=list)
    taxonomy_node_files: list[str] = Field(default_factory=list)
    entity_type_files: list[str]
    attribute_files: list[str]
    relationship_type_files: list[str] = Field(default_factory=list)
    view_files: list[str]
    quality_profile_files: list[str] = Field(default_factory=list)


class LoadedDomainPack(DomainPackModel):
    root: str
    manifest: DomainPackManifest
    spaces: list[KnowledgeSpace] = Field(default_factory=list)
    taxonomy_nodes: list[TaxonomyNode] = Field(default_factory=list)
    entity_types: list[EntityType]
    attributes: list[AttributeDefinition]
    relationship_types: list[RelationshipType]
    views: list[ViewDefinition]
    quality_profiles: list[QualityProfile] = Field(default_factory=list)


class DomainRegistry:
    def __init__(self, packs: list[LoadedDomainPack] | None = None) -> None:
        self.packs: dict[str, LoadedDomainPack] = {}
        self.spaces: dict[str, KnowledgeSpace] = {}
        self.taxonomy_nodes: dict[str, TaxonomyNode] = {}
        self.entity_types: dict[str, EntityType] = {}
        self.attributes: dict[str, AttributeDefinition] = {}
        self.relationship_types: dict[str, RelationshipType] = {}
        self.views: dict[str, ViewDefinition] = {}
        self.quality_profiles: dict[str, QualityProfile] = {}
        for pack in packs or []:
            self.register(pack)

    @staticmethod
    def _merge_document(
        documents: dict[str, Any],
        document: Any,
        *,
        kind: str,
    ) -> None:
        document_id = str(document.id)
        existing = documents.get(document_id)
        if existing is not None and existing != document:
            raise ValueError(
                f"conflicting {kind} definition across domain packs: "
                f"{document_id}"
            )
        documents[document_id] = document

    def register(self, pack: LoadedDomainPack) -> None:
        existing = self.packs.get(pack.manifest.id)
        if existing is not None and existing != pack:
            raise ValueError(
                f"domain pack id is already registered: {pack.manifest.id}"
            )
        for space in pack.spaces:
            self._merge_document(self.spaces, space, kind="knowledge space")
        for node in pack.taxonomy_nodes:
            self._merge_document(
                self.taxonomy_nodes,
                node,
                kind="taxonomy node",
            )
        for entity_type in pack.entity_types:
            self._merge_document(
                self.entity_types,
                entity_type,
                kind="entity type",
            )
        for attribute in pack.attributes:
            self._merge_document(
                self.attributes,
                attribute,
                kind="attribute",
            )
        for relationship_type in pack.relationship_types:
            self._merge_document(
                self.relationship_types,
                relationship_type,
                kind="relationship type",
            )
        for view in pack.views:
            self._merge_document(self.views, view, kind="view")
        for profile in pack.quality_profiles:
            self._merge_document(
                self.quality_profiles,
                profile,
                kind="quality profile",
            )
        self.packs[pack.manifest.id] = pack

    def validate(self) -> None:
        for node in self.taxonomy_nodes.values():
            if node.space_id not in self.spaces:
                raise ValueError(
                    f"taxonomy node {node.id} references missing space "
                    f"{node.space_id}"
                )
            for parent_id in node.parent_ids:
                parent = self.taxonomy_nodes.get(parent_id)
                if parent is None or parent.space_id != node.space_id:
                    raise ValueError(
                        f"taxonomy node {node.id} has invalid parent {parent_id}"
                    )
        for space in self.spaces.values():
            for root_id in space.root_taxonomy_node_ids:
                root = self.taxonomy_nodes.get(root_id)
                if root is None or root.space_id != space.id or root.parent_ids:
                    raise ValueError(
                        f"space {space.id} has invalid root taxonomy {root_id}"
                    )
        for entity_type in self.entity_types.values():
            if self.spaces and entity_type.space_id not in self.spaces:
                raise ValueError(
                    f"{entity_type.id} references missing space "
                    f"{entity_type.space_id}"
                )
            if self.taxonomy_nodes:
                invalid_taxonomy = {
                    node_id
                    for node_id in entity_type.allowed_taxonomy_node_ids
                    if node_id not in self.taxonomy_nodes
                    or self.taxonomy_nodes[node_id].space_id
                    != entity_type.space_id
                }
                if invalid_taxonomy:
                    raise ValueError(
                        f"{entity_type.id} references invalid taxonomy nodes: "
                        f"{sorted(invalid_taxonomy)}"
                    )
            missing_attributes = (
                set(entity_type.attribute_definition_ids)
                - self.attributes.keys()
            )
            missing_relationships = (
                set(entity_type.allowed_relationship_type_ids)
                - self.relationship_types.keys()
            )
            if missing_attributes or missing_relationships:
                raise ValueError(
                    f"domain registry has unresolved references for "
                    f"{entity_type.id}: attributes={sorted(missing_attributes)}, "
                    f"relationships={sorted(missing_relationships)}"
                )
            if entity_type.default_view_definition_id not in self.views:
                raise ValueError(
                    f"domain registry has no default view for {entity_type.id}"
                )
        profile_entity_types: set[str] = set()
        for profile in self.quality_profiles.values():
            entity_type = self.entity_types.get(profile.entity_type_id)
            if entity_type is None:
                raise ValueError(
                    f"quality profile {profile.id} references missing entity type "
                    f"{profile.entity_type_id}"
                )
            missing_attributes = (
                set(profile.required_attribute_ids)
                - set(entity_type.attribute_definition_ids)
            )
            if missing_attributes:
                raise ValueError(
                    f"quality profile {profile.id} references attributes outside "
                    f"{profile.entity_type_id}: {sorted(missing_attributes)}"
                )
            if profile.entity_type_id in profile_entity_types:
                raise ValueError(
                    f"multiple quality profiles target {profile.entity_type_id}"
                )
            profile_entity_types.add(profile.entity_type_id)


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_domain_pack(root: Path) -> LoadedDomainPack:
    manifest = DomainPackManifest.model_validate(_read_json(root / "manifest.json"))
    spaces = [
        KnowledgeSpace.model_validate(_read_json(root / relative))
        for relative in manifest.space_files
    ]
    taxonomy_nodes = [
        TaxonomyNode.model_validate(_read_json(root / relative))
        for relative in manifest.taxonomy_node_files
    ]
    entity_types = [
        EntityType.model_validate(_read_json(root / relative))
        for relative in manifest.entity_type_files
    ]
    attributes = [
        AttributeDefinition.model_validate(_read_json(root / relative))
        for relative in manifest.attribute_files
    ]
    relationship_types = [
        RelationshipType.model_validate(_read_json(root / relative))
        for relative in manifest.relationship_type_files
    ]
    views = [
        ViewDefinition.model_validate(_read_json(root / relative))
        for relative in manifest.view_files
    ]
    quality_profiles = [
        QualityProfile.model_validate(_read_json(root / relative))
        for relative in manifest.quality_profile_files
    ]

    attribute_ids = {attribute.id for attribute in attributes}
    relationship_type_ids = {
        relationship_type.id for relationship_type in relationship_types
    }
    view_ids = {view.id for view in views}
    for entity_type in entity_types:
        missing_attributes = set(entity_type.attribute_definition_ids) - attribute_ids
        if missing_attributes:
            raise ValueError(
                f"{entity_type.id} references missing attributes: {sorted(missing_attributes)}"
            )
        missing_relationship_types = (
            set(entity_type.allowed_relationship_type_ids)
            - relationship_type_ids
        )
        if missing_relationship_types:
            raise ValueError(
                f"{entity_type.id} references missing relationship types: "
                f"{sorted(missing_relationship_types)}"
            )
        if entity_type.default_view_definition_id not in view_ids:
            raise ValueError(
                f"{entity_type.id} references missing view: "
                f"{entity_type.default_view_definition_id}"
            )

    entity_type_ids = {entity_type.id for entity_type in entity_types}
    for view in views:
        if view.entity_type_id not in entity_type_ids:
            raise ValueError(f"{view.id} references missing entity type: {view.entity_type_id}")
    for profile in quality_profiles:
        entity_type = next(
            (
                item
                for item in entity_types
                if item.id == profile.entity_type_id
            ),
            None,
        )
        if entity_type is None:
            raise ValueError(
                f"{profile.id} references missing entity type: "
                f"{profile.entity_type_id}"
            )
        missing_attributes = (
            set(profile.required_attribute_ids)
            - set(entity_type.attribute_definition_ids)
        )
        if missing_attributes:
            raise ValueError(
                f"{profile.id} references attributes outside its entity type: "
                f"{sorted(missing_attributes)}"
            )

    return LoadedDomainPack(
        root=str(root),
        manifest=manifest,
        spaces=spaces,
        taxonomy_nodes=taxonomy_nodes,
        entity_types=entity_types,
        attributes=attributes,
        relationship_types=relationship_types,
        views=views,
        quality_profiles=quality_profiles,
    )


def load_domain_registry(roots: list[Path]) -> DomainRegistry:
    registry = DomainRegistry([load_domain_pack(root) for root in roots])
    registry.validate()
    return registry
