"""
Audit log API contracts (Pydantic models).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from src.models.contracts.audit_retention import AuditRetentionInfo


class AuditLogActor(BaseModel):
    """Who performed the action."""

    user_id: UUID | None = Field(None, description="Acting user's ID (null for system events)")
    user_email: str | None = Field(None, description="Acting user's email")
    user_name: str | None = Field(None, description="Acting user's display name")
    organization_id: UUID | None = Field(
        None, description="Organization the event happened in (an access check's target organization)"
    )
    organization_name: str | None = Field(None, description="Name of the organization the event happened in")
    home_organization_id: UUID | None = Field(
        None, description="The organization the acting user belongs to (null for Global or a deleted user)"
    )
    home_organization_name: str | None = Field(None, description="Name of the acting user's own organization")


class AuditLogEntry(BaseModel):
    """A single audit log entry."""

    id: UUID
    timestamp: datetime = Field(..., description="When the event occurred")
    action: str = Field(..., description="Dotted event name, e.g. 'user.create'")
    resource_type: str | None = Field(None, description="Target entity type")
    resource_id: UUID | None = Field(None, description="Target entity ID")
    outcome: str = Field(..., description="'success' or 'failure'")
    source: str = Field(..., description="Event source: 'http', 'sso_sync', 'scheduler', 'cli', ...")
    operation_id: str | None = Field(
        None, description="Catalog operation id for the route that produced this event"
    )
    surface: str | None = Field(
        None,
        description="Transport the request came in over: 'web', 'cli', 'mcp', 'embed', 'workflow', 'service'",
    )
    execution_id: UUID | None = Field(
        None,
        description="Workflow execution that produced the event, when supported",
    )
    actor: AuditLogActor = Field(..., description="Who performed the action")
    ip_address: str | None = Field(None)
    user_agent: str | None = Field(None)
    details: dict[str, Any] | None = Field(None, description="Event-specific metadata")
    workflow_name: str | None = Field(
        None, description="Name of the workflow the event names (details.workflow_id), when it still exists"
    )

    @field_serializer("timestamp")
    def _serialize_ts(self, dt: datetime) -> str:
        return dt.isoformat()


class AuditLogGroup(BaseModel):
    """Entries sharing one value of the requested ``group_by``."""

    key: str | None = Field(None, description="The group's value (null when entries have none)")
    count: int = Field(..., description="Matching entries in the group")
    last_seen: datetime = Field(..., description="Newest entry's time")
    sample: AuditLogEntry = Field(..., description="Newest entry in the group")

    @field_serializer("last_seen")
    def _serialize_ts(self, dt: datetime) -> str:
        return dt.isoformat()


class AuditLogListResponse(BaseModel):
    """Paginated audit log list response, or groups when ``group_by`` is set."""

    entries: list[AuditLogEntry] = Field(..., description="Audit log entries, newest first")
    groups: list[AuditLogGroup] | None = Field(
        None, description="Groups, largest first (only with group_by; entries is then empty)"
    )
    continuation_token: str | None = Field(
        None, description="Opaque token for next page (null when no more)"
    )
    retention: AuditRetentionInfo | None = Field(
        None, description="What the database holds and what is archived"
    )
