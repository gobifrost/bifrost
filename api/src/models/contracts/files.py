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


class WorkspaceFilePatchRequest(BaseModel):
    """Conflict-safe unique-string edit for the global source workspace."""

    path: str = Field(..., min_length=1, description="Workspace-relative file path")
    old_string: str = Field(..., min_length=1, description="Unique text to replace")
    new_string: str = Field(default="", description="Replacement text")
    expected_version: str | None = Field(
        default=None,
        description="Optional version returned by the file stat operation",
    )
    force_deactivation: bool = Field(
        default=False,
        description="Allow workflows removed by this edit to be deactivated",
    )
    replacements: dict[str, str] | None = Field(
        default=None,
        description="Map old workflow IDs to replacement function names",
    )
    workflows_to_deactivate: list[str] | None = Field(
        default=None,
        description="Workflow IDs explicitly selected for deactivation",
    )


class WorkspaceFilePatchResponse(BaseModel):
    """Result of a successful workspace patch."""

    path: str
    version: str
    lines_changed: int = Field(ge=1)
    content_modified: bool = False
    needs_indexing: bool = False
    diagnostics: list[dict] = Field(default_factory=list)
