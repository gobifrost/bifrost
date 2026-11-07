"""Contracts for the default base role used during account provisioning."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class BaseRoleDefaultsResponse(BaseModel):
    """The configured default at a scope and its resolved effective role."""

    default_base_role_id: UUID | None = Field(
        description="Configured role; null for an organization that inherits the global default."
    )
    effective_base_role_id: UUID
    effective_base_role_name: str
    source: Literal["global", "organization"]


class BaseRoleDefaultsUpdate(BaseModel):
    """Replace a default base role; null resets/inherits for its scope."""

    default_base_role_id: UUID | None
