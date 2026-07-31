from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from hardatlas_domain import (
    GovernedProposal,
    KnowledgeEntity,
    PublicationError,
    ReleaseManifest,
    apply_release_to_entity,
    apply_release_to_new_entity,
)

from .repository import KnowledgeRepository
from .search import SearchBackend


class ReleaseOrchestrationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        phase: str,
        retryable: bool,
    ) -> None:
        self.phase = phase
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class ReleasePublicationResult:
    release: ReleaseManifest
    proposals: list[GovernedProposal]
    entities: list[KnowledgeEntity]


@dataclass(frozen=True)
class ReleaseRollbackResult:
    release: ReleaseManifest
    entities: list[KnowledgeEntity]


class ReleaseOrchestrator:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        search_backend: SearchBackend,
    ) -> None:
        self.repository = repository
        self.search_backend = search_backend

    def stage(
        self,
        *,
        release_id: str,
        proposal_ids: list[str],
        data_version: str,
        schema_versions: list[str],
        policy_version: str,
        previous_release_id: str,
        trigger: Literal["manual", "automatic"] = "manual",
        initiated_by: str | None = None,
    ) -> ReleaseManifest:
        unique_ids = list(dict.fromkeys(proposal_ids))
        if not unique_ids:
            raise ReleaseOrchestrationError(
                "release requires at least one proposal",
                phase="stage",
                retryable=False,
            )
        if len(unique_ids) != len(proposal_ids):
            raise ReleaseOrchestrationError(
                "release proposal ids must be unique",
                phase="stage",
                retryable=False,
            )
        proposals = self._load_proposals(unique_ids)
        if any(item.proposal.status != "accepted" for item in proposals):
            raise ReleaseOrchestrationError(
                "all release proposals must be accepted",
                phase="stage",
                retryable=False,
            )
        existing = self.repository.get_release(release_id)
        if existing is not None:
            if (
                existing.proposal_ids != unique_ids
                or existing.data_version != data_version
                or existing.schema_versions != sorted(set(schema_versions))
                or existing.policy_version != policy_version
                or existing.previous_release_id != previous_release_id
                or existing.trigger != trigger
                or existing.initiated_by != initiated_by
            ):
                raise ReleaseOrchestrationError(
                    "release id belongs to another immutable manifest",
                    phase="stage",
                    retryable=False,
                )
            return existing
        release = ReleaseManifest(
            id=release_id,
            proposal_ids=unique_ids,
            data_version=data_version,
            schema_versions=sorted(set(schema_versions)),
            policy_version=policy_version,
            previous_release_id=previous_release_id,
            trigger=trigger,
            initiated_by=initiated_by,
            status="staged",
        )
        self.repository.save_release(release)
        return release

    def publish(self, release_id: str) -> ReleasePublicationResult:
        release = self.repository.get_release(release_id)
        if release is None:
            raise ReleaseOrchestrationError(
                "release not found",
                phase="load",
                retryable=False,
            )
        if release.status == "published":
            proposals = self._load_proposals(release.proposal_ids)
            entities = [
                entity
                for entity_id in release.entity_revisions_after
                if (entity := self.repository.get_entity_by_id(entity_id))
                is not None
            ]
            return ReleasePublicationResult(
                release=release,
                proposals=proposals,
                entities=entities,
            )
        if release.status not in {"staged", "publishing"}:
            raise ReleaseOrchestrationError(
                f"release cannot publish from status {release.status}",
                phase="load",
                retryable=False,
            )

        if release.status == "staged":
            release = self._prepare(release)

        assert release.search_index is not None
        try:
            active_index = getattr(
                self.search_backend,
                "active_index",
                None,
            )
            if (
                self.search_backend.name == "memory-lexical"
                and active_index != release.search_index
            ):
                self.search_backend.stage(
                    entities=self.repository.list_entities(),
                    data_version=release.data_version,
                )
            self.search_backend.activate(release.search_index)
        except Exception as error:
            raise ReleaseOrchestrationError(
                f"search index activation failed: {error}",
                phase="search-activate",
                retryable=True,
            ) from error

        published = release.model_copy(
            deep=True,
            update={
                "status": "published",
                "published_at": datetime.now(UTC),
            },
        )
        proposals = self._load_proposals(published.proposal_ids)
        published_proposals: list[GovernedProposal] = []
        for governed in proposals:
            if governed.proposal.status not in {"accepted", "released"}:
                raise ReleaseOrchestrationError(
                    f"proposal changed before release completion: "
                    f"{governed.proposal.id}",
                    phase="database-complete",
                    retryable=False,
                )
            released = governed.model_copy(deep=True)
            released.proposal.status = "released"
            published_proposals.append(released)
        try:
            self.repository.complete_release_publication(
                published,
                published_proposals,
            )
        except Exception as error:
            raise ReleaseOrchestrationError(
                f"release completion failed after search activation: {error}",
                phase="database-complete",
                retryable=True,
            ) from error
        entities = [
            entity
            for entity_id in published.entity_revisions_after
            if (entity := self.repository.get_entity_by_id(entity_id))
            is not None
        ]
        return ReleasePublicationResult(
            release=published,
            proposals=published_proposals,
            entities=entities,
        )

    def rollback(self, release_id: str) -> ReleaseRollbackResult:
        release = self.repository.get_release(release_id)
        if release is None:
            raise ReleaseOrchestrationError(
                "release not found",
                phase="rollback-load",
                retryable=False,
            )
        if release.status == "rolled-back":
            return ReleaseRollbackResult(
                release=release,
                entities=self.repository.list_entities(),
            )
        if release.status not in {"published", "rolling-back"}:
            raise ReleaseOrchestrationError(
                f"release cannot roll back from status {release.status}",
                phase="rollback-load",
                retryable=False,
            )
        if release.status == "published":
            restored_by_id = {
                entity.ref.id: entity
                for entity in self.repository.entities_at_revisions(
                    release.entity_revisions_before
                )
            }
            rollback_entities = [
                restored_by_id.get(entity.ref.id, entity)
                for entity in self.repository.list_entities()
                if entity.ref.id not in release.created_entity_ids
            ]
            try:
                rollback_index = self.search_backend.stage(
                    entities=rollback_entities,
                    data_version=f"rollback-{release.id}",
                )
            except Exception as error:
                raise ReleaseOrchestrationError(
                    f"rollback search index staging failed: {error}",
                    phase="rollback-search-stage",
                    retryable=True,
                ) from error
            rolling_back = release.model_copy(
                deep=True,
                update={
                    "status": "rolling-back",
                    "rollback_search_index": rollback_index,
                },
            )
            try:
                self.repository.begin_release_rollback(rolling_back)
            except Exception as error:
                raise ReleaseOrchestrationError(
                    f"release rollback preparation failed: {error}",
                    phase="rollback-database-prepare",
                    retryable=False,
                ) from error
            release = rolling_back

        assert release.rollback_search_index is not None
        try:
            active_index = getattr(
                self.search_backend,
                "active_index",
                None,
            )
            if (
                self.search_backend.name == "memory-lexical"
                and release.rollback_search_index != active_index
            ):
                self.search_backend.stage(
                    entities=self.repository.list_entities(),
                    data_version=f"rollback-{release.id}",
                )
            self.search_backend.activate(release.rollback_search_index)
        except Exception as error:
            raise ReleaseOrchestrationError(
                f"rollback index activation failed: {error}",
                phase="rollback-search-activate",
                retryable=True,
            ) from error
        rolled_back = release.model_copy(
            deep=True,
            update={
                "status": "rolled-back",
                "rolled_back_at": datetime.now(UTC),
            },
        )
        try:
            self.repository.complete_release_rollback(rolled_back)
        except Exception as error:
            raise ReleaseOrchestrationError(
                f"rollback completion failed: {error}",
                phase="rollback-database-complete",
                retryable=True,
            ) from error
        return ReleaseRollbackResult(
            release=rolled_back,
            entities=self.repository.list_entities(),
        )

    def _prepare(self, release: ReleaseManifest) -> ReleaseManifest:
        proposals = self._load_proposals(release.proposal_ids)
        proposals_by_entity: dict[str, list[GovernedProposal]] = {}
        for governed in proposals:
            if governed.proposal.status != "accepted":
                raise ReleaseOrchestrationError(
                    "release proposal is not accepted",
                    phase="prepare",
                    retryable=False,
                )
            entity_id = governed.proposal.entity_id
            if entity_id is None:
                raise ReleaseOrchestrationError(
                    "release proposal is not bound to an entity",
                    phase="prepare",
                    retryable=False,
                )
            proposals_by_entity.setdefault(entity_id, []).append(governed)

        current_entities = {
            entity.ref.id: entity
            for entity in self.repository.list_entities()
        }
        revisions_before = self.repository.current_revision_ids(
            list(proposals_by_entity)
        )
        updated_entities: dict[str, KnowledgeEntity] = {}
        created_entity_ids: list[str] = []
        schema_version = (
            release.schema_versions[-1]
            if release.schema_versions
            else "schema-2.1.0"
        )
        try:
            for entity_id, entity_proposals in proposals_by_entity.items():
                entity = current_entities.get(entity_id)
                if entity is None:
                    updated_entities[entity_id] = (
                        apply_release_to_new_entity(
                            entity_proposals,
                            entity_id=entity_id,
                            data_version=release.data_version,
                            schema_version=schema_version,
                            policy_version=release.policy_version,
                        )
                    )
                    created_entity_ids.append(entity_id)
                else:
                    updated_entities[entity_id] = apply_release_to_entity(
                        entity,
                        entity_proposals,
                        data_version=release.data_version,
                        schema_version=schema_version,
                        policy_version=release.policy_version,
                    )
        except PublicationError as error:
            raise ReleaseOrchestrationError(
                str(error),
                phase="prepare",
                retryable=False,
            ) from error

        future_entities = [
            updated_entities.get(entity_id, entity)
            for entity_id, entity in current_entities.items()
        ]
        future_entities.extend(
            entity
            for entity_id, entity in updated_entities.items()
            if entity_id not in current_entities
        )
        try:
            search_index = self.search_backend.stage(
                entities=future_entities,
                data_version=release.data_version,
            )
        except Exception as error:
            raise ReleaseOrchestrationError(
                f"search index staging failed: {error}",
                phase="search-stage",
                retryable=True,
            ) from error

        publishing = release.model_copy(
            deep=True,
            update={
                "status": "publishing",
                "search_index": search_index,
                "search_alias": getattr(
                    self.search_backend,
                    "index_alias",
                    None,
                ),
                "entity_revisions_before": revisions_before,
                "entity_revisions_after": {
                    entity_id: entity.revision.revision_id
                    for entity_id, entity in updated_entities.items()
                },
                "created_entity_ids": sorted(created_entity_ids),
            },
        )
        try:
            self.repository.begin_release_publication(
                publishing,
                list(updated_entities.values()),
            )
        except Exception as error:
            raise ReleaseOrchestrationError(
                f"release database preparation failed: {error}",
                phase="database-prepare",
                retryable=True,
            ) from error
        return publishing

    def _load_proposals(
        self,
        proposal_ids: list[str],
    ) -> list[GovernedProposal]:
        proposals: list[GovernedProposal] = []
        for proposal_id in proposal_ids:
            governed = self.repository.get_governed_proposal(proposal_id)
            if governed is None:
                raise ReleaseOrchestrationError(
                    f"release proposal not found: {proposal_id}",
                    phase="load",
                    retryable=False,
                )
            proposals.append(governed)
        return proposals
