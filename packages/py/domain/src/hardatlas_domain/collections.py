from datetime import UTC, datetime

from pydantic import Field

from .knowledge import EntityRef, KnowledgeModel


class SavedCollection(KnowledgeModel):
    id: str
    workspace_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SavedCollectionItem(KnowledgeModel):
    collection_id: str
    workspace_id: str
    entity_id: str
    entity_ref: EntityRef
    entity_revision_id: str
    data_version: str
    note: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    saved_by: str
    saved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
