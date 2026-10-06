"""Contracts for what a workflow's identity lacks (the Access panel's Requirements)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.contracts.role_assignments import RoleBoundaryInput

RequirementKind = Literal["reach", "policy_role", "workflow_role"]


class RequirementGrant(BaseModel):
    """The role assignment that would meet a requirement: ``role_id`` applied at
    ``boundaries``, merged into the identity's existing assignment of that role."""

    role_id: UUID
    boundaries: list[RoleBoundaryInput]


class WorkflowRequirement(BaseModel):
    kind: RequirementKind
    label: str
    detail: str
    grant: RequirementGrant | None = Field(
        description="Null when no single role assignment meets it (a role to choose, or information only)."
    )


class WorkflowRequirements(BaseModel):
    identity_id: UUID = Field(description="The identity the requirements are computed for.")
    observed_runs: int = Field(description="Runs in the window that recorded access checks for this workflow.")
    window_days: int = Field(description="How far back the recorded checks reach: the audit log's hot window.")
    items: list[WorkflowRequirement]
