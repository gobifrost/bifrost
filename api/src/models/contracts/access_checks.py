"""Contracts for explaining stored ``access.check`` events and what-if access checks."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.contracts.audit import AuditLogEntry


class AccessStep(BaseModel):
    """One step of an access trace."""

    key: str
    label: str
    status: Literal["passed", "stopped", "not_applicable", "not_reached"]
    reason: str
    facts: dict[str, Any] = {}


class AccessTrace(BaseModel):
    """What the managed-identity model decides, step by step."""

    outcome: Literal["success", "failure"]
    enforced: bool
    steps: list[AccessStep]


class AccessCheckRequest(BaseModel):
    """A what-if: could this user do this operation in this organization?"""

    organization_id: UUID | Literal["global"] = Field(description='Organization to act in, or "global".')
    operation: str = Field(
        description='Access-list operation: a catalog id (e.g. tables.documents.create) or "METHOD /api/path".'
    )
    workflow_id: UUID | None = Field(
        default=None, description="Workflow whose powers apply; omit for the user acting directly."
    )


NowUnavailable = Literal["rows_not_stored", "run_user_missing", "workflow_missing", "solution_not_recorded"]


class AccessExplanation(BaseModel):
    """A stored access check, as decided then and judged again now."""

    event: AuditLogEntry
    then: AccessTrace
    now: AccessTrace | None
    now_unavailable: NowUnavailable | None
    changed: bool | None
