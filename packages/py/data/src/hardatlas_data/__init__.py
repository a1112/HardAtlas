"""Persistence adapters. Domain code must not import web frameworks from here."""

from .answering import KnowledgeAnsweringService
from .models import Base
from .outbox import OutboxRecord
from .publication import SearchPublicationResult, SearchPublicationService
from .quality import (
    QualityMaintenanceService,
    QualityScanFailure,
    QualityScanResult,
)
from .release_verification import (
    ReleaseVerificationError,
    ReleaseVerificationResult,
    ReleaseVerifier,
)
from .releases import (
    ReleaseOrchestrationError,
    ReleaseOrchestrator,
    ReleasePublicationResult,
    ReleaseRollbackResult,
)
from .repository import KnowledgeRepository, ProposalConcurrencyError
from .search import (
    LexicalSearchBackend,
    OpenSearchBackend,
    SearchBackend,
    SearchBackendError,
    SearchHit,
    SearchResultPage,
)

__all__ = [
    "Base",
    "KnowledgeRepository",
    "KnowledgeAnsweringService",
    "LexicalSearchBackend",
    "OpenSearchBackend",
    "OutboxRecord",
    "ProposalConcurrencyError",
    "ReleaseOrchestrationError",
    "ReleaseOrchestrator",
    "ReleasePublicationResult",
    "ReleaseRollbackResult",
    "ReleaseVerificationError",
    "ReleaseVerificationResult",
    "ReleaseVerifier",
    "SearchBackend",
    "SearchBackendError",
    "SearchHit",
    "SearchResultPage",
    "SearchPublicationResult",
    "SearchPublicationService",
    "QualityMaintenanceService",
    "QualityScanFailure",
    "QualityScanResult",
]
