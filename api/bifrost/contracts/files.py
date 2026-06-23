"""Minimal CLI-side mirrors of file policy DTOs."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class FilePolicyCreate(BaseModel):
    """Input for creating or replacing a file policy row."""

    location: str = Field(default="workspace")
    path: str = Field(min_length=1)
    organization_id: UUID | None = Field(default=None)
    policies: dict[str, Any] = Field(default_factory=dict)


class FilePolicyUpdate(BaseModel):
    """Input for replacing a file policy document."""

    policies: dict[str, Any] | None = None
