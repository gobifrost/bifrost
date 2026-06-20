"""Contract models for CLI file push/pull operations."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FilePullRequest(BaseModel):
    """Request to pull files from server."""
    prefix: str = Field(..., description="Repo prefix to pull from")
    local_hashes: dict[str, str] = Field(
        default_factory=dict, description="Map of path to sha256 hash"
    )


class FilePullResponse(BaseModel):
    """Response for file pull."""
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Map of path to base64-encoded content for changed files",
    )
    deleted: list[str] = Field(
        default_factory=list,
        description="Paths that exist locally but not on server",
    )
    manifest_files: dict[str, str] = Field(
        default_factory=dict,
        description="Regenerated .bifrost/*.yaml",
    )


class WatchSessionRequest(BaseModel):
    """Request to manage a CLI watch session."""
    action: Literal["start", "stop", "heartbeat"]
    prefix: str
    session_id: str | None = None
