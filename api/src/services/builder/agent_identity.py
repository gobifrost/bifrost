"""Canonical Builder coding profile shared by native and external harnesses."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid5

from src.models.enums import AgentAccessLevel
from src.models.orm.solutions import Solution
from src.services.authorization import AuthorizationContext
from src.services.builder.authorization_targets import (
    global_builder_tool_names,
    organization_builder_tool_names,
)
from src.services.builder.scaffold import (
    BUILDER_AGENT_MAX_ITERATIONS,
    BUILDER_AGENT_MAX_TOKEN_BUDGET,
    BUILDER_AGENT_SYSTEM_TOOLS,
    BUILDER_SKILL_BUNDLE_PATH,
    _builder_skill_source,
)

_BUILDER_RUNTIME_PROFILE_ID_KEY = "bifrost-private-solution-builder-runtime-profile"
_GLOBAL_WORKSPACE_PROMPT = """

---
TARGET: GLOBAL WORKSPACE
You are the Bifrost Global Workspace coding agent.

Work only through the bounded workspace tools and the Global operation
changeset tools. The source workspace is an immutable proposal copied from the
live global _repo. Never modify, add, or delete files under .bifrost; those
manifests are read-only evidence. For global loose-resource changes such as
Agents, stage operation changes for review instead of calling live write tools.
Do not claim that a change is live: a platform administrator must inspect the
diff, validate it, and explicitly apply it.
"""
_ORGANIZATION_WORKSPACE_PROMPT = """

---
TARGET: ORGANIZATION WORKSPACE
Use only the platform operations exposed for this session to create and
maintain loose resources in the selected organization. The organization
boundary is fixed by the server. This target has no writable source workspace
and does not create a Solution revision or preview deployment. Report the
concrete resources you created or changed.
"""

BOUNDARY_CONTROL_PARAMETER_NAMES = frozenset(
    {
        "authorization_boundary",
        "organization",
        "organization_id",
        "organization_ref",
        "org_id",
        "scope",
        "target_organization_id",
    }
)


def builder_runtime_profile_id() -> UUID:
    return uuid5(NAMESPACE_URL, _BUILDER_RUNTIME_PROFILE_ID_KEY)


@dataclass(frozen=True, slots=True)
class BuilderRuntimeProfile:
    """Transient configuration for the shared Pydantic AI harness.

    This is not persisted. It only carries the maintained Builder coding
    profile through the shared Pydantic AI harness.
    """

    id: UUID
    name: str
    description: str
    target_kind: str
    system_prompt: str
    bundle_path: str | None
    skill_asset_root: Path | None
    organization_id: UUID | None
    solution_id: UUID
    owner_user_id: UUID | None
    system_tools: tuple[str, ...]
    authorization_boundary: str
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
    max_iterations: int = BUILDER_AGENT_MAX_ITERATIONS
    max_token_budget: int = BUILDER_AGENT_MAX_TOKEN_BUDGET
    llm_max_tokens: int | None = None


def _organization_system_tools(
    authorization: AuthorizationContext,
) -> tuple[str, ...]:
    tools = [
        name
        for name, _action_scopes in organization_builder_tool_names(
            authorization.effective_capabilities
        )
    ]
    return tuple(sorted(dict.fromkeys(tools)))


def _global_system_tools(
    authorization: AuthorizationContext,
) -> tuple[str, ...]:
    read_only_domain_tools = [
        name
        for name, _action_scopes in global_builder_tool_names(
            authorization.effective_capabilities
        )
    ]
    from src.services.mcp_server.tools.builder_global_operations import (
        BUILDER_GLOBAL_OPERATION_TOOL_IDS,
    )

    workspace_tools = [
        tool
        for tool in BUILDER_AGENT_SYSTEM_TOOLS
        if tool not in {"validate_solution", "test_solution_build"}
    ]
    return tuple(
        sorted(
            dict.fromkeys(
                [
                    *workspace_tools,
                    *read_only_domain_tools,
                    *BUILDER_GLOBAL_OPERATION_TOOL_IDS,
                ]
            )
        )
    )


def sanitize_builder_tool_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Hide model-supplied boundary selectors from Builder tool schemas."""

    sanitized = dict(parameters)
    properties = dict(sanitized.get("properties") or {})
    for name in BOUNDARY_CONTROL_PARAMETER_NAMES:
        properties.pop(name, None)
    sanitized["properties"] = properties
    required = sanitized.get("required")
    if isinstance(required, list):
        sanitized["required"] = [
            name for name in required if name not in BOUNDARY_CONTROL_PARAMETER_NAMES
        ]
    return sanitized


