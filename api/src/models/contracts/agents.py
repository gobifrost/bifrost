"""
Agent and Chat contract models for Bifrost.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from src.models.contracts.refs import WorkflowRef
from src.models.enums import AgentAccessLevel, AgentChannel, MessageRole


# ==================== TOOL CALL MODELS ====================


class ToolCall(BaseModel):
    """Tool call from assistant message."""
    id: str = Field(..., description="Unique identifier for this tool call")
    name: str = Field(..., description="Name of the tool to call")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments to pass to the tool")


class ToolResult(BaseModel):
    """Result from tool execution."""
    tool_call_id: str = Field(..., description="ID of the tool call this responds to")
    tool_name: str = Field(..., description="Name of the tool that was called")
    result: Any = Field(..., description="Result from tool execution")
    error: str | None = Field(default=None, description="Error message if tool failed")
    duration_ms: int | None = Field(default=None, description="Execution duration in milliseconds")
    error_type: str | None = Field(
        default=None,
        description=(
            "Optional structured error class. Used by the chat surface to "
            "render specialized recovery UIs (e.g. 'needs_reauth' shows an "
            "inline reconnect button instead of a plain error message)."
        ),
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional structured payload that travels alongside the error. "
            "For 'needs_reauth' this carries 'reauth_url', 'connection_id', "
            "and 'tool_name' so the chat surface can build the reconnect "
            "button without re-querying."
        ),
    )


# ==================== AGENT MODELS ====================


class AgentCreate(BaseModel):
    """Request model for creating an agent."""
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    system_prompt: str = Field(..., min_length=1, max_length=50000)
    channels: list[AgentChannel] = Field(default_factory=lambda: [AgentChannel.CHAT])
    access_level: AgentAccessLevel = Field(default=AgentAccessLevel.ROLE_BASED)
    organization_id: UUID | None = Field(
        default=None, description="Organization ID (null = global resource)"
    )
    tool_ids: list[str] = Field(default_factory=list, description="List of workflow IDs to use as tools")
    delegated_agent_ids: list[str] = Field(default_factory=list, description="List of agent IDs this agent can delegate to")
    role_ids: list[str] = Field(default_factory=list, description="List of role IDs that can access this agent (for role_based access)")
    knowledge_sources: list[str] = Field(default_factory=list, description="List of knowledge namespaces this agent can search")
    system_tools: list[str] = Field(default_factory=list, description="List of system tool names enabled for this agent")
    mcp_connection_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "MCP connection UUIDs this agent is granted access to. Empty list "
            "(default) means the agent receives no external MCP tools. The "
            "agent's organization must own each listed connection."
        ),
    )
    llm_model: str | None = Field(default=None, description="Override model (null=use global config)")
    llm_max_tokens: int | None = Field(default=None, ge=1, le=200000, description="Override max tokens")
    max_iterations: int | None = Field(default=None, ge=1, le=200, description="Max LLM iterations for autonomous runs")
    max_token_budget: int | None = Field(default=None, ge=1000, le=1000000, description="Max token budget for autonomous runs")


class AgentUpdate(BaseModel):
    """Request model for updating an agent."""
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=50000)
    channels: list[AgentChannel] | None = None
    access_level: AgentAccessLevel | None = None
    organization_id: UUID | None = Field(
        default=None, description="Organization ID (null = global resource)"
    )
    is_active: bool | None = None
    tool_ids: list[str] | None = Field(default=None, description="List of workflow IDs to use as tools")
    delegated_agent_ids: list[str] | None = Field(default=None, description="List of agent IDs this agent can delegate to")
    role_ids: list[str] | None = Field(default=None, description="List of role IDs that can access this agent (for role_based access)")
    knowledge_sources: list[str] | None = Field(default=None, description="List of knowledge namespaces this agent can search")
    system_tools: list[str] | None = Field(default=None, description="List of system tool names enabled for this agent")
    mcp_connection_ids: list[UUID] | None = Field(
        default=None,
        description=(
            "MCP connection UUIDs this agent is granted access to. Replaces "
            "the agent's full grant list when provided; omit to leave grants "
            "unchanged. Pass [] to revoke all grants."
        ),
    )
    clear_roles: bool = Field(default=False, description="If true, clear all role assignments (sets to role_based with no roles)")
    llm_model: str | None = Field(default=None, description="Override model (null=use global config)")
    llm_max_tokens: int | None = Field(default=None, ge=1, le=200000, description="Override max tokens")
    max_iterations: int | None = Field(default=None, ge=1, le=200, description="Max LLM iterations for autonomous runs")
    max_token_budget: int | None = Field(default=None, ge=1000, le=1000000, description="Max token budget for autonomous runs")


class AgentPromoteRequest(BaseModel):
    """Request to promote a private agent to organization scope."""
    access_level: AgentAccessLevel = Field(
        default=AgentAccessLevel.ROLE_BASED,
        description="Target access level (authenticated or role_based)"
    )
    role_ids: list[str] = Field(
        default_factory=list,
        description="Role IDs for role_based access"
    )


class AccessibleTool(BaseModel):
    """A tool the current user can assign to their agents."""
    id: str
    name: str
    description: str | None = None


class AccessibleKnowledgeSource(BaseModel):
    """A knowledge source the current user can assign to their agents."""
    id: str
    name: str
    namespace: str
    description: str | None = None


class AgentPublic(BaseModel):
    """Agent output for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    system_prompt: str
    channels: list[str]
    access_level: AgentAccessLevel | None = None
    organization_id: UUID | None = None
    is_solution_managed: bool = Field(default=False, description="True if managed by a deployed Solution (read-only on platform)")
    solution_id: UUID | None = Field(default=None, description="UUID of the owning Solution install (null if not solution-managed)")
    is_active: bool
    created_by: str | None = None
    owner_user_id: UUID | None = None
    owner_email: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Populated from relationships
    tool_ids: Annotated[list[str], WorkflowRef()] = Field(default_factory=list)
    delegated_agent_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    system_tools: list[str] = Field(default_factory=list)
    mcp_connection_ids: list[str] = Field(
        default_factory=list,
        description="MCP connection UUIDs this agent is granted access to.",
    )
    llm_model: str | None = None
    llm_max_tokens: int | None = None
    max_iterations: int | None = None
    max_token_budget: int | None = None
    logo: str | None = Field(
        default=None,
        description="Inline logo as a data URL, or null when no logo is set.",
    )

    @field_serializer("id")
    def serialize_id(self, v: UUID) -> str:
        return str(v)

    @field_serializer("organization_id", "owner_user_id")
    def serialize_nullable_uuid(self, v: UUID | None) -> str | None:
        return str(v) if v else None

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class AgentSummary(BaseModel):
    """Lightweight agent summary for listings."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    channels: list[str]
    is_active: bool
    access_level: AgentAccessLevel
    organization_id: UUID | None = None
    owner_user_id: UUID | None = None
    created_at: datetime
    llm_model: str | None = None
    dependency_count: int = Field(default=0, description="Number of tool dependencies this agent uses")
    mcp_connection_count: int = Field(
        default=0,
        description="Number of MCP connections explicitly granted to this agent.",
    )
    logo: str | None = Field(
        default=None,
        description="Inline logo as a data URL, or null when no logo is set. Avoids an N+1 GET per card in list views.",
    )
    is_solution_managed: bool = Field(default=False, description="True if managed by a deployed Solution (read-only on platform)")
    solution_id: UUID | None = Field(default=None, description="UUID of the owning Solution install (null if not solution-managed)")

    @model_validator(mode="before")
    @classmethod
    def _derive_solution_managed(cls, data):
        """Derive is_solution_managed from the ORM's solution_id.

        The DTO field has no matching ORM attribute, so set a transient
        attribute on the ORM instance for from_attributes to read. This is a
        non-mapped attribute — SQLAlchemy ignores it for flush.
        """
        if not isinstance(data, dict) and hasattr(data, "solution_id"):
            try:
                data.is_solution_managed = data.solution_id is not None
            except (AttributeError, ValueError):
                pass  # read-only/detached instance — DTO default (False) applies
        return data

    @field_serializer("id")
    def serialize_id(self, v: UUID) -> str:
        return str(v)

    @field_serializer("organization_id", "owner_user_id", "solution_id")
    def serialize_nullable_uuid(self, v: UUID | None) -> str | None:
        return str(v) if v else None

    @field_serializer("created_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


# ==================== CONVERSATION MODELS ====================


class ConversationCreate(BaseModel):
    """Request model for creating a conversation."""
    agent_id: UUID | None = Field(default=None, description="ID of the agent to chat with (optional for agentless chat)")
    workspace_id: UUID | None = Field(
        default=None,
        description="Workspace to file this conversation in. Null = general pool (unscoped chat list).",
    )
    channel: AgentChannel = Field(default=AgentChannel.CHAT)
    title: str | None = Field(default=None, max_length=500)


class ConversationUpdate(BaseModel):
    """Patch model for updating a conversation."""
    title: str | None = Field(
        default=None,
        max_length=500,
        description="Conversation title. Set by inline rename in the sidebar.",
    )
    workspace_id: UUID | None = Field(
        default=None,
        description="New workspace id (or null to move to the general pool).",
    )
    current_model: str | None = Field(
        default=None,
        description=(
            "Model to use for this conversation going forward. Set by the "
            "chat picker. Resolved through the cascade — must be in the "
            "user's allowed set."
        ),
    )
    instructions: str | None = Field(
        default=None,
        description="Per-conversation custom instructions appended to the system prompt.",
    )


class ConversationPublic(BaseModel):
    """Conversation output for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID | None = None
    user_id: UUID
    workspace_id: UUID | None = None
    active_leaf_message_id: UUID | None = None
    instructions: str | None = None
    current_model: str | None = None
    channel: str
    title: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Computed fields (populated by query)
    message_count: int | None = None
    last_message_at: datetime | None = None
    agent_name: str | None = None

    @field_serializer("id", "user_id")
    def serialize_uuid(self, v: UUID) -> str:
        return str(v)

    @field_serializer("agent_id", "workspace_id", "active_leaf_message_id")
    def serialize_optional_uuid(self, v: UUID | None) -> str | None:
        return str(v) if v else None

    @field_serializer("created_at", "updated_at", "last_message_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class ConversationSummary(BaseModel):
    """Lightweight conversation summary for sidebar listings."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID | None = None
    agent_name: str | None = None
    workspace_id: UUID | None = None
    title: str | None = None
    updated_at: datetime
    last_message_preview: str | None = None

    @field_serializer("id")
    def serialize_uuid(self, v: UUID) -> str:
        return str(v)

    @field_serializer("agent_id", "workspace_id")
    def serialize_optional_uuid(self, v: UUID | None) -> str | None:
        return str(v) if v else None

    @field_serializer("updated_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


# ==================== ATTACHMENT MODELS ====================


class AttachmentPublic(BaseModel):
    """A file attached to a chat message."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    has_extracted_text: bool = Field(
        default=False,
        description="True if server-side text was extracted (PDF/CSV/text). Images have none.",
    )

    @field_serializer("id")
    def serialize_id(self, v: UUID) -> str:
        return str(v)

    @model_validator(mode="before")
    @classmethod
    def _derive_has_extracted_text(cls, data):
        """Derive has_extracted_text from the ORM's extracted_text column."""
        if not isinstance(data, dict) and hasattr(data, "extracted_text"):
            try:
                data.has_extracted_text = bool(data.extracted_text)
            except (AttributeError, ValueError):
                pass  # detached instance — DTO default (False) applies
        return data


