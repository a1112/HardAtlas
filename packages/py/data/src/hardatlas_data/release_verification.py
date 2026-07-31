from dataclasses import dataclass
from datetime import UTC, datetime

from hardatlas_domain import (
    KnowledgeEntity,
    ReleaseManifest,
    ReleaseVerification,
    ReleaseVerificationCheck,
)

from .repository import KnowledgeRepository
from .search import SearchBackend


class ReleaseVerificationError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class ReleaseVerificationResult:
    release: ReleaseManifest
    should_rollback: bool


class ReleaseVerifier:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        search_backend: SearchBackend,
    ) -> None:
        self.repository = repository
        self.search_backend = search_backend

    def verify(
        self,
        release_id: str,
        *,
        retry_failed: bool = False,
    ) -> ReleaseVerificationResult:
        release = self.repository.get_release(release_id)
        if release is None:
            raise ReleaseVerificationError(
                "release not found",
                retryable=False,
            )
        if release.status == "rolled-back":
            return ReleaseVerificationResult(
                release=release,
                should_rollback=False,
            )
        if release.status != "published":
            raise ReleaseVerificationError(
                f"release cannot be verified from status {release.status}",
                retryable=release.status == "publishing",
            )
        if release.verification.status in {"passed", "superseded"}:
            return ReleaseVerificationResult(
                release=release,
                should_rollback=False,
            )
        if (
            release.verification.status == "failed"
            and not retry_failed
        ):
            return ReleaseVerificationResult(
                release=release,
                should_rollback=release.trigger == "automatic",
            )

        now = datetime.now(UTC)
        running = ReleaseVerification(
            status="running",
            attempt=release.verification.attempt + 1,
            started_at=now,
        )
        try:
            release = self.repository.update_release_verification(
                release.id,
                running,
                expected_statuses={
                    "pending",
                    "running",
                    "failed",
                },
            )
        except ValueError as error:
            latest = self.repository.get_release(release.id)
            if latest is not None and latest.verification.status in {
                "passed",
                "failed",
                "superseded",
            }:
                return ReleaseVerificationResult(
                    release=latest,
                    should_rollback=(
                        latest.verification.status == "failed"
                        and latest.trigger == "automatic"
                    ),
                )
            raise ReleaseVerificationError(
                str(error),
                retryable=True,
            ) from error

        checks, outcome = self._run_checks(release)
        completed = ReleaseVerification(
            status=outcome,
            attempt=running.attempt,
            checks=checks,
            started_at=running.started_at,
            completed_at=datetime.now(UTC),
        )
        try:
            release = self.repository.update_release_verification(
                release.id,
                completed,
                expected_statuses={"running"},
            )
        except ValueError as error:
            raise ReleaseVerificationError(
                str(error),
                retryable=True,
            ) from error
        return ReleaseVerificationResult(
            release=release,
            should_rollback=(
                outcome == "failed"
                and release.trigger == "automatic"
            ),
        )

    def mark_automatic_rollback(
        self,
        release_id: str,
        *,
        reason: str,
    ) -> ReleaseManifest:
        release = self.repository.get_release(release_id)
        if release is None:
            raise ReleaseVerificationError(
                "release not found",
                retryable=False,
            )
        if release.verification.status != "failed":
            raise ReleaseVerificationError(
                "automatic rollback requires failed verification",
                retryable=False,
            )
        verification = release.verification.model_copy(
            deep=True,
            update={
                "automatic_rollback": True,
                "rollback_reason": reason,
            },
        )
        try:
            return self.repository.update_release_verification(
                release.id,
                verification,
                expected_statuses={"failed"},
            )
        except ValueError as error:
            latest = self.repository.get_release(release.id)
            if (
                latest is not None
                and latest.verification.automatic_rollback
            ):
                return latest
            raise ReleaseVerificationError(
                str(error),
                retryable=True,
            ) from error

    def _run_checks(
        self,
        release: ReleaseManifest,
    ) -> tuple[
        list[ReleaseVerificationCheck],
        str,
    ]:
        checks = [
            ReleaseVerificationCheck(
                key="release-state",
                status="passed",
                detail=(
                    f"release {release.id} is published with "
                    f"{len(release.entity_revisions_after)} frozen revisions"
                ),
            )
        ]
        frozen_entities = self._frozen_entities(
            release,
            checks,
        )
        self._check_citations(frozen_entities, checks)
        self._check_relationships(frozen_entities, checks)

        if not self.search_backend.ready():
            raise ReleaseVerificationError(
                "search backend is not ready for release verification",
                retryable=True,
            )
        checks.append(
            ReleaseVerificationCheck(
                key="search-readiness",
                status="passed",
                detail=f"{self.search_backend.name} is ready",
            )
        )
        try:
            current_index = self.search_backend.current_index()
        except Exception as error:
            raise ReleaseVerificationError(
                f"cannot resolve active search index: {error}",
                retryable=True,
            ) from error

        newer_release = self._newer_active_release(
            release,
            current_index,
        )
        if current_index != release.search_index:
            if newer_release is not None:
                checks.extend(
                    [
                        ReleaseVerificationCheck(
                            key="search-index",
                            status="skipped",
                            detail=(
                                f"release was superseded by "
                                f"{newer_release.id} before alias verification"
                            ),
                        ),
                        ReleaseVerificationCheck(
                            key="search-discoverability",
                            status="skipped",
                            detail=(
                                "alias-level discovery belongs to the newer "
                                "published release"
                            ),
                        ),
                    ]
                )
            else:
                checks.extend(
                    [
                        ReleaseVerificationCheck(
                            key="search-index",
                            status="failed",
                            detail=(
                                f"active index {current_index!r} does not match "
                                f"release index {release.search_index!r}"
                            ),
                        ),
                        ReleaseVerificationCheck(
                            key="search-discoverability",
                            status="skipped",
                            detail=(
                                "discoverability was not tested against the "
                                "wrong active index"
                            ),
                        ),
                    ]
                )
        else:
            checks.append(
                ReleaseVerificationCheck(
                    key="search-index",
                    status="passed",
                    detail=f"read alias resolves to {current_index}",
                )
            )
            self._check_discoverability(frozen_entities, checks)

        if any(check.status == "failed" for check in checks):
            return checks, "failed"
        if newer_release is not None:
            return checks, "superseded"
        return checks, "passed"

    def _frozen_entities(
        self,
        release: ReleaseManifest,
        checks: list[ReleaseVerificationCheck],
    ) -> list[KnowledgeEntity]:
        try:
            frozen = self.repository.entities_at_revisions(
                release.entity_revisions_after
            )
        except ValueError as error:
            checks.append(
                ReleaseVerificationCheck(
                    key="entity-revisions",
                    status="failed",
                    detail=str(error),
                    affected_entity_ids=sorted(
                        release.entity_revisions_after
                    ),
                )
            )
            return []
        actual = {
            entity.ref.id: entity.revision.revision_id
            for entity in frozen
        }
        mismatched = sorted(
            entity_id
            for entity_id, revision_id
            in release.entity_revisions_after.items()
            if actual.get(entity_id) != revision_id
        )
        unpublished = sorted(
            entity.ref.id
            for entity in frozen
            if entity.publication_status != "published"
        )
        affected = sorted(set(mismatched + unpublished))
        checks.append(
            ReleaseVerificationCheck(
                key="entity-revisions",
                status="failed" if affected else "passed",
                detail=(
                    "frozen revisions are missing, mismatched, or unpublished"
                    if affected
                    else (
                        f"{len(frozen)} immutable entity revisions match "
                        "the release manifest"
                    )
                ),
                affected_entity_ids=affected,
            )
        )
        return frozen

    @staticmethod
    def _check_citations(
        entities: list[KnowledgeEntity],
        checks: list[ReleaseVerificationCheck],
    ) -> None:
        affected: list[str] = []
        for entity in entities:
            known = {citation.id for citation in entity.citations}
            referenced = {
                citation_id
                for claim in entity.claims
                for citation_id in claim.citation_ids
            }
            referenced.update(
                citation_id
                for section in entity.sections
                for citation_id in section.citation_ids
            )
            referenced.update(
                citation_id
                for relationship in entity.relationships
                for citation_id in relationship.citation_ids
            )
            if referenced - known:
                affected.append(entity.ref.id)
        checks.append(
            ReleaseVerificationCheck(
                key="citation-integrity",
                status="failed" if affected else "passed",
                detail=(
                    "published values reference unknown citations"
                    if affected
                    else "all published citation references resolve"
                ),
                affected_entity_ids=sorted(affected),
            )
        )

    def _check_relationships(
        self,
        entities: list[KnowledgeEntity],
        checks: list[ReleaseVerificationCheck],
    ) -> None:
        known_entity_ids = {
            entity.ref.id
            for entity in self.repository.list_entities()
        }
        known_entity_ids.update(entity.ref.id for entity in entities)
        affected: list[str] = []
        for entity in entities:
            if any(
                relationship.source.id != entity.ref.id
                or relationship.target.id not in known_entity_ids
                for relationship in entity.relationships
            ):
                affected.append(entity.ref.id)
        checks.append(
            ReleaseVerificationCheck(
                key="relationship-integrity",
                status="failed" if affected else "passed",
                detail=(
                    "relationship source or target references are invalid"
                    if affected
                    else "all released relationships resolve to known entities"
                ),
                affected_entity_ids=sorted(affected),
            )
        )

    def _check_discoverability(
        self,
        entities: list[KnowledgeEntity],
        checks: list[ReleaseVerificationCheck],
    ) -> None:
        missing: list[str] = []
        try:
            for entity in entities:
                hits = self.search_backend.search(
                    entity.ref.canonical_name,
                    limit=100,
                )
                if not any(
                    hit.slug == entity.ref.slug
                    for hit in hits
                ):
                    missing.append(entity.ref.id)
        except Exception as error:
            raise ReleaseVerificationError(
                f"search discoverability check failed: {error}",
                retryable=True,
            ) from error
        checks.append(
            ReleaseVerificationCheck(
                key="search-discoverability",
                status="failed" if missing else "passed",
                detail=(
                    "released entities are missing from exact-name search"
                    if missing
                    else (
                        f"{len(entities)} released entities are discoverable "
                        "through the read alias"
                    )
                ),
                affected_entity_ids=sorted(missing),
            )
        )

    def _newer_active_release(
        self,
        release: ReleaseManifest,
        current_index: str | None,
    ) -> ReleaseManifest | None:
        if current_index is None:
            return None
        newer = [
            candidate
            for candidate in self.repository.list_releases()
            if candidate.id != release.id
            and candidate.status == "published"
            and candidate.search_index == current_index
            and candidate.published_at is not None
            and (
                release.published_at is None
                or candidate.published_at > release.published_at
            )
        ]
        return max(
            newer,
            key=lambda candidate: candidate.published_at
            or candidate.created_at,
            default=None,
        )
