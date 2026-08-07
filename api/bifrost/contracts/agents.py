"""Minimal CLI-side mirror of agent create/update DTOs."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from bifrost.contracts.enums import AgentAccessLevel, AgentChannel


class AgentCreate(BaseModel):
    """Request model for creating an agent (CLI mirror)."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    system_prompt: str = Field(min_length=1, max_length=50000)
    channels: list[AgentChannel] = Field(default_factory=lambda: [AgentChannel.CHAT])
    access_level: AgentAccessLevel = Field(default=AgentAccessLevel.ROLE_BASED)
    organization_id: UUID | None = Field(default=None)
    tool_ids: list[str] = Field(default_factory=list)
    delegated_agent_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    system_tools: list[str] = Field(default_factory=list)
    mcp_connection_ids: list[UUID] = Field(default_factory=list)
    llm_model: str | None = Field(default=None)
    llm_max_tokens: int | None = Field(default=None, ge=1, le=200000)
    max_iterations: int | None = Field(default=None, ge=1, le=200)
    max_token_budget: int | None = Field(default=None, ge=1000, le=1000000)


class AgentUpdate(BaseModel):
    """Request model for updating an agent (CLI mirror)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=50000)
    channels: list[AgentChannel] | None = None
    access_level: AgentAccessLevel | None = None
    organization_id: UUID | None = Field(default=None)
    is_active: bool | None = None
    tool_ids: list[str] | None = Field(default=None)
    delegated_agent_ids: list[str] | None = Field(default=None)
    role_ids: list[str] | None = Field(default=None)
    knowledge_sources: list[str] | None = Field(default=None)
    system_tools: list[str] | None = Field(default=None)
    mcp_connection_ids: list[UUID] | None = Field(default=None)
    clear_roles: bool = Field(default=False)
    llm_model: str | None = Field(default=None)
    llm_max_tokens: int | None = Field(default=None, ge=1, le=200000)
    max_iterations: int | None = Field(default=None, ge=1, le=200)
    max_token_budget: int | None = Field(default=None, ge=1000, le=1000000)
