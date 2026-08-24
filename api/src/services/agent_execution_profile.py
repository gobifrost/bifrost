"""Typed configuration consumed by the shared Pydantic AI execution harness."""

from __future__ import annotations

from typing import Any, Protocol, TypeAlias
from uuid import UUID

from src.models.orm.agents import Agent


class MaintainedAgentExecutionProfile(Protocol):
    """Runtime-facing subset shared by stored Agents and maintained profiles.

    SQLAlchemy ``Agent`` rows satisfy this contract structurally. Maintained
    product experiences can therefore reuse the execution harness without
    manufacturing a user-visible Agent row.
    """

    @property
    def id(self) -> UUID: ...

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str | None: ...

    @property
    def system_prompt(self) -> str: ...

    @property
    def bundle_path(self) -> str | None: ...

    @property
    def organization_id(self) -> UUID | None: ...

    @property
    def solution_id(self) -> UUID | None: ...

    @property
    def owner_user_id(self) -> UUID | None: ...

    @property
    def system_tools(self) -> Any: ...

    @property
    def tools(self) -> Any: ...

    @property
    def delegated_agents(self) -> Any: ...

    @property
    def knowledge_sources(self) -> Any: ...

    @property
    def mcp_connections(self) -> Any: ...

    @property
    def roles(self) -> Any: ...

    @property
    def owner(self) -> Any: ...

    @property
    def llm_profile_id(self) -> UUID | None: ...

    @property
    def max_iterations(self) -> int | None: ...

    @property
    def max_token_budget(self) -> int | None: ...

    @property
    def llm_max_tokens(self) -> int | None: ...


AgentExecutionProfile: TypeAlias = Agent | MaintainedAgentExecutionProfile

__all__ = ["AgentExecutionProfile", "MaintainedAgentExecutionProfile"]
