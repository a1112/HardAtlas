from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator

from .knowledge import KnowledgeModel
from .quality import MaintenanceRoute


class AgentRuntime(KnowledgeModel):
    """Persisted identity and capacity advertised by one Agent process."""

    id: str
    definition_id: str
    definition_version: str
    capabilities: list[str]
    supported_routes: list[MaintenanceRoute]
    status: Literal["online", "draining"] = "online"
    max_concurrency: int = Field(default=1, ge=1, le=100)
    heartbeat_ttl_seconds: int = Field(default=90, ge=30, le=600)
    labels: dict[str, str] = Field(default_factory=dict)
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("capabilities")
    @classmethod
    def normalize_capabilities(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip() for item in value if item.strip()})
        if not normalized:
            raise ValueError("agent runtime requires at least one capability")
        return normalized

    @field_validator("supported_routes")
    @classmethod
    def normalize_routes(
        cls,
        value: list[MaintenanceRoute],
    ) -> list[MaintenanceRoute]:
        normalized = sorted(set(value))
        if not normalized:
            raise ValueError("agent runtime requires at least one supported route")
        return normalized


class AgentRuntimeState(KnowledgeModel):
    runtime: AgentRuntime
    effective_status: Literal["online", "draining", "offline"]
    active_lease_count: int = Field(ge=0)
    available_capacity: int = Field(ge=0)
    observed_at: datetime
