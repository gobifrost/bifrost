"""
MCP Configuration Contracts

Pydantic models for MCP configuration API requests and responses.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPConfigResponse(BaseModel):
    """Response model for MCP configuration (no sensitive data)."""

    enabled: bool = Field(
        description="Whether external MCP access is enabled"
    )
    allowed_tool_ids: list[str] | None = Field(
        default=None,
        description="List of allowed tool IDs (None = all tools allowed)"
    )
    blocked_tool_ids: list[str] = Field(
        default_factory=list,
        description="List of blocked tool IDs"
    )
    is_configured: bool = Field(
        description="Whether MCP has been explicitly configured"
    )
    configured_at: datetime | None = Field(
        default=None,
        description="When the configuration was last updated"
    )
    configured_by: str | None = Field(
        default=None,
        description="Email of user who last configured"
    )


class MCPConfigRequest(BaseModel):
    """Request model for updating MCP configuration."""

    enabled: bool = Field(
        default=True,
        description="Whether external MCP access is enabled"
    )
    allowed_tool_ids: list[str] | None = Field(
        default=None,
        description="List of allowed tool IDs (None = all tools allowed)"
    )
    blocked_tool_ids: list[str] = Field(
        default_factory=list,
        description="List of blocked tool IDs"
    )


class MCPToolInfo(BaseModel):
    """Information about an available MCP tool."""

    id: str = Field(description="Tool identifier")
    name: str = Field(description="Tool display name")
    description: str = Field(description="Tool description")
    is_system: bool = Field(
        default=True,
        description="Whether this is a built-in system tool"
    )


class MCPToolsResponse(BaseModel):
    """Response model for listing MCP tools."""

    tools: list[MCPToolInfo] = Field(description="List of available MCP tools")


class MCPRunInfoResponse(BaseModel):
    """Connection information for installing the Bifrost Agent plugin."""

    enabled: bool = Field(description="Whether external MCP access is enabled")
    mcp_url: str = Field(description="Public streamable-http MCP endpoint")
    setup_prompt: str = Field(
        description="Prompt for creating a reusable Bifrost skill or agent"
    )


class MCPGatewayToolSummary(BaseModel):
    """A matching agent-bound tool, optionally hydrated with its schema."""

    tool_ref: str
    name: str
    description: str
    source: str
    supports_async: bool
    default_async: bool
    input_schema: dict[str, Any] | None = None
    schema_included: bool = False


class MCPGatewayCapabilityAgent(BaseModel):
    """One agent and the bounded subset of tools relevant to the search."""

    id: str
    name: str
    description: str | None = None
    instructions: str | None = None
    instructions_included: bool = False
    matching_tools: list[MCPGatewayToolSummary]
    total_tools: int
    returned_tools: int
    complete: bool
    total_matching_tools: int
    has_more_matches: bool
    search_again: str | None = None


class MCPGatewayCapabilitySearchRequest(BaseModel):
    """Progressively search or hydrate the live agent capability catalog."""

    query: str | None = Field(default=None, max_length=500)
    agent_id: str | None = None
    tool_ref: str | None = None
    limit: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def validate_scope(self) -> "MCPGatewayCapabilitySearchRequest":
        if self.tool_ref and not self.agent_id:
            raise ValueError("tool_ref requires agent_id")
        if not self.agent_id and not (self.query and self.query.strip()):
            raise ValueError("query is required unless agent_id is provided")
        return self


class MCPGatewayCapabilitySearchResponse(BaseModel):
    """Bounded search results with explicit disclosure completeness."""

    query: str | None = None
    agent_id: str | None = None
    tool_ref: str | None = None
    agents: list[MCPGatewayCapabilityAgent]
    returned_matches: int
    total_matches: int
    has_more_matches: bool
    response_complete: bool
    guidance: str


class MCPGatewayExecuteRequest(BaseModel):
    """Arguments passed to an agent-bound tool."""

    model_config = ConfigDict(populate_by_name=True)

    arguments: dict[str, Any] = Field(default_factory=dict)
    async_: bool | None = Field(
        default=None,
        alias="async",
        description=(
            "Override the capability's default execution mode. When omitted, "
            "delegations run asynchronously and other tools run synchronously."
        ),
    )


class MCPGatewayExecuteResponse(BaseModel):
    """Internal REST envelope for an auditable gateway tool call.

    Synchronous public MCP calls return ``result`` directly. Async calls return
    this compact receipt so the caller can poll ``bifrost_get_execution``.
    """

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str
    agent_name: str
    tool_ref: str
    tool_name: str
    source: str
    duration_ms: int
    async_: bool = Field(default=False, alias="async")
    execution_id: str | None = None
    execution_type: Literal["workflow", "agent_run"] | None = None
    status: str | None = None
    result: Any = None


class MCPGatewayExecutionResponse(BaseModel):
    """Compact, ownership-checked execution status and paged result."""

    execution_id: str
    execution_type: Literal["workflow", "agent_run"]
    workflow_id: str | None = None
    workflow_name: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    status: str
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    result_available: bool
    result: Any = None
    result_page: dict[str, Any] | None = None