def bind_builder_tool_arguments(
    arguments: dict[str, Any],
    *,
    parameters: dict[str, Any],
    target_kind: str | None,
    organization_id: UUID | None,
    authorization_boundary: str | None,
) -> dict[str, Any]:
    """Bind tool arguments to the selected direct Builder boundary."""

    if target_kind not in {"global_repo", "organization"}:
        return dict(arguments)

    properties = parameters.get("properties")
    parameter_names = set(properties) if isinstance(properties, dict) else set()
    bound = {
        name: value
        for name, value in dict(arguments).items()
        if name not in BOUNDARY_CONTROL_PARAMETER_NAMES
    }
    if target_kind == "organization":
        if organization_id is None:
            raise ValueError("organization Builder tool call has no organization boundary")
        organization_value = str(organization_id)
        for name in (
            "organization",
            "organization_id",
            "organization_ref",
            "org_id",
            "target_organization_id",
        ):
            if name in parameter_names:
                bound[name] = organization_value
        if "scope" in parameter_names:
            bound["scope"] = organization_value
    else:
        if "scope" in parameter_names:
            bound["scope"] = "global"
        for name in (
            "organization",
            "organization_id",
            "organization_ref",
            "org_id",
            "target_organization_id",
        ):
            if name in parameter_names:
                bound[name] = None

    if "authorization_boundary" in parameter_names and authorization_boundary:
        bound["authorization_boundary"] = authorization_boundary
    return bound


def build_builder_runtime_profile(
    solution: Solution,
    *,
    target_kind: str = "solution",
    authorization: AuthorizationContext | None = None,
) -> BuilderRuntimeProfile:
    """Return the transient coding profile for one Builder run."""

    skill_source = _builder_skill_source()
    skill_instructions = (skill_source / "SKILL.md").read_text(encoding="utf-8")
    is_global_workspace = target_kind == "global_repo"
    is_organization_workspace = target_kind == "organization"
    if (is_global_workspace or is_organization_workspace) and authorization is None:
        raise ValueError("direct Builder profiles require authorization")
    system_tools = (
        _global_system_tools(authorization)
        if is_global_workspace
        else _organization_system_tools(authorization)
        if is_organization_workspace
        else tuple(BUILDER_AGENT_SYSTEM_TOOLS)
    )
    return BuilderRuntimeProfile(
        id=builder_runtime_profile_id(),
        name=(
            "Global Workspace Builder"
            if is_global_workspace
            else f"{solution.name} Organization Builder"
            if is_organization_workspace
            else f"{solution.name} Builder"
        ),
        description=(
            "Administrator global workspace proposal agent"
            if is_global_workspace
            else "Organization workspace builder"
            if is_organization_workspace
            else "Private Solution authoring agent"
        ),
        target_kind=target_kind,
        system_prompt=(
            skill_instructions + _GLOBAL_WORKSPACE_PROMPT
            if is_global_workspace
            else skill_instructions + _ORGANIZATION_WORKSPACE_PROMPT
            if is_organization_workspace
            else skill_instructions
        ),
        bundle_path=BUILDER_SKILL_BUNDLE_PATH,
        skill_asset_root=skill_source,
        organization_id=solution.organization_id,
        solution_id=solution.id,
        owner_user_id=solution.owner_user_id,
        system_tools=system_tools,
        authorization_boundary=(
            "platform"
            if is_global_workspace
            else f"organization:{solution.organization_id}"
            if solution.organization_id is not None
            else "platform"
        ),
    )


__all__ = [
    "BuilderRuntimeProfile",
    "bind_builder_tool_arguments",
    "sanitize_builder_tool_parameters",
    "build_builder_runtime_profile",
    "builder_runtime_profile_id",
]