class AttachmentUploadResponse(BaseModel):
    """Response after uploading attachments to a conversation.

    ``attachments`` is always present (the endpoint returns the records it just
    created), so it is required — keeps the generated client type a plain array
    rather than ``T[] | undefined``.
    """
    attachments: list[AttachmentPublic]


# ==================== ARTIFACT MODELS ====================
#
# Artifacts are the mirror image of attachments: a tool/skill returns an
# artifact contract (file metadata + inline bytes + an optional inert preview),
# the trusted execution layer persists the bytes to S3 under
# ``_artifacts/{conversation_id}/...`` and exposes only metadata. Download URLs
# are minted scoped + expiring at render time by the API — a tool NEVER returns
# a URL (it has no credentials and a baked-in URL bypasses authorization).
# See Part C of the agent-skill-bundles-and-capabilities design.

# Inert preview kinds the browser renders natively + safely. No html/svg/react.
ArtifactPreviewKind = Literal["markdown", "image", "pdf", "csv"]


class ArtifactToolFile(BaseModel):
    """One file in a tool's returned artifact contract (input side).

    The tool provides the bytes inline as base64. The trusted layer strips
    ``content_base64`` before persisting and before any value reaches the model
    or client — it is never echoed back.
    """

    name: str = Field(..., description="File name, e.g. 'report.pdf'")
    content_type: str = Field(..., description="MIME type, e.g. 'application/pdf'")
    content_base64: str = Field(..., description="Base64-encoded file bytes (stripped after persist)")


