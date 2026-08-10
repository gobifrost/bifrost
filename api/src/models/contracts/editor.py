"""
Code editor contract models for Bifrost.
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    pass


# ==================== EDITOR MODELS ====================


class FileType(str, Enum):
    """File or folder type"""
    FILE = "file"
    FOLDER = "folder"


class FileMetadata(BaseModel):
    """
    File or folder metadata
    Used in directory listing responses
    """
    path: str = Field(..., description="Relative path from /home/repo")
    name: str = Field(..., description="File or folder name")
    type: FileType = Field(..., description="File or folder")
    size: int | None = Field(default=None, description="Size in bytes (null for folders)")
    extension: str | None = Field(default=None, description="File extension (null for folders)")
    modified: str = Field(..., description="Last modified timestamp (ISO 8601)")
    entity_type: Literal["workflow", "form", "agent", "module", "text"] | None = Field(
        default=None,
        description="Platform entity type if file is a platform entity, null for regular files"
    )
    entity_id: str | None = Field(
        default=None,
        description="Platform entity ID if file is a platform entity, null for regular files"
    )
    organization_id: str | None = Field(
        default=None,
        description="Organization ID for scoped entities, null for global or non-entity files"
    )

    model_config = ConfigDict(from_attributes=True)


class FileContentRequest(BaseModel):
    """Request to write file content"""
    path: str = Field(..., description="Relative path from /home/repo")
    content: str = Field(..., description="File content (plain text or base64 encoded)")
    encoding: str = Field(default="utf-8", description="Content encoding (utf-8 or base64)")
    expected_etag: str | None = Field(default=None, description="Expected ETag for conflict detection (optional)")
    force_deactivation: bool = Field(
        default=False,
        description="Skip deactivation protection and allow workflows to be deactivated"
    )
    replacements: dict[str, str] | None = Field(
        default=None,
        description="Map of workflow_id -> new_function_name to transfer identity when renaming"
    )
    workflows_to_deactivate: list[str] | None = Field(
        default=None,
        description="List of workflow IDs to selectively deactivate (used with replacements for mixed actions)"
    )

    model_config = ConfigDict(from_attributes=True)


class WorkflowIdConflict(BaseModel):
    """
    Conflict detected when a workflow file is being overwritten and
    the new file lacks an ID that the database already has.
    """
    name: str = Field(..., description="Workflow display name from decorator")
    function_name: str = Field(..., description="Python function name")
    existing_id: str = Field(..., description="UUID from database that would be lost")
    file_path: str = Field(..., description="Path of the file being saved")

    model_config = ConfigDict(from_attributes=True)


class FileDiagnostic(BaseModel):
    """
    A file-specific issue detected during save/indexing.

    These are returned to the client for display in the editor (Monaco markers)
    and also used to create system notifications for errors.
    """
    severity: Literal["error", "warning", "info"] = Field(
        ..., description="Severity level of the diagnostic"
    )
    message: str = Field(..., description="Human-readable description of the issue")
    line: int | None = Field(default=None, description="Line number (1-indexed) if applicable")
    column: int | None = Field(default=None, description="Column number if applicable")
    source: str = Field(
        default="bifrost",
        description="Source of the diagnostic (e.g., 'syntax', 'indexing', 'sdk')"
    )

    model_config = ConfigDict(from_attributes=True)


# ==================== DEACTIVATION PROTECTION MODELS ====================


class AffectedEntity(BaseModel):
    """Entity that depends on a workflow being deactivated."""
    entity_type: Literal["form", "agent", "app"] = Field(
        ..., description="Type of the affected entity"
    )
    id: str = Field(..., description="Entity ID")
    name: str = Field(..., description="Entity display name")
    reference_type: str = Field(
        ...,
        description="How the entity references the workflow (e.g., 'workflow', 'launch_workflow', 'data_provider', 'tool')"
    )

    model_config = ConfigDict(from_attributes=True)


class PendingDeactivation(BaseModel):
    """Workflow/tool/data_provider that would be deactivated by a file save."""
    id: str = Field(..., description="Workflow UUID")
    name: str = Field(..., description="Display name from decorator")
    function_name: str = Field(..., description="Python function name")
    path: str = Field(..., description="File path")
    description: str | None = Field(default=None, description="Workflow description")
    decorator_type: Literal["workflow", "tool", "data_provider"] = Field(
        ..., description="Type of decorator"
    )
    has_executions: bool = Field(
        default=False, description="Whether this workflow has execution history"
    )
    last_execution_at: str | None = Field(
        default=None, description="Last execution timestamp (ISO 8601)"
    )
    endpoint_enabled: bool = Field(
        default=False, description="Whether HTTP endpoint is enabled"
    )
    affected_entities: list[AffectedEntity] = Field(
        default_factory=list,
        description="Forms, agents, and apps that depend on this workflow"
    )

    model_config = ConfigDict(from_attributes=True)


class AvailableReplacement(BaseModel):
    """Function that could replace a deactivated workflow."""
    function_name: str = Field(..., description="Python function name")
    name: str = Field(..., description="Display name from decorator or function name")
    decorator_type: Literal["workflow", "tool", "data_provider"] = Field(
        ..., description="Type of decorator"
    )
    similarity_score: float = Field(
        ..., ge=0.0, le=1.0, description="Similarity score to the deactivated workflow (0.0-1.0)"
    )

    model_config = ConfigDict(from_attributes=True)


class WorkflowDeactivationConflict(BaseModel):
    """409 response when workflows would be deactivated."""
    reason: Literal["workflows_would_deactivate"] = Field(
        default="workflows_would_deactivate",
        description="Conflict reason identifier"
    )
    message: str = Field(..., description="Human-readable description")
    pending_deactivations: list[PendingDeactivation] = Field(
        ..., description="Workflows that would be deactivated"
    )
    available_replacements: list[AvailableReplacement] = Field(
        default_factory=list,
        description="New functions that could replace deactivated workflows"
    )

    model_config = ConfigDict(from_attributes=True)


class FileContentResponse(BaseModel):
    """Response with file content"""
    path: str = Field(..., description="Relative path from /home/repo")
    content: str = Field(..., description="File content")
    encoding: str = Field(..., description="Content encoding")
    size: int = Field(..., description="Content size in bytes")
    etag: str = Field(..., description="ETag for change detection")
    modified: str = Field(..., description="Last modified timestamp (ISO 8601)")
    content_modified: bool = Field(
        default=False,
        description="True if server modified content (e.g., injected IDs). Client should update editor buffer."
    )
    needs_indexing: bool = Field(
        default=False,
        description="True if file has decorators that need ID injection. Client should trigger indexing."
    )
    workflow_id_conflicts: list[WorkflowIdConflict] = Field(
        default_factory=list,
        description="List of workflows that would lose their existing IDs. Client should prompt user."
    )
    diagnostics: list[FileDiagnostic] = Field(
        default_factory=list,
        description="File-specific issues detected during save (syntax errors, indexing warnings, etc.)"
    )

    model_config = ConfigDict(from_attributes=True)


class FileStatResponse(BaseModel):
    """Response with file metadata for conflict-safe CLI workflows."""
    path: str = Field(..., description="Relative path from /home/repo")
    exists: bool = Field(..., description="True if the file exists")
    version: str | None = Field(
        default=None,
        description="Opaque content version for guarded writes and deletes",
    )
    size: int | None = Field(default=None, description="File size in bytes")
    last_modified: str | None = Field(default=None, description="Last modified timestamp (ISO 8601)")
    updated_by: str | None = Field(default=None, description="User who last updated the file")

    model_config = ConfigDict(from_attributes=True)


class FileConflictResponse(BaseModel):
    """Response when file write encounters a conflict"""
    reason: Literal[
        "content_changed",
        "file_exists",
        "file_missing",
        "path_not_found",
        "version_conflict",
        "workflows_would_deactivate",
    ] = Field(
        ..., description="Type of conflict"
    )
    message: str = Field(..., description="Human-readable conflict description")
    current_etag: str | None = Field(
        default=None,
        description="Current ETag when the conflict is caused by a stale precondition",
    )
    current_last_modified: str | None = Field(
        default=None,
        description="Current modified timestamp when the conflict is caused by a stale precondition",
    )
    current_updated_by: str | None = Field(
        default=None,
        description="Current last editor when the conflict is caused by a stale precondition",
    )
    current_version: str | None = Field(
        default=None,
        description="Current opaque version when a guarded CLI mutation conflicts",
    )
    # Fields for workflows_would_deactivate conflicts
    pending_deactivations: list[PendingDeactivation] | None = Field(
        default=None,
        description="Workflows that would be deactivated (only for workflows_would_deactivate)"
    )
    available_replacements: list[AvailableReplacement] | None = Field(
        default=None,
        description="New functions that could replace deactivated workflows (only for workflows_would_deactivate)"
    )

    model_config = ConfigDict(from_attributes=True)


class SearchRequest(BaseModel):
    """Search query request"""
    query: str = Field(..., min_length=1, description="Search text or regex pattern")
    case_sensitive: bool = Field(default=False, description="Case-sensitive matching")
    is_regex: bool = Field(default=False, description="Treat query as regex")
    include_pattern: str | None = Field(default="**/*", description="Glob pattern for files to search")
    max_results: int = Field(default=1000, ge=1, le=10000, description="Maximum results to return")

    model_config = ConfigDict(from_attributes=True)


class SearchResult(BaseModel):
    """Single search match result"""
    file_path: str = Field(..., description="Relative path to file containing match")
    line: int = Field(..., ge=1, description="Line number (1-indexed)")
    column: int = Field(..., ge=0, description="Column number (0-indexed)")
    match_text: str = Field(..., description="The matched text")
    context_before: str | None = Field(default=None, description="Line before match")
    context_after: str | None = Field(default=None, description="Line after match")

    model_config = ConfigDict(from_attributes=True)


class SearchResponse(BaseModel):
    """Search results response"""
    query: str = Field(..., description="Original search query")
    total_matches: int = Field(..., description="Total matches found")
    files_searched: int = Field(..., description="Number of files searched")
    results: list[SearchResult] = Field(..., description="Array of search results")
    truncated: bool = Field(..., description="Whether results were truncated")
    search_time_ms: int = Field(..., description="Search duration in milliseconds")

    model_config = ConfigDict(from_attributes=True)


class ScriptExecutionRequest(BaseModel):
    """Request model for executing a Python script"""
    code: str = Field(..., description="Python code to execute")
    timeout_seconds: int | None = Field(default=None, description="Optional timeout in seconds")


class ScriptExecutionResponse(BaseModel):
    """Response model for script execution"""
    execution_id: str = Field(..., description="Unique execution identifier")
    status: Literal["Success", "Failed"] = Field(..., description="Execution status")
    output: str = Field(..., description="Combined stdout/stderr output")
    result: dict[str, str] | None = Field(default=None, description="Execution result data")
    error: str | None = Field(default=None, description="Error message if execution failed")
    duration_ms: int = Field(..., description="Execution duration in milliseconds")
    started_at: datetime = Field(..., description="Execution start timestamp")
    completed_at: datetime = Field(..., description="Execution completion timestamp")
