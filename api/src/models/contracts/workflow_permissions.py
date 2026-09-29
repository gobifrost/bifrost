"""Workflow permission mode and grants, and Solution permission requests.

Data only: nothing reads these at run time until delegated execution.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from src.services.role_permissions import RolePermissionError, validate_permission

GrantBoundary = Literal["organization", "managed_organizations", "platform"]


class WorkflowPermissionMode(StrEnum):
    FULL = "full"
    RESTRICTED = "restricted"


class RequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"


class WorkflowPermissionGrantSpec(BaseModel):
    """One requested grant: a permission plus an optional cross-org boundary.

    No boundary means the run's own organization.
    """

    model_config = ConfigDict(frozen=True)

    permission: str
    boundary: GrantBoundary | None = None

    @field_validator("permission")
    @classmethod
    def _known_permission(cls, value: str) -> str:
        try:
            validate_permission(value)
        except RolePermissionError as exc:
            raise ValueError(exc.detail) from exc
        return value


class RequestedWorkflowPermissions(BaseModel):
    """A workflow's requested mode and grants, as declared by a Solution."""

    mode: WorkflowPermissionMode
    grants: list[WorkflowPermissionGrantSpec] = []

    @model_validator(mode="after")
    def _consistent(self) -> "RequestedWorkflowPermissions":
        if self.mode is WorkflowPermissionMode.FULL and self.grants:
            raise ValueError("grants must be empty when mode is 'full'")
        if len(set(self.grants)) != len(self.grants):
            raise ValueError("duplicate grants")
        return self


class WorkflowGrant(WorkflowPermissionGrantSpec):
    """A stored grant; ``organization_id`` is set only for an 'organization' boundary."""

    organization_id: UUID | None = None
