"""Maintained native Chat profile backed by the Bifrost gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid5

from src.models.enums import AgentAccessLevel
from src.services.mcp_server.tools.gateway import (
    GATEWAY_INSTRUCTIONS,
    GATEWAY_TOOL_NAMES,
)

_NATIVE_CHAT_PROFILE_ID_KEY = "bifrost-native-chat-gateway-profile"


def native_chat_profile_id() -> UUID:
    return uuid5(NAMESPACE_URL, _NATIVE_CHAT_PROFILE_ID_KEY)


@dataclass(frozen=True, slots=True)
class NativeChatExecutionProfile:
    """Runtime profile for native Chat's dynamic Bifrost discovery surface."""

    id: UUID
    name: str
    description: str
    system_prompt: str
    organization_id: UUID | None
    owner_user_id: UUID | None
    authorization_boundary: str | None
    system_tools: tuple[str, ...]
    gateway_is_platform_admin: bool = False
    resource_gate_bypass: bool = False
    bundle_path: str | None = None
    solution_id: UUID | None = None
    tools: tuple[Any, ...] = ()
    delegated_agents: tuple[Any, ...] = ()
    knowledge_sources: tuple[Any, ...] = ()
    mcp_connections: tuple[Any, ...] = ()
    roles: tuple[Any, ...] = ()
    owner: Any | None = None
    channels: tuple[str, ...] = ("chat",)
    access_level: AgentAccessLevel = AgentAccessLevel.ROLE_BASED
    is_active: bool = True
    llm_model: str | None = None
    max_iterations: int | None = None
    max_token_budget: int | None = None
    llm_max_tokens: int | None = None


def build_native_chat_profile(
    *,
    organization_id: UUID | None,
    user_id: UUID | None,
    authorization_boundary: str | None = None,
    gateway_is_platform_admin: bool = False,
    resource_gate_bypass: bool = False,
    selected_agent_id: UUID | None = None,
    selected_agent_name: str | None = None,
) -> NativeChatExecutionProfile:
    prompt = GATEWAY_INSTRUCTIONS
    if selected_agent_id is not None:
        prompt += (
            "\n\n---\nSELECTED AGENT CONTEXT\n"
            f"The user explicitly selected Agent {selected_agent_name or selected_agent_id} "
            f"({selected_agent_id}). Use bifrost_search_capabilities with this "
            "agent_id to load its live instructions and tool schemas. Do not "
            "assume prior tool access; every tool execution must go through "
            "bifrost_execute_tool with that agent_id and a returned tool_ref."
        )
    return NativeChatExecutionProfile(
        id=native_chat_profile_id(),
        name="Bifrost Chat",
        description="Native Bifrost Chat dynamic capability profile",
        system_prompt=prompt,
        organization_id=organization_id,
        owner_user_id=user_id,
        authorization_boundary=authorization_boundary,
        system_tools=tuple(sorted(GATEWAY_TOOL_NAMES)),
        gateway_is_platform_admin=gateway_is_platform_admin,
        resource_gate_bypass=resource_gate_bypass,
    )


__all__ = [
    "NativeChatExecutionProfile",
    "build_native_chat_profile",
    "native_chat_profile_id",
]
