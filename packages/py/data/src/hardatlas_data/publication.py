from dataclasses import dataclass

from hardatlas_domain import KnowledgeEntity

from .repository import KnowledgeRepository
from .search import SearchBackend


@dataclass(frozen=True)
class SearchPublicationResult:
    index_name: str
    data_version: str
    entity_count: int
    outbox_event_count: int


class SearchPublicationService:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        search_backend: SearchBackend,
    ) -> None:
        self.repository = repository
        self.search_backend = search_backend

    def publish_snapshot(
        self,
        *,
        data_version: str,
        entities: list[KnowledgeEntity] | None = None,
    ) -> SearchPublicationResult:
        snapshot = entities if entities is not None else self.repository.list_entities()
        event_ids = self.repository.pending_outbox_ids(topic="knowledge.entity.revised")
        try:
            index_name = self.search_backend.publish(
                entities=snapshot,
                data_version=data_version,
            )
        except Exception as error:
            self.repository.mark_outbox_failed(event_ids, str(error))
            raise
        self.repository.mark_outbox_published(event_ids)
        return SearchPublicationResult(
            index_name=index_name,
            data_version=data_version,
            entity_count=len(snapshot),
            outbox_event_count=len(event_ids),
        )
