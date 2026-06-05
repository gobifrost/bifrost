"""Minimal CLI-side mirror of workflow update DTO.

Workflows have no ``Create`` DTO — workflows are created by registering a
decorated Python function. Only the update surface needs flag generation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WorkflowUpdateRequest(BaseModel):
    """Request model for updating a workflow's editable properties."""

    organization_id: str | None = Field(default=None)
    access_level: str | None = Field(default=None)
    clear_roles: bool = Field(default=False)
    role_ids: list[str] | None = Field(default=None)
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="MCP tool name for this workflow. Defaults to the Python function name on registration.",
    )
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=100)
    timeout_seconds: int | None = Field(default=None, ge=0, le=86400)
    execution_mode: Literal["sync", "async"] | None = Field(default=None)
    time_saved: int | None = Field(default=None, ge=0)
    value: float | None = Field(default=None, ge=0.0)
    tool_description: str | None = Field(default=None, max_length=1000)
    cache_ttl_seconds: int | None = Field(default=None, ge=0, le=86400)
    tags: list[str] | None = Field(default=None)
    endpoint_enabled: bool | None = Field(default=None)
    allowed_methods: list[str] | None = Field(default=None)
    public_endpoint: bool | None = Field(default=None)
    disable_global_key: bool | None = Field(default=None)
