"""Contracts for identities: the accounts that run work no person started."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from src.models.contracts.role_assignments import RoleBoundaryPublic

IdentityName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class IdentityBaseRole(BaseModel):
    id: UUID
    name: str


class IdentityRole(BaseModel):
    """An additional role the identity holds, and where it applies."""

    role_id: UUID
    name: str
    boundaries: list[RoleBoundaryPublic]


class IdentityPublic(BaseModel):
    id: UUID
    name: str
    identity_kind: Literal["org_default", "global_default", "custom"]
    organization_id: UUID | None = Field(description="Null for Global identities.")
    organization_name: str | None
    base_role: IdentityBaseRole
    additional_roles: list[IdentityRole]
    workflows_using: int = Field(
        description=(
            "Workflows that run unattended as this identity. A default identity also runs the "
            "workflows of its organization (Global: of no organization) that name no identity."
        )
    )


class IdentityCreate(BaseModel):
    """A new custom identity (base role User, no additional roles)."""

    name: IdentityName
    organization_id: UUID | None = Field(description="The identity's organization; null for a Global identity.")


class IdentityUpdate(BaseModel):
    name: IdentityName
