"""Contracts for Recommended Access: what the identity a workflow runs as would
also need, based on its recorded runs (the Access panel's Recommended Access)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.contracts.role_assignments import RoleBoundaryInput

RecommendedAccessKind = Literal["reach", "policy_role", "workflow_role"]


class RecommendedGrant(BaseModel):
    """The role assignment a recommendation asks for: ``role_id`` applied at
    ``boundaries``, merged into the identity's existing assignment of that role."""

    role_id: UUID
    boundaries: list[RoleBoundaryInput]


class RecommendedAccessItem(BaseModel):
    kind: RecommendedAccessKind
    label: str
    detail: str
    organization_id: UUID | None = Field(
        description=(
            "The organization a reach recommendation targets, or where a policy role's grant would be "
            "placed; null for everything, for Global, and for a workflow role."
        )
    )
    grant: RecommendedGrant | None = Field(
        description="Null when no single role assignment meets it (a role to choose, or information only)."
    )


class RecommendedAccess(BaseModel):
    """What the identity a workflow runs as would also need, based on its recorded runs."""

    identity_id: UUID = Field(description="The identity the recommendations are computed for.")
    observed_runs: int = Field(description="Runs in the window that recorded access checks for this workflow.")
    window_days: int = Field(description="How far back the recorded checks reach: the audit log's hot window.")
    items: list[RecommendedAccessItem]
