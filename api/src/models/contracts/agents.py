"""
Agent and Chat contract models for Bifrost.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

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

    @field_serializer("id")
    def serialize_id(self, v: UUID) -> str:
        return str(v)

    @field_serializer("organization_id", "owner_user_id")
    def serialize_nullable_uuid(self, v: UUID | None) -> str | None:
        return str(v) if v else None

    @field_serializer("created_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


# ==================== CONVERSATION MODELS ====================


class ConversationCreate(BaseModel):
    """Request model for creating a conversation."""
    agent_id: UUID | None = Field(default=None, description="ID of the agent to chat with (optional for agentless chat)")
    channel: AgentChannel = Field(default=AgentChannel.CHAT)
    title: str | None = Field(default=None, max_length=500)


class ConversationPublic(BaseModel):
    """Conversation output for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID | None = None
    user_id: UUID
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

    @field_serializer("agent_id")
    def serialize_agent_id(self, v: UUID | None) -> str | None:
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
    title: str | None = None
    updated_at: datetime
    last_message_preview: str | None = None

    @field_serializer("id")
    def serialize_uuid(self, v: UUID) -> str:
        return str(v)

    @field_serializer("agent_id")
    def serialize_agent_id(self, v: UUID | None) -> str | None:
        return str(v) if v else None

    @field_serializer("updated_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


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
    duration_ms: int | None = None
    sequence: int
    created_at: datetime

    @field_serializer("id", "conversation_id")
    def serialize_uuid(self, v: UUID) -> str:
        return str(v)

    @field_serializer("created_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


# ==================== CHAT REQUEST/RESPONSE MODELS ====================


class ChatRequest(BaseModel):
    """Request for sending a chat message."""
    message: str = Field(..., min_length=1, max_length=100000)
    stream: bool = Field(default=True, description="Whether to stream the response")


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


class AgentSwitch(BaseModel):
    """Agent switch event during chat."""
    agent_id: str = Field(..., description="ID of the agent switched to")
    agent_name: str = Field(..., description="Name of the agent switched to")
    reason: str = Field(default="", description="Reason for the switch (e.g., '@mention', 'routed')")


class ContextWarning(BaseModel):
    """Context window warning/compaction event."""
    current_tokens: int = Field(..., description="Estimated current token count")
    max_tokens: int = Field(..., description="Configured threshold")
    action: str = Field(..., description="'warning' or 'compacted'")
    message: str = Field(..., description="Human-readable explanation")


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
