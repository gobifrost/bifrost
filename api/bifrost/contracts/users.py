"""Minimal CLI-side mirror of role create/update DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    """Input for creating a role (CLI mirror)."""

    name: str = Field(max_length=100)
    description: str | None = Field(default=None)
    permissions: dict | None = Field(default=None)
    scopes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Input for updating a role (CLI mirror)."""

    name: str | None = None
    description: str | None = None
    permissions: dict | None = Field(default=None)
    scopes: list[str] | None = Field(default=None)