class ArtifactToolPreview(BaseModel):
    """Optional inline preview in a tool's artifact contract (input side)."""

    kind: ArtifactPreviewKind
    # For image/pdf/csv: the name of the file in files[] to preview.
    content_ref: str | None = Field(
        default=None, description="Name of the files[] entry to preview (image/pdf/csv)"
    )
    # For markdown: the text itself.
    inline: str | None = Field(default=None, description="Inline markdown text (markdown only)")


class ArtifactToolContract(BaseModel):
    """The ``artifact`` object a tool may return alongside its normal result.

    Detected on ``tool_result.result['artifact']`` by the trusted layer.
    """

    title: str | None = None
    preview: ArtifactToolPreview | None = None
    files: list[ArtifactToolFile] = Field(default_factory=list)


class ArtifactFilePublic(BaseModel):
    """Artifact file metadata exposed to the client (no URL, no bytes)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str | None = None

    @field_serializer("id")
    def serialize_id(self, v: UUID) -> str:
        return str(v)


class ArtifactPreviewPublic(BaseModel):
    """The inert preview to render inline (no URL — file fetched via download endpoint)."""

    kind: ArtifactPreviewKind
    # For image/pdf/csv: the artifact file id to fetch via the download endpoint.
    file_id: UUID | None = None
    # For markdown: the inline text.
    inline: str | None = None

    @field_serializer("file_id")
    def serialize_file_id(self, v: UUID | None) -> str | None:
        return str(v) if v is not None else None


class ArtifactInfo(BaseModel):
    """A rendered artifact carried on the ``artifact_generated`` stream chunk and
    persisted per message. Metadata only — download URLs are minted at render
    time by the API, never stored here."""

    title: str | None = None
    preview: ArtifactPreviewPublic | None = None
    files: list[ArtifactFilePublic] = Field(default_factory=list)


class ArtifactDownloadResponse(BaseModel):
    """A scoped, expiring download URL minted at render time for one artifact file."""

    url: str
    expires_in: int = Field(..., description="Seconds until the URL expires")


# ==================== MESSAGE MODELS ====================


class MessagePublic(BaseModel):
    """Message output for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    execution_id: str | None = Field(default=None, description="Execution ID for tool results (for fetching logs)")
    # New fields for TOOL_CALL messages
    tool_state: Literal["running", "completed", "error"] | None = Field(default=None, description="Tool execution state")
    tool_result: Any | None = Field(default=None, description="Result from tool execution")
    tool_input: dict[str, Any] | None = Field(default=None, description="Input arguments for tool call")
    token_count_input: int | None = None
    token_count_output: int | None = None
    model: str | None = None
    cost_tier: str | None = Field(
        default=None,
        description="Symbolic cost tier that handled this turn (fast / balanced / premium).",
    )
    duration_ms: int | None = None
    sequence: int
    created_at: datetime
    parent_message_id: UUID | None = None
    sibling_count: int = 1   # 1 = this message has no siblings
    sibling_index: int = 0   # 0-based index among siblings
    attachments: list[AttachmentPublic] = Field(default_factory=list)
    artifacts: list[ArtifactInfo] = Field(default_factory=list)

    @field_serializer("id", "conversation_id")
    def serialize_uuid(self, v: UUID) -> str:
        return str(v)

    @field_serializer("parent_message_id")
    def serialize_parent_message_id(self, v: UUID | None) -> str | None:
        return str(v) if v else None

    @field_serializer("created_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


# ==================== CHAT REQUEST/RESPONSE MODELS ====================


class ChatRequest(BaseModel):
    """Request for sending a chat message."""
    message: str = Field(..., min_length=1, max_length=100000)
    stream: bool = Field(default=True, description="Whether to stream the response")
    attachment_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "IDs of attachments (previously uploaded to this conversation) to "
            "bind to this user message. Each must belong to this conversation "
            "and not already be bound to another message."
        ),
    )


