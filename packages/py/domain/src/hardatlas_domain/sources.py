import ipaddress
import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from croniter import croniter
from pydantic import Field, field_validator, model_validator

from .knowledge import Citation, KnowledgeModel, LocalizedText


class ExtractionFieldMapping(KnowledgeModel):
    source_path: str
    target_path: str
    attribute_definition_id: str | None = None
    locale: str | None = None
    confidence: float = Field(default=0.8, ge=0, le=1)
    required: bool = False

    @field_validator("source_path", "target_path")
    @classmethod
    def require_safe_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 240:
            raise ValueError("extraction paths must contain 1-240 characters")
        if any(token in normalized for token in ("..", "(", ")", "{", "}")):
            raise ValueError("extraction paths use a restricted, non-executable syntax")
        return normalized


class ExtractionParserDefinition(KnowledgeModel):
    id: str
    version: str
    format: Literal["json", "jsonl", "csv", "html"]
    media_types: list[str]
    entity_type_id: str
    locale: str = "und"
    records_path: str | None = None
    record_selector: str | None = None
    external_id_path: str | None = None
    entity_id_path: str | None = None
    label_path: str
    field_mappings: list[ExtractionFieldMapping]
    max_records: int = Field(default=1000, ge=1, le=100_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("media_types")
    @classmethod
    def normalize_media_types(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.casefold().strip() for item in value if item.strip()})
        if not normalized:
            raise ValueError("parser requires at least one media type")
        return normalized

    @model_validator(mode="after")
    def validate_format_options(self) -> "ExtractionParserDefinition":
        if self.format == "html" and not self.record_selector:
            raise ValueError("HTML parsers require record_selector")
        if self.format != "html" and self.record_selector is not None:
            raise ValueError("record_selector is only valid for HTML parsers")
        if self.format == "jsonl" and self.records_path is not None:
            raise ValueError("JSONL parsers operate on one record per line")
        return self


class ExtractionLocator(KnowledgeModel):
    kind: Literal["json-pointer", "jsonl-line", "csv-row", "html-selector"]
    value: str


