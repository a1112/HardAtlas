import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .knowledge import KnowledgeModel


class Permission(StrEnum):
    MAINTENANCE_READ = "maintenance.read"
    AGENT_RUN = "agent.run"
    PROPOSAL_CREATE = "proposal.create"
    PROPOSAL_EVALUATE = "proposal.evaluate"
    PROPOSAL_REVIEW = "proposal.review"
    RELEASE_STAGE = "release.stage"
    RELEASE_PUBLISH = "release.publish"
    RELEASE_ROLLBACK = "release.rollback"
    SOURCE_REGISTER = "source.register"
    SOURCE_READ = "source.read"
    AUDIT_READ = "audit.read"


ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "agent-runner": {
        Permission.MAINTENANCE_READ,
        Permission.AGENT_RUN,
        Permission.PROPOSAL_CREATE,
    },
    "editor": {Permission.MAINTENANCE_READ, Permission.PROPOSAL_CREATE},
    "policy-evaluator": {
        Permission.MAINTENANCE_READ,
        Permission.PROPOSAL_EVALUATE,
    },
    "reviewer": {Permission.MAINTENANCE_READ, Permission.PROPOSAL_REVIEW},
    "publisher": {
        Permission.MAINTENANCE_READ,
        Permission.RELEASE_STAGE,
        Permission.RELEASE_PUBLISH,
        Permission.RELEASE_ROLLBACK,
    },
    "source-admin": {
        Permission.MAINTENANCE_READ,
        Permission.SOURCE_REGISTER,
        Permission.SOURCE_READ,
    },
    "auditor": {
        Permission.MAINTENANCE_READ,
        Permission.AUDIT_READ,
        Permission.SOURCE_READ,
    },
}
ALL_PERMISSIONS = set(Permission)


class Principal(KnowledgeModel):
    subject: str
    display_name: str
    email: str | None = None
    workspace_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    authentication_method: Literal["development", "oidc", "system"]

    def permissions(self) -> set[Permission]:
        if "admin" in self.roles:
            return ALL_PERMISSIONS
        return {
            permission
            for role in self.roles
            for permission in ROLE_PERMISSIONS.get(role, set())
        }

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions()


class AuditEvent(KnowledgeModel):
    id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: Principal
    action: str
    resource_type: str
    resource_id: str
    outcome: Literal["success", "denied", "failed"]
    request_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str | None = None
    event_hash: str


def audit_event_hash(document: dict[str, Any]) -> str:
    payload = {key: value for key, value in document.items() if key != "eventHash"}
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def build_audit_event(
    *,
    event_id: str,
    actor: Principal,
    action: str,
    resource_type: str,
    resource_id: str,
    outcome: Literal["success", "denied", "failed"],
    request_id: str,
    metadata: dict[str, Any] | None = None,
    previous_hash: str | None = None,
    occurred_at: datetime | None = None,
) -> AuditEvent:
    event = AuditEvent(
        id=event_id,
        occurred_at=occurred_at or datetime.now(UTC),
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        request_id=request_id,
        metadata=metadata or {},
        previous_hash=previous_hash,
        event_hash="",
    )
    return event.model_copy(
        update={
            "event_hash": audit_event_hash(
                event.model_dump(mode="json", by_alias=True)
            )
        }
    )
