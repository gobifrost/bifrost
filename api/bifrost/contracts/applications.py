"""Minimal CLI-side mirror of application create/update DTOs."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ApplicationCreate(BaseModel):
    """Input for creating an application (CLI mirror)."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None)
    icon: str | None = Field(default=None, max_length=50)
    slug: str = Field(min_length=1, max_length=255)
    access_level: str = Field(default="authenticated")
    app_model: str = Field(default="inline_v1")
    role_ids: list[UUID] = Field(default_factory=list)
    organization_id: UUID | None = Field(default=None)


class ApplicationUpdate(BaseModel):
    """Input for updating application metadata (CLI mirror)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=50)
    organization_id: UUID | None = Field(default=None)
    access_level: str | None = Field(default=None)
    role_ids: list[UUID] | None = Field(default=None)