class ExtractionFieldCandidate(KnowledgeModel):
    id: str
    target_path: str
    attribute_definition_id: str | None = None
    original_value: Any
    proposed_value: Any
    locale: str | None = None
    confidence: float = Field(ge=0, le=1)
    locator: ExtractionLocator
    quote_hash: str
    citation_id: str

    @field_validator("quote_hash")
    @classmethod
    def validate_quote_hash(cls, value: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("quote_hash must be a lowercase SHA-256 digest")
        return value


class EntityResolutionMatch(KnowledgeModel):
    entity_id: str
    slug: str
    canonical_name: str
    match_basis: Literal["explicit-id", "exact-name", "exact-alias"]
    score: float = Field(ge=0, le=1)


class ExtractionCandidate(KnowledgeModel):
    id: str
    batch_id: str
    snapshot_id: str
    source_id: str
    source_version: str
    parser_id: str
    parser_version: str
    entity_type_id: str
    entity_id: str | None = None
    external_id: str | None = None
    labels: list[LocalizedText] = Field(min_length=1)
    fields: list[ExtractionFieldCandidate]
    citation: Citation
    record_locator: ExtractionLocator
    status: Literal["parsed", "scheduled", "resolved", "rejected"] = "parsed"
    schedule_ids: list[str] = Field(default_factory=list)
    resolution_matches: list[EntityResolutionMatch] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExtractionBatch(KnowledgeModel):
    id: str
    snapshot_id: str
    source_id: str
    source_version: str
    content_sha256: str
    parser_id: str
    parser_version: str
    status: Literal["completed", "failed"]
    candidate_ids: list[str] = Field(default_factory=list)
    candidate_count: int = Field(ge=0)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("content_sha256")
    @classmethod
    def validate_content_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        return value


class SourceDefinition(KnowledgeModel):
    id: str
    version: str
    name: str
    kind: Literal["website", "api", "feed", "file", "dataset"]
    base_url: str
    allowed_hosts: list[str]
    trust_tier: Literal["primary", "authoritative", "secondary", "community"]
    license_id: str
    license_status: Literal["allowed", "review-required", "blocked"]
    robots_policy: Literal["respect", "explicit-api", "not-applicable"]
    robots_status: Literal["allowed", "review-required", "blocked"] = "review-required"
    model_processing_policy: Literal[
        "forbidden",
        "approved-gateway",
    ] = "forbidden"
    allowed_media_types: list[str]
    parser_id: str | None = None
    parser_version: str | None = None
    max_bytes: int = Field(default=5_000_000, gt=0, le=100_000_000)
    locales: list[str] = Field(default_factory=list)
    entity_type_ids: list[str] = Field(default_factory=list)
    taxonomy_node_ids: list[str] = Field(default_factory=list)
    schedule: str | None = None
    status: Literal["active", "paused", "blocked"] = "paused"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("allowed_hosts")
    @classmethod
    def require_allowed_hosts(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.casefold().strip().rstrip(".") for item in value if item.strip()})
        if not normalized:
            raise ValueError("source requires at least one allowed host")
        return normalized

    @field_validator("locales", "entity_type_ids", "taxonomy_node_ids")
    @classmethod
    def normalize_scope_values(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if len(normalized.split()) != 5 or not croniter.is_valid(normalized):
            raise ValueError("source schedule must be a valid five-field cron expression")
        return normalized

    @model_validator(mode="after")
    def validate_acquisition_boundary(self) -> "SourceDefinition":
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source base_url must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("source base_url must not include credentials")
        if parsed.query:
            raise ValueError("source base_url must not include query parameters")
        if parsed.fragment:
            raise ValueError("source base_url must not include fragment")
        hostname = parsed.hostname.casefold().rstrip(".")
        if not any(
            hostname == candidate or hostname.endswith(f".{candidate}")
            for candidate in self.allowed_hosts
        ):
            raise ValueError("source base_url host must be allow-listed")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("source base_url host is blocked")
        if not self.allowed_media_types:
            raise ValueError("source requires at least one allowed media type")
        if bool(self.parser_id) != bool(self.parser_version):
            raise ValueError("parser_id and parser_version must be configured together")
        return self


class SourceSnapshot(KnowledgeModel):
    id: str
    source_id: str
    source_version: str
    url: str
    content_sha256: str
    storage_key: str
    media_type: str
    byte_size: int = Field(ge=0)
    http_status: int = Field(ge=100, le=599)
    etag: str | None = None
    last_modified: str | None = None
    license_id: str
    capture_status: Literal["captured", "unchanged", "rejected"]
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("content_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        return value


class SourceAcquisitionJob(KnowledgeModel):
    id: str
    source_id: str
    source_version: str
    url: str | None = None
    status: Literal[
        "queued",
        "dispatched",
        "running",
        "completed",
        "failed",
        "canceled",
    ] = "queued"
    snapshot_id: str | None = None
    error: str | None = None
    requested_by: str
    idempotency_key: str
    maintenance_work_item_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourcePolicyDecision(KnowledgeModel):
    allowed: bool
    source_id: str
    source_version: str
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    model_processing_allowed: bool = False


def evaluate_source_policy(source: SourceDefinition) -> SourcePolicyDecision:
    blockers: list[str] = []
    warnings: list[str] = []
    if source.status != "active":
        blockers.append(f"source status is {source.status}")
    if source.license_status != "allowed":
        blockers.append(f"license status is {source.license_status}")
    if source.kind == "website" and source.robots_policy != "respect":
        blockers.append("website acquisition must respect robots policy")
    if source.kind == "website" and source.robots_status != "allowed":
        blockers.append(f"robots status is {source.robots_status}")
    if source.kind == "api" and source.robots_policy != "explicit-api":
        blockers.append("API sources require explicit-api policy")
    if not source.schedule:
        warnings.append("source has no automatic acquisition schedule")
    if source.trust_tier == "community":
        warnings.append("community source claims require corroboration")
    return SourcePolicyDecision(
        allowed=not blockers,
        source_id=source.id,
        source_version=source.version,
        blockers=blockers,
        warnings=warnings,
        model_processing_allowed=(
            not blockers
            and source.model_processing_policy == "approved-gateway"
        ),
    )
