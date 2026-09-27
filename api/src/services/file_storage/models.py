"""Data models for file storage operations."""

from dataclasses import dataclass


@dataclass
class WorkflowIdConflictInfo:
    """Info about a workflow that would lose its ID on overwrite."""

    name: str  # Workflow display name from decorator
    function_name: str  # Python function name
    existing_id: str  # UUID from database
    file_path: str


@dataclass
class FileDiagnosticInfo:
    """A file-specific issue detected during save/indexing."""

    severity: str  # "error", "warning", "info"
    message: str
    line: int | None = None
    column: int | None = None
    source: str = "bifrost"  # e.g., "syntax", "indexing", "sdk"


@dataclass
class PendingDeactivationInfo:
    """Info about a workflow that would be deactivated on save."""

    id: str  # Workflow UUID
    name: str  # Display name from decorator
    function_name: str  # Python function name
    path: str  # File path
    description: str | None
    decorator_type: str  # "workflow", "tool", "data_provider", "service"
    has_executions: bool
    last_execution_at: str | None  # ISO 8601
    endpoint_enabled: bool
    affected_entities: list[dict[str, str]]  # List of {entity_type, id, name, reference_type}


@dataclass
class AvailableReplacementInfo:
    """Info about a function that could replace a deactivated workflow."""

    function_name: str
    name: str  # From decorator or function name
    decorator_type: str  # "workflow", "tool", "data_provider", "service"
    similarity_score: float  # 0.0-1.0


@dataclass
class WriteResult:
    """Result of a file write operation."""

    file_record: None  # Deprecated — was WorkspaceFile, now always None
    final_content: bytes
    content_modified: bool  # True if forms/agents were modified for ID alignment
    needs_indexing: bool = False  # Legacy field, always False
    workflow_id_conflicts: list[WorkflowIdConflictInfo] | None = None  # Legacy field, always None
    diagnostics: list[FileDiagnosticInfo] | None = None  # File issues detected during save
    # Deactivation protection
    pending_deactivations: list[PendingDeactivationInfo] | None = None
    available_replacements: list[AvailableReplacementInfo] | None = None
