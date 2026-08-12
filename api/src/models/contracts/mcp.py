"""
MCP Configuration Contracts

Pydantic models for MCP configuration API requests and responses.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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


class MCPGatewayAgentSummary(BaseModel):
    """Compact agent metadata returned by gateway discovery."""

    id: str
    name: str
    description: str | None = None


class MCPGatewayFindAgentsResponse(BaseModel):
    """Search results for agents visible to the caller."""

    query: str | None = None
    agents: list[MCPGatewayAgentSummary]
    count: int
    total_matches: int
    has_more: bool


class MCPGatewayAgentDetail(MCPGatewayAgentSummary):
    """Live task instructions for a selected agent."""

    instructions: str | None = None


class MCPGatewayToolSummary(BaseModel):
    """Schema-free tool metadata returned with an agent."""

    tool_ref: str
    name: str
    description: str
    source: str


class MCPGatewayAgentResponse(BaseModel):
    """Selected agent instructions and compact tool catalog."""

    agent: MCPGatewayAgentDetail
    tools: list[MCPGatewayToolSummary]
    tool_count: int


class MCPGatewayToolSchemaResponse(BaseModel):
    """Live schema for one agent-bound tool reference."""

    agent_id: str
    tool_ref: str
    name: str
    description: str
    source: str
    input_schema: dict[str, Any]


class MCPGatewayExecuteRequest(BaseModel):
    """Arguments passed to an agent-bound tool."""

    arguments: dict[str, Any] = Field(default_factory=dict)


class MCPGatewayExecuteResponse(BaseModel):
    """Auditable envelope returned after a gateway tool call."""

    agent_id: str
    agent_name: str
    tool_ref: str
    tool_name: str
    source: str
    duration_ms: int
    result: Any
