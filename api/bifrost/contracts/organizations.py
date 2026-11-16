"""Minimal CLI-side mirror of organization create/update DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrganizationCreate(BaseModel):
    """Input for creating an organization (CLI mirror)."""

    name: str = Field(max_length=255)
    domain: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)
    is_provider: bool = Field(default=False)
    settings: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class OrganizationUpdate(BaseModel):
    """Input for updating an organization (CLI mirror)."""

    name: str | None = None
    domain: str | None = None
    is_active: bool | None = None
    settings: dict | None = None
    tags: list[str] | None = None
