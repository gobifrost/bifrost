"""Agent MCP tools backed by the canonical REST API.

The five Agent CRUD tools are deliberately thin adapters. They resolve human
references and assemble the shared Agent DTO, then cross the same FastAPI
authorization, validation, side-effect, and audit boundary used by the CLI and
web client. No Agent ORM or repository logic belongs in this module.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client


def _ref_error_payload(exc: Exception) -> dict[str, Any]:
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    if isinstance(exc, AmbiguousRefError):
        return {"kind": exc.kind, "value": exc.value, "candidates": exc.candidates}
    if isinstance(exc, RefNotFoundError):
        return {"kind": exc.kind, "value": exc.value}
    return {"detail": str(exc)}


def _rest_error(action: str, status_code: int, body: Any) -> ToolResult:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
    else:
        message = detail
    return error_result(
        str(message) if message else f"{action} failed: HTTP {status_code}",
        {"status_code": status_code, "body": body},
    )


async def _resolve_ref(context: Any, kind: str, value: str) -> str:
    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        return await RefResolver(http).resolve(kind, value)  # type: ignore[arg-type]


async def _assemble_agent_body(
    context: Any,
    fields: dict[str, Any],
    *,
    is_update: bool,
    scope: str | None,
) -> dict[str, Any]:
    """Assemble a shared DTO payload and resolve every supported human ref."""

    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.agents import AgentCreate, AgentUpdate

    model_cls = AgentUpdate if is_update else AgentCreate
    async with rest_client(context) as http:
        resolver = RefResolver(http)
        body = await assemble_body(model_cls, fields, resolver=resolver)
        for field_name, kind in (
            ("tool_ids", "workflow"),
            ("delegated_agent_ids", "agent"),
        ):
            values = body.get(field_name)
            if isinstance(values, list):
                body[field_name] = [
                    await resolver.resolve(kind, str(value))  # type: ignore[arg-type]
                    for value in values
                ]

        if scope is not None:
            if scope == "global":
                body["organization_id"] = None
            else:
                body["organization_id"] = await resolver.resolve("org", scope)
    return body


async def bifrost_list_agents(
    context: Any,
    scope: str | None = None,
    active_only: bool = True,
    include_stats: bool = False,
) -> ToolResult:
    """List Agents visible to the caller through ``GET /api/agents``."""

    params: dict[str, Any] = {
        "active_only": active_only,
        "include_stats": include_stats,
    }
    if scope is not None:
        params["scope"] = scope
    status_code, body = await call_rest(
        context,
        "GET",
        "/api/agents",
        params=params,
    )
    if status_code != 200:
        return _rest_error("List Agents", status_code, body)
    agents = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(agents)} Agent(s)",
        {"agents": agents, "count": len(agents)},
    )


async def bifrost_get_agent(context: Any, agent_ref: str) -> ToolResult:
    """Get one Agent by UUID or accessible name, including its Skill.

    The payload carries a ``skill`` block so a harness can hydrate the Agent in
    one call: canonical instructions, the companion-file inventory, and the
    content ``revision`` it can cache against. Companion files are read
    individually with ``bifrost_read_agent_skill_file``; no storage path is
    exposed.

    A Skill that cannot be projected (an unreadable or malformed bundle) does
    not fail the whole read: the Agent is still returned, with ``skill`` set to
    an error marker, so callers can distinguish "no Skill" from "Agent absent".
    """

    if not agent_ref:
        return error_result("agent_ref is required")
    try:
        agent_id = await _resolve_ref(context, "agent", agent_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Agent {agent_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/agents/{agent_id}",
    )
    if status_code != 200:
        return _rest_error("Get Agent", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}

    skill_status, skill_body = await call_rest(
        context,
        "GET",
        f"/api/agents/{agent_id}/skill",
    )
    if skill_status == 200 and isinstance(skill_body, dict):
        payload["skill"] = {
            "name": skill_body.get("name"),
            "description": skill_body.get("description"),
            "revision": skill_body.get("revision"),
            "source": skill_body.get("source"),
            "is_managed": skill_body.get("is_managed"),
            "instructions": skill_body.get("skill_markdown"),
            "files": skill_body.get("files", []),
            "companion_files": skill_body.get("companion_files", []),
            "read_file_tool": "bifrost_read_agent_skill_file",
        }
    else:
        payload["skill"] = {
            "error": f"skill projection unavailable: HTTP {skill_status}",
        }
    return success_result(f"Agent: {payload.get('name', agent_id)}", payload)


async def bifrost_create_agent(
    context: Any,
    name: str,
    system_prompt: str,
    description: str | None = None,
    channels: list[str] | None = None,
    access_level: str | None = None,
    tool_ids: list[str] | None = None,
    delegated_agent_ids: list[str] | None = None,
    role_ids: list[str] | None = None,
    knowledge_sources: list[str] | None = None,
    system_tools: list[str] | None = None,
    mcp_connection_ids: list[str] | None = None,
    llm_model: str | None = None,
    llm_max_tokens: int | None = None,
    max_iterations: int | None = None,
    max_token_budget: int | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Create an Agent through ``POST /api/agents``.

    Workflow, delegated-Agent, role, and organization values accept UUIDs or
    human refs. MCP connection values are UUIDs. ``scope`` is ``global``, an
    organization UUID/name, or omitted for the caller's home organization.
    """

    fields = {
        "name": name,
        "system_prompt": system_prompt,
        "description": description,
        "channels": channels,
        "access_level": access_level,
        "tool_ids": tool_ids,
        "delegated_agent_ids": delegated_agent_ids,
        "role_ids": role_ids,
        "knowledge_sources": knowledge_sources,
        "system_tools": system_tools,
        "mcp_connection_ids": mcp_connection_ids,
        "llm_model": llm_model,
        "llm_max_tokens": llm_max_tokens,
        "max_iterations": max_iterations,
        "max_token_budget": max_token_budget,
    }
    try:
        body = await _assemble_agent_body(
            context,
            fields,
            is_update=False,
            scope=scope,
        )
    except Exception as exc:
        return error_result(f"Invalid Agent input: {exc}", _ref_error_payload(exc))
    status_code, response = await call_rest(
        context,
        "POST",
        "/api/agents",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Agent", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(f"Created Agent: {payload.get('name', name)}", payload)


async def bifrost_update_agent(
    context: Any,
    agent_ref: str,
    name: str | None = None,
    description: str | None = None,
    system_prompt: str | None = None,
    channels: list[str] | None = None,
    access_level: str | None = None,
    is_active: bool | None = None,
    tool_ids: list[str] | None = None,
    delegated_agent_ids: list[str] | None = None,
    role_ids: list[str] | None = None,
    knowledge_sources: list[str] | None = None,
    system_tools: list[str] | None = None,
    mcp_connection_ids: list[str] | None = None,
    clear_roles: bool | None = None,
    llm_model: str | None = None,
    llm_max_tokens: int | None = None,
    max_iterations: int | None = None,
    max_token_budget: int | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Update an Agent through ``PUT /api/agents/{id}``."""

    if not agent_ref:
        return error_result("agent_ref is required")
    try:
        agent_id = await _resolve_ref(context, "agent", agent_ref)
        body = await _assemble_agent_body(
            context,
            {
                "name": name,
                "description": description,
                "system_prompt": system_prompt,
                "channels": channels,
                "access_level": access_level,
                "is_active": is_active,
                "tool_ids": tool_ids,
                "delegated_agent_ids": delegated_agent_ids,
                "role_ids": role_ids,
                "knowledge_sources": knowledge_sources,
                "system_tools": system_tools,
                "mcp_connection_ids": mcp_connection_ids,
                "clear_roles": clear_roles,
                "llm_model": llm_model,
                "llm_max_tokens": llm_max_tokens,
                "max_iterations": max_iterations,
                "max_token_budget": max_token_budget,
            },
            is_update=True,
            scope=scope,
        )
    except Exception as exc:
        return error_result(f"Invalid Agent input: {exc}", _ref_error_payload(exc))
    if not body:
        return error_result("No updates provided")
    status_code, response = await call_rest(
        context,
        "PUT",
        f"/api/agents/{agent_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Agent", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(f"Updated Agent: {payload.get('name', agent_id)}", payload)


async def bifrost_delete_agent(context: Any, agent_ref: str) -> ToolResult:
    """Delete an Agent by UUID or accessible name through the REST API."""

    if not agent_ref:
        return error_result("agent_ref is required")
    try:
        agent_id = await _resolve_ref(context, "agent", agent_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Agent {agent_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "DELETE",
        f"/api/agents/{agent_id}",
    )
    if status_code != 204:
        return _rest_error("Delete Agent", status_code, body)
    return success_result(f"Deleted Agent {agent_id}", {"deleted": agent_id})


TOOLS = [
    (
        "bifrost_list_agents",
        "List Agents",
        "List AI Agents visible to the caller.",
    ),
    (
        "bifrost_get_agent",
        "Get Agent",
        "Get an Agent by UUID or accessible name.",
    ),
    (
        "bifrost_create_agent",
        "Create Agent",
        "Create an Agent through the canonical platform API.",
    ),
    (
        "bifrost_update_agent",
        "Update Agent",
        "Update an Agent through the canonical platform API.",
    ),
    (
        "bifrost_delete_agent",
        "Delete Agent",
        "Permanently delete an Agent through the canonical platform API.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register Agent tools with FastMCP."""

    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_agents": bifrost_list_agents,
        "bifrost_get_agent": bifrost_get_agent,
        "bifrost_create_agent": bifrost_create_agent,
        "bifrost_update_agent": bifrost_update_agent,
        "bifrost_delete_agent": bifrost_delete_agent,
    }
    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp,
            tool_funcs[tool_id],
            tool_id,
            description,
            get_context_fn,
        )


__all__ = [
    "TOOLS",
    "bifrost_create_agent",
    "bifrost_delete_agent",
    "bifrost_get_agent",
    "bifrost_list_agents",
    "bifrost_update_agent",
    "register_tools",
]
