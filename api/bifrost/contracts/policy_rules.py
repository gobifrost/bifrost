"""Minimal CLI-side mirrors of policy-rule DTOs."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Domain = Literal["file", "table"]


class PolicyRuleCreate(BaseModel):
    """Input for creating a named policy rule (CLI mirror)."""

    name: str = Field(min_length=1, max_length=100)
    domain: Domain
    description: str | None = None
    body: dict[str, Any]
    organization_id: UUID | None = None


class PolicyRuleUpdate(BaseModel):
    """Input for updating a named policy rule (CLI mirror)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    body: dict[str, Any] | None = None
    # domain is immutable — not surfaced in update.