class ChatResponse(BaseModel):
    """Response from chat completion (non-streaming)."""
    message_id: UUID
    content: str
    tool_calls: list[ToolCall] | None = None
    token_count_input: int | None = None
    token_count_output: int | None = None
    duration_ms: int | None = None

    @field_serializer("message_id")
    def serialize_uuid(self, v: UUID) -> str:
        return str(v)


class EditMessageRequest(BaseModel):
    """Edit a user message — creates a sibling and dispatches a fresh turn."""
    content: str = Field(..., min_length=1, description="New text for the user message.")
    local_id: str | None = Field(default=None, description="Client-generated ID for optimistic update reconciliation.")


class RetryMessageRequest(BaseModel):
    """Retry an assistant message — creates a sibling and dispatches a fresh turn."""
    local_id: str | None = Field(default=None, description="Client-generated ID for optimistic update reconciliation.")


class SwitchBranchRequest(BaseModel):
    """Switch the conversation's active leaf to another message id."""
    message_id: UUID = Field(..., description="Target message id (must belong to this conversation).")


class CompactConversationResponse(BaseModel):
    """Result of a manual "Compact older turns" request (§4.3)."""
    compacted: bool = Field(..., description="Whether anything was compacted")
    turns_compacted: int = Field(default=0, description="Number of earlier turns folded into the summary")
    tokens_before: int = Field(default=0, description="Estimated tokens of the folded span")
    tokens_after: int = Field(default=0, description="Estimated tokens of the resulting summary")
    message: str = Field(default="", description="Human-readable result for toast/feedback")


