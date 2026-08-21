"""Solution MCP tools backed by the canonical REST lifecycle."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client


def _error(action: str, status_code: int, body: Any) -> ToolResult:
    detail = body.get("detail") if isinstance(body, dict) else None
    return error_result(
        str(detail) if detail else f"{action} failed: HTTP {status_code}",
        {"status_code": status_code, "body": body},
    )


async def _resolve_boundary(
    context: Any,
    scope: str | None,
) -> tuple[str | None, str | None]:
    if scope is None:
        return None, None
    if scope.lower() == "global":
        return None, "platform"

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        organization_id = await RefResolver(http).resolve("org", scope)
    return organization_id, f"organization:{organization_id}"


async def _resolve_solution(
    context: Any,
    solution_ref: str,
    *,
    authorization_boundary: str | None,
) -> dict[str, Any]:
    try:
        solution_id = str(UUID(solution_ref))
    except ValueError:
        solution_id = ""

    status_code, body = await call_rest(
        context,
        "GET",
        "/api/solutions",
        authorization_boundary=authorization_boundary,
    )
    if status_code != 200 or not isinstance(body, dict):
        raise ValueError(f"could not list Solutions: HTTP {status_code}")
    matches = [
        row
        for row in body.get("solutions", [])
        if isinstance(row, dict)
        and (row.get("id") == solution_id or row.get("slug") == solution_ref)
    ]
    if not matches:
        raise ValueError(f"Solution {solution_ref!r} was not found in this context")
    if len(matches) > 1:
        raise ValueError(
            f"Solution {solution_ref!r} is ambiguous in this context; use its UUID"
        )
    return matches[0]


async def bifrost_list_solutions(
    context: Any,
    scope: str | None = None,
) -> ToolResult:
    """List shared Solution installs in one Organization or Global context."""

    try:
        _, boundary = await _resolve_boundary(context, scope)
    except Exception as exc:
        return error_result(f"Invalid Solution scope: {exc}")
    status_code, body = await call_rest(
        context,
        "GET",
        "/api/solutions",
        authorization_boundary=boundary,
    )
    if status_code != 200 or not isinstance(body, dict):
        return _error("List Solutions", status_code, body)
    items = body.get("solutions", [])
    return success_result(
        f"Found {len(items)} Solution(s)",
        {"solutions": items, "count": len(items)},
    )


async def bifrost_get_solution(
    context: Any,
    solution_ref: str,
    scope: str | None = None,
) -> ToolResult:
    """Get one shared Solution install by UUID or slug."""

    try:
        _, boundary = await _resolve_boundary(context, scope)
        solution = await _resolve_solution(
            context,
            solution_ref,
            authorization_boundary=boundary,
        )
    except Exception as exc:
        return error_result(str(exc))
    return success_result(f"Solution: {solution.get('name')}", solution)


async def bifrost_create_solution(
    context: Any,
    slug: str,
    name: str,
    scope: str | None = None,
    global_repo_access: bool = False,
    git_connected: bool = False,
    git_repo_url: str | None = None,
    repo_subpath: str | None = None,
    git_ref: str | None = None,
) -> ToolResult:
    """Create a shared Solution install in the selected target."""

    try:
        organization_id, boundary = await _resolve_boundary(context, scope)
    except Exception as exc:
        return error_result(f"Invalid Solution scope: {exc}")
    body: dict[str, Any] = {
        "slug": slug,
        "name": name,
        "global_repo_access": global_repo_access,
        "git_connected": git_connected,
        "git_repo_url": git_repo_url,
        "repo_subpath": repo_subpath,
        "git_ref": git_ref,
    }
    if scope is not None:
        body["organization_id"] = organization_id
    status_code, response = await call_rest(
        context,
        "POST",
        "/api/solutions",
        json_body=body,
        authorization_boundary=boundary,
    )
    if status_code not in (200, 201) or not isinstance(response, dict):
        return _error("Create Solution", status_code, response)
    return success_result(f"Created Solution: {response.get('name', name)}", response)


async def bifrost_update_solution(
    context: Any,
    solution_ref: str,
    scope: str | None = None,
    name: str | None = None,
    global_repo_access: bool | None = None,
    git_connected: bool | None = None,
    git_repo_url: str | None = None,
    repo_subpath: str | None = None,
    git_ref: str | None = None,
) -> ToolResult:
    """Update local fields on one shared Solution install."""

    try:
        _, boundary = await _resolve_boundary(context, scope)
        solution = await _resolve_solution(
            context,
            solution_ref,
            authorization_boundary=boundary,
        )
    except Exception as exc:
        return error_result(str(exc))
    values = {
        "name": name,
        "global_repo_access": global_repo_access,
        "git_connected": git_connected,
        "git_repo_url": git_repo_url,
        "repo_subpath": repo_subpath,
        "git_ref": git_ref,
    }
    body = {key: value for key, value in values.items() if value is not None}
    if not body:
        return error_result("At least one update field is required")
    status_code, response = await call_rest(
        context,
        "PATCH",
        f"/api/solutions/{solution['id']}",
        json_body=body,
        authorization_boundary=boundary,
    )
    if status_code != 200 or not isinstance(response, dict):
        return _error("Update Solution", status_code, response)
    return success_result(f"Updated Solution: {response.get('name')}", response)


async def bifrost_delete_solution(
    context: Any,
    solution_ref: str,
    scope: str | None = None,
) -> ToolResult:
    """Permanently delete one shared Solution after server-side slug confirmation."""

    try:
        _, boundary = await _resolve_boundary(context, scope)
        solution = await _resolve_solution(
            context,
            solution_ref,
            authorization_boundary=boundary,
        )
    except Exception as exc:
        return error_result(str(exc))
    status_code, response = await call_rest(
        context,
        "DELETE",
        f"/api/solutions/{solution['id']}",
        params={"confirm": solution["slug"]},
        authorization_boundary=boundary,
    )
    if status_code not in (200, 204):
        return _error("Delete Solution", status_code, response)
    return success_result(
        f"Deleted Solution: {solution.get('name')}",
        response if isinstance(response, dict) else {"deleted": solution["id"]},
    )


async def bifrost_sync_solution(
    context: Any,
    solution_ref: str,
    scope: str | None = None,
) -> ToolResult:
    """Pull and deploy one git-connected shared Solution."""

    try:
        _, boundary = await _resolve_boundary(context, scope)
        solution = await _resolve_solution(
            context,
            solution_ref,
            authorization_boundary=boundary,
        )
    except Exception as exc:
        return error_result(str(exc))
    status_code, response = await call_rest(
        context,
        "POST",
        f"/api/solutions/{solution['id']}/sync",
        authorization_boundary=boundary,
    )
    if status_code not in (200, 202):
        return _error("Sync Solution", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(f"Synced Solution: {solution.get('name')}", payload)


TOOLS = [
    ("bifrost_list_solutions", "List Solutions", "List shared Solution installs."),
    ("bifrost_get_solution", "Get Solution", "Get a shared Solution install."),
    ("bifrost_create_solution", "Create Solution", "Create a shared Solution install."),
    ("bifrost_update_solution", "Update Solution", "Update a shared Solution install."),
    ("bifrost_delete_solution", "Delete Solution", "Delete a shared Solution install."),
    (
        "bifrost_sync_solution",
        "Sync Solution",
        "Sync a git-connected Solution install.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    functions = {name: globals()[name] for name, _title, _description in TOOLS}
    for name, _title, description in TOOLS:
        register_tool_with_context(
            mcp,
            functions[name],
            name,
            description,
            get_context_fn,
        )


__all__ = [
    "TOOLS",
    "bifrost_create_solution",
    "bifrost_delete_solution",
    "bifrost_get_solution",
    "bifrost_list_solutions",
    "bifrost_sync_solution",
    "bifrost_update_solution",
    "register_tools",
]
