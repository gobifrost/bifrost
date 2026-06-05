"""
Workflow metadata and validation contract models for Bifrost.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import FormAccessLevel
from src.models.contracts.base import RetryPolicy

if TYPE_CHECKING:
    pass


# ==================== EXECUTABLE TYPE ====================


class ExecutableType(str, Enum):
    """Type discriminator for all executable user code."""
    WORKFLOW = "workflow"
    TOOL = "tool"
    DATA_PROVIDER = "data_provider"


# ==================== WORKFLOW PARAMETER & METADATA ====================


class WorkflowParameter(BaseModel):
    """Workflow parameter metadata - derived from function signature.

    Note: Form-specific fields like data_provider, help_text, validation
    belong on FormField, not here. Workflow parameters come from Python
    function signatures and don't have form-specific metadata.
    """
    name: str
    type: str  # string, int, bool, etc.
    required: bool
    label: str | None = None
    default_value: Any | None = None
    description: str | None = None
    options: list[dict[str, str]] | None = None  # For Literal types - [{label, value}, ...]


class WorkflowMetadata(BaseModel):
    """Workflow metadata for discovery API.

    This model represents all executable types (workflow, tool, data_provider)
    via the `type` field discriminator.
    """
    # Unique identifier
    id: str = Field(..., description="Workflow UUID")

    # Required fields
    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="MCP tool name for this workflow. Defaults to the Python function name on registration.",
    )
    function_name: str | None = Field(default=None, description="Python function name (for CodeLens registration status)")
    display_name: str | None = Field(default=None, description="Optional UI display name (falls back to the tool name if not set)")
    description: str | None = Field(default=None, description="Human-readable description")

    # Type discriminator - distinguishes workflow/tool/data_provider
    type: ExecutableType = Field(default=ExecutableType.WORKFLOW, description="Executable type: workflow, tool, or data_provider")

    # Organization scoping - NULL means global (available to all orgs)
    organization_id: str | None = Field(default=None, description="Organization ID if org-scoped, None for global")

    # Access control
    access_level: str = Field(default="role_based", description="Access level: 'authenticated' (any logged-in user) or 'role_based' (specific roles required)")

    # Optional fields with defaults
    category: str = Field(default="General", description="Category for organization")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization and search")
    parameters: list[WorkflowParameter] = Field(default_factory=list, description="Workflow parameters")

    # Execution configuration
    execution_mode: Literal["sync", "async"] = Field(default="sync", description="Execution mode")
    timeout_seconds: int = Field(default=1800, ge=0, le=86400, description="Max execution time in seconds. 0 = no timeout. Default 1800 (30 min), max 86400 (24h).")

    # Retry policy (for future use)
    retry_policy: RetryPolicy | None = Field(default=None, description="Retry configuration")

    # HTTP Endpoint configuration
    endpoint_enabled: bool = Field(default=False, description="Whether workflow is exposed as HTTP endpoint")
    allowed_methods: list[str] = Field(default_factory=lambda: ["POST"], description="Allowed HTTP methods")
    disable_global_key: bool = Field(default=False, description="If true, only workflow-specific API keys work")
    public_endpoint: bool = Field(default=False, description="If true, skip authentication for webhooks")

    # Tool configuration (for AI agent tool calling)
    # NOTE: is_tool is deprecated - use type == ExecutableType.TOOL instead
    is_tool: bool = Field(default=False, description="[Deprecated] Use type='tool' instead. Whether workflow is available as an AI tool")
    tool_description: str | None = Field(default=None, description="Description optimized for AI tool selection")

    # Data provider configuration
    cache_ttl_seconds: int = Field(default=300, description="Cache TTL in seconds (for data providers)")

    # Economics - value metrics for reporting
    time_saved: int = Field(default=0, description="Minutes saved per execution")
    value: float = Field(default=0.0, description="Flexible value unit (e.g., cost savings, revenue)")

    # Dependency tracking
    used_by_count: int = Field(default=0, description="Number of entities (forms, agents, apps) that reference this workflow")

    # Source tracking
    source_file_path: str | None = Field(default=None, description="Full file path to the workflow source code")
    relative_file_path: str | None = Field(default=None, description="Workspace-relative file path with /workspace/ prefix (e.g., '/workspace/workflows/my_workflow.py')")

    # Timestamps
    created_at: datetime = Field(..., description="When the workflow was first discovered")


class DataProviderMetadata(BaseModel):
    """Data provider metadata from @data_provider decorator.

    NOTE: Data providers are being consolidated into the workflows table.
    This model is kept for backward compatibility with existing code.
    New code should use WorkflowMetadata with type=ExecutableType.DATA_PROVIDER.
    """
    id: str | None = Field(default=None, description="Data provider UUID (when loaded from database)")
    name: str
    description: str | None = None
    type: ExecutableType = Field(default=ExecutableType.DATA_PROVIDER, description="Always 'data_provider' for this model")
    category: str = "General"
    tags: list[str] = Field(default_factory=list, description="Tags for categorization and search")
    timeout_seconds: int = Field(default=300, ge=0, le=86400, description="Max execution time in seconds. 0 = no timeout. Default 300 (5 min), max 86400 (24h).")
    cache_ttl_seconds: int = Field(default=300, description="Cache TTL in seconds")
    parameters: list[WorkflowParameter] = Field(default_factory=list, description="Input parameters from @param decorators")
    source_file_path: str | None = Field(default=None, description="Full file path to the data provider source code")
    relative_file_path: str | None = Field(default=None, description="Workspace-relative file path with /workspace/ prefix (e.g., '/workspace/data_providers/my_provider.py')")


class FormDiscoveryMetadata(BaseModel):
    """Lightweight form metadata for discovery endpoint"""
    id: str
    name: str
    workflow_id: str | None = None
    org_id: str
    is_active: bool
    is_global: bool
    access_level: FormAccessLevel | str | None = None
    created_at: datetime
    updated_at: datetime


class MetadataResponse(BaseModel):
    """Response model for /admin/workflow endpoint"""
    workflows: list[WorkflowMetadata] = Field(default_factory=list)
    data_providers: list[DataProviderMetadata] = Field(default_factory=list)
    forms: list[FormDiscoveryMetadata] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


# ==================== WORKFLOW REGISTRATION ====================


class RegisterWorkflowRequest(BaseModel):
    """Request model for explicit workflow registration."""
    path: str = Field(..., description="Workspace-relative path to the .py file")
    function_name: str = Field(..., description="Name of the decorated function to register")
    organization_id: str | None = Field(default=None, description="Organization ID to scope the workflow to, or null for global scope")
    access_level: str | None = Field(
        default=None,
        description="Access level: 'authenticated' (any logged-in user) or 'role_based' (specific roles required). Omit to leave at the schema default.",
    )
    role_ids: list[str] | None = Field(
        default=None,
        description=(
            "Role IDs for role_based access. Omit to leave unchanged when "
            "reactivating; pass an empty list to clear."
        ),
    )


class RegisterWorkflowResponse(BaseModel):
    """Response model for workflow registration."""
    id: str = Field(..., description="Workflow UUID")
    name: str = Field(..., description="MCP tool name, defaulted from function_name on registration")
    function_name: str = Field(..., description="Python function name")
    path: str = Field(..., description="File path")
    type: str = Field(..., description="Executable type: workflow, tool, or data_provider")
    description: str | None = Field(default=None, description="Workflow description")
    organization_id: str | None = Field(default=None, description="Organization ID if org-scoped, null for global")


# ==================== WORKFLOW VALIDATION ====================


class ValidationIssue(BaseModel):
    """A single validation error or warning"""
    line: int | None = Field(default=None, description="Line number where issue occurs (if applicable)")
    message: str = Field(..., description="Human-readable error or warning message")
    severity: Literal["error", "warning"] = Field(..., description="Severity level")


class WorkflowValidationRequest(BaseModel):
    """Request model for workflow validation endpoint"""
    path: str = Field(..., description="Relative workspace path to the workflow file")
    content: str | None = Field(default=None, description="File content to validate (if not provided, reads from disk)")


class WorkflowValidationResponse(BaseModel):
    """Response model for workflow validation endpoint"""
    valid: bool = Field(..., description="True if workflow is valid and will be discovered")
    issues: list[ValidationIssue] = Field(default_factory=list, description="List of errors and warnings")
    metadata: WorkflowMetadata | None = Field(default=None, description="Workflow metadata if valid")


# ==================== WORKFLOW API KEYS ====================


class WorkflowKey(BaseModel):
    """Workflow API Key for HTTP access without user authentication"""
    id: str = Field(default_factory=lambda: str(__import__('uuid').uuid4()), description="Unique key ID")
    hashed_key: str = Field(..., description="SHA-256 hash of the API key")
    workflow_id: str | None = Field(default=None, description="Workflow-specific key, or None for global access")
    created_by: str = Field(..., description="User email who created the key")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None
    revoked: bool = Field(default=False)
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    expires_at: datetime | None = Field(default=None, description="Optional expiration timestamp")
    description: str | None = Field(default=None, description="Optional key description")
    disable_global_key: bool = Field(default=False, description="If true, workflow opts out of global API keys")


class WorkflowKeyCreateRequest(BaseModel):
    """Request model for creating a workflow API key"""
    workflow_id: str | None = Field(default=None, description="Workflow UUID for workflow-specific key")
    expires_in_days: int | None = Field(default=None, description="Days until key expires (default: no expiration)")
    description: str | None = Field(default=None, description="Optional key description")
    disable_global_key: bool = Field(default=False, description="If true, workflow opts out of global API keys")


class WorkflowKeyResponse(BaseModel):
    """Response model for workflow key (includes raw key on creation only)"""
    id: str
    raw_key: str | None = Field(default=None, description="Raw API key (only returned on creation)")
    masked_key: str | None = Field(default=None, description="Last 4 characters for display")
    workflow_id: str | None = None
    workflow_name: str | None = None
    created_by: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked: bool
    expires_at: datetime | None = None
    description: str | None = None
    disable_global_key: bool = Field(default=False, description="If true, workflow opts out of global API keys")


# ==================== WORKFLOW USAGE STATS ====================


class EntityUsage(BaseModel):
    """Usage count for a single entity (form, app, agent)."""
    id: str = Field(..., description="Entity UUID")
    name: str = Field(..., description="Entity name")
    workflow_count: int = Field(..., description="Number of workflows referenced by this entity")


class WorkflowUsageStats(BaseModel):
    """Aggregated workflow usage stats by entity type."""
    forms: list[EntityUsage] = Field(default_factory=list, description="Forms and their workflow counts")
    apps: list[EntityUsage] = Field(default_factory=list, description="Apps and their workflow counts")
    agents: list[EntityUsage] = Field(default_factory=list, description="Agents and their workflow counts")


# ==================== WORKFLOW UPDATE ====================


class WorkflowUpdateRequest(BaseModel):
    """Request model for updating a workflow's editable properties.

    All fields are optional - only provided fields will be updated.
    """
    # Existing fields - organization scoping and access control
    organization_id: str | None = Field(
        default=None,
        description="Organization ID to scope the workflow to, or null for global scope"
    )
    access_level: str | None = Field(
        default=None,
        description="Access level: 'authenticated' (any logged-in user) or 'role_based' (specific roles required)"
    )
    clear_roles: bool = Field(
        default=False,
        description="If true, clear all role assignments for this workflow (sets to role_based with no roles)"
    )
    role_ids: list[str] | None = Field(
        default=None,
        description=(
            "Role IDs for role_based access (bulk replaces existing assignments "
            "when provided). Mutually exclusive with clear_roles; if both are "
            "set, role_ids wins."
        ),
    )

    # New fields for UI management
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="MCP tool name for this workflow. Defaults to the Python function name on registration."
    )
    display_name: str | None = Field(
        default=None,
        max_length=200,
        description="Optional UI display name (falls back to the tool name if not set)"
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Workflow description (initially set from code, editable in UI or manifest YAML)"
    )
    category: str | None = Field(
        default=None,
        max_length=100,
        description="Category for organization (initially set from code, editable in UI or manifest YAML)"
    )
    timeout_seconds: int | None = Field(
        default=None,
        ge=0,
        le=86400,
        description="Max execution time in seconds. 0 = no timeout. Default 1800 (30 min), max 86400 (24h)."
    )
    execution_mode: Literal["sync", "async"] | None = Field(
        default=None,
        description="Execution mode: 'sync' for immediate response, 'async' for background execution"
    )

    # Economics - value metrics for reporting
    time_saved: int | None = Field(
        default=None,
        ge=0,
        description="Minutes saved per execution (for ROI reporting)"
    )
    value: float | None = Field(
        default=None,
        ge=0.0,
        description="Flexible value unit per execution (e.g., cost savings, revenue)"
    )

    # Tool configuration (for AI agent tool calling)
    tool_description: str | None = Field(
        default=None,
        max_length=1000,
        description="Description optimized for AI tool selection (used when workflow is exposed as a tool)"
    )

    # Data provider configuration
    cache_ttl_seconds: int | None = Field(
        default=None,
        ge=0,
        le=86400,
        description="Cache TTL in seconds for data providers (0-86400, default 300)"
    )

    # Tags (editable in UI, code-defined tags are initial values)
    tags: list[str] | None = Field(
        default=None,
        description="Tags for categorization and search"
    )

    # HTTP Endpoint configuration
    endpoint_enabled: bool | None = Field(
        default=None,
        description="Whether workflow is exposed as an HTTP endpoint"
    )
    allowed_methods: list[str] | None = Field(
        default=None,
        description="Allowed HTTP methods when endpoint is enabled (e.g., ['GET', 'POST'])"
    )
    public_endpoint: bool | None = Field(
        default=None,
        description="If true, skip authentication for this endpoint (use for webhooks)"
    )
    disable_global_key: bool | None = Field(
        default=None,
        description="If true, only workflow-specific API keys work (global keys rejected)"
    )


# ==================== WORKFLOW ROLE ACCESS CONTROL ====================


class WorkflowRolesResponse(BaseModel):
    """Response model for getting roles assigned to a workflow."""
    role_ids: list[str] = Field(..., description="List of role IDs assigned to the workflow")


class AssignRolesToWorkflowRequest(BaseModel):
    """Request model for assigning roles to a workflow."""
    role_ids: list[str] = Field(..., min_length=1, description="List of role IDs to assign")


# ==================== WORKFLOW DELETE ====================


class DeleteWorkflowRequest(BaseModel):
    """Request body for DELETE /api/workflows/{workflow_id}.

    On first call (without flags), the endpoint performs a deactivation check
    and returns 409 if the workflow has history or dependencies.
    The client then re-calls with force_deactivation=True or replacements.
    """
    force_deactivation: bool = Field(
        default=False,
        description="Skip deactivation protection and allow the workflow to be removed"
    )
    replacements: dict[str, str] | None = Field(
        default=None,
        description="Map of workflow_id -> new_function_name to transfer identity before removal"
    )

    model_config = ConfigDict(from_attributes=True)