class AgentSwitch(BaseModel):
    """Agent switch event during chat."""
    agent_id: str = Field(..., description="ID of the agent switched to")
    agent_name: str = Field(..., description="Name of the agent switched to")
    reason: str = Field(default="", description="Reason for the switch (e.g., '@mention', 'routed')")


class DelegationInfo(BaseModel):
    """Multi-agent delegation event during a single chat turn (M6).

    Carried by ``delegation_started`` (response unset) and
    ``delegation_complete`` (response/error populated) chunks. The primary
    agent calls a ``delegate_to_<agent>`` tool mid-turn; the delegated agent
    runs and its result returns as a tool result the primary continues from.
    The UI renders a "✓ consulted <agent>" badge with the exchange in an
    expandable detail.
    """
    tool_call_id: str = Field(..., description="ID of the delegate_to_* tool call")
    agent_id: str | None = Field(default=None, description="Delegated agent UUID")
    agent_name: str = Field(..., description="Delegated agent display name")
    task: str = Field(default="", description="Task/question delegated to the agent")
    response: str | None = Field(
        default=None,
        description="Delegated agent's response (delegation_complete only)",
    )
    error: str | None = Field(
        default=None,
        description="Error if the delegation failed (delegation_complete only)",
    )
    duration_ms: int | None = Field(
        default=None, description="Delegation duration (delegation_complete only)"
    )


class ContextWarning(BaseModel):
    """Context window warning/compaction event.

    Carried by both ``context_warning`` chunks (compaction approaching) and
    ``compaction_complete`` chunks (compaction ran). Per the M5 spec the
    ``context_warning`` semantics shifted from "messages will be deleted" to
    "compaction is approaching/imminent."
    """
    current_tokens: int = Field(..., description="Estimated current token count")
    max_tokens: int = Field(..., description="Per-model compaction threshold (0.85 * context window)")
    action: str = Field(..., description="'warning' or 'compacted'")
    message: str = Field(..., description="Human-readable explanation")
    turns_compacted: int = Field(
        default=0, description="Number of earlier turns folded into the summary (compaction_complete only)"
    )


class ToolProgressLog(BaseModel):
    """Log entry for tool execution progress."""
    level: str = Field(..., description="Log level: debug, info, warning, error")
    message: str = Field(..., description="Log message")


class ToolProgress(BaseModel):
    """Tool execution progress update."""
    tool_call_id: str = Field(..., description="ID of the tool call")
    execution_id: str | None = Field(default=None, description="Execution ID for tracking")
    status: str | None = Field(default=None, description="Status: pending, running, success, failed, timeout")
    log: ToolProgressLog | None = Field(default=None, description="Log entry if this is a log update")


class ChatStreamChunk(BaseModel):
    """
    Unified streaming chat response chunk.

    This is the single source of truth for streaming chunk format.
    """

    type: Literal[
        # Regular agent types
        "message_start",
        "delta",
        "assistant_message_end",
        "tool_call",
        "tool_progress",
        "tool_result",
        "agent_switch",
        "context_warning",
        # M5 compaction (§10): compaction_started fires before the summarizer
        # runs; compaction_complete carries the ContextWarning result. The
        # context_warning chunk now means "compaction approaching."
        "compaction_started",
        "compaction_complete",
        # M6 multi-agent delegation (§13): delegation_started fires when the
        # primary agent calls a delegate_to_* tool mid-turn; delegation_complete
        # carries the delegated agent's response so the UI can render the
        # "✓ consulted <agent>" badge with an expandable detail.
        "delegation_started",
        "delegation_complete",
        # Artifacts (Part C): a tool returned an artifact contract; the trusted
        # layer persisted its files and emits this chunk so the UI can render the
        # inert preview + open the artifact panel. Carries metadata only — the
        # client fetches download URLs from the artifact download endpoint.
        "artifact_generated",
        "title_update",
        "done",
        "error",
    ]

    # Text content (for delta)
    content: str | None = None

    # Tool-related fields
    tool_call: ToolCall | None = None
    tool_progress: ToolProgress | None = None
    tool_result: ToolResult | None = None
    execution_id: str | None = Field(default=None, description="Execution ID for tool_call chunks")

    # Agent switch and context warning
    agent_switch: AgentSwitch | None = None
    context_warning: ContextWarning | None = None

    # M6 multi-agent delegation (delegation_started / delegation_complete)
    delegation: DelegationInfo | None = None

    # Artifacts (artifact_generated): the rendered artifact (metadata + preview)
    artifact: ArtifactInfo | None = None

    # Message IDs
    message_id: str | None = None
    user_message_id: str | None = Field(default=None, description="Real UUID of user message (sent in message_start)")
    assistant_message_id: str | None = Field(default=None, description="Real UUID of assistant message (sent in message_start)")
    local_id: str | None = Field(default=None, description="Client-generated ID echoed back for optimistic update reconciliation")

    # Conversation ID (for routing chunks to correct conversation)
    conversation_id: str | None = None

    # Usage metrics (for done)
    token_count_input: int | None = None
    token_count_output: int | None = None
    duration_ms: int | None = None

    # Error info
    error: str | None = None

    # Title update (for title_update type)
    title: str | None = None

    # Message boundary fields (for assistant_message_end)
    stop_reason: str | None = Field(default=None, description="Why message ended: 'tool_use' or 'end_turn'")


# ==================== ROLE ASSIGNMENT MODELS ====================


class RoleAgentsResponse(BaseModel):
    """Response for getting agents assigned to a role."""
    agent_ids: list[str] = Field(default_factory=list)


class AssignAgentsToRoleRequest(BaseModel):
    """Request for assigning agents to a role."""
    agent_ids: list[str] = Field(..., min_length=1)


# ==================== UNIFIED TOOLS ====================


class ToolInfo(BaseModel):
    """
    Unified tool information for both system and workflow tools.

    Used by the /api/tools endpoint to provide a single view of all available tools.
    """
    id: str = Field(..., description="Tool ID (UUID for workflows, name for system tools)")
    name: str = Field(..., description="Display name")
    description: str = Field(..., description="What the tool does")
    type: str = Field(..., description="Tool type: 'system' or 'workflow'")
    category: str | None = Field(default=None, description="Category for grouping (workflows only)")
    default_enabled_for_coding_agent: bool = Field(
        default=False,
        description="Whether this tool is enabled by default for coding agents"
    )
    is_active: bool = Field(
        default=True,
        description="Whether the workflow tool is active (always true for system tools)"
    )
    organization_id: str | None = Field(
        default=None,
        description="Owning organization UUID (workflow tools only; null = global tool or system tool)"
    )
    organization_name: str | None = Field(
        default=None,
        description="Owning organization display name (workflow tools only; null = global tool or system tool)"
    )


class ToolsResponse(BaseModel):
    """Response model for listing available tools."""
    tools: list[ToolInfo] = Field(default_factory=list)
