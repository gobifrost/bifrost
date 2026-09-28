"""
Workflow MCP Tools — thin wrappers around the REST API.

``bifrost_workflow_list``, ``bifrost_workflow_get``, ``bifrost_workflow_validate``,
``bifrost_workflow_register``, ``bifrost_workflow_execute``,
``bifrost_workflow_update``, ``bifrost_workflow_delete``,
``bifrost_workflow_role_grant``, ``bifrost_workflow_role_revoke``.

These tools are thin wrappers: they resolve human refs, assemble the shared
Workflow DTOs via ``bifrost/dto_flags.py``, then call the corresponding REST
endpoint through the in-process HTTP bridge (``_http_bridge``). No ORM, no
repositories, no ``AsyncSession`` — all side effects (audit logs, cache
invalidation, MCP tool registry refresh, role sync) happen behind the REST
handler, matching the CLI's path exactly.

``GET /api/workflows`` and ``GET /api/workflows/{workflow_id}`` are platform-
admin only (all orgs, no role filter) — see the R1b batch 5 decisions. A
non-admin caller gets a plain 403 from these tools, same as REST/CLI.
``bifrost_workflow_execute`` is unchanged in authorization: it wraps
``POST /api/workflows/execute``, which role-checks via
``shared/sdk_workflow_execution.py`` (platform admin, or access via a
form/app/integration that uses the workflow).
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client

logger = logging.getLogger(__name__)


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


async def bifrost_workflow_list(
    context: Any, query: str | None = None, category: str | None = None
) -> ToolResult:
    """List Workflows — thin wrapper over ``GET /api/workflows`` (platform admin only).

    ``query`` and ``category`` are applied client-side as a substring/exact
    filter over the REST list payload (the REST endpoint has no free-text
    search parameter).
    """
    status_code, body = await call_rest(context, "GET", "/api/workflows")
    if status_code != 200:
        return _rest_error("List Workflows", status_code, body)
    workflows = body if isinstance(body, list) else []

    if category:
        workflows = [w for w in workflows if w.get("category") == category]
    if query:
        needle = query.lower()
        workflows = [
            w
            for w in workflows
            if needle in (w.get("name") or "").lower()
            or needle in (w.get("description") or "").lower()
        ]

    if not workflows:
        return success_result("No workflows found", {"workflows": [], "count": 0})

    display_lines = [f"Found {len(workflows)} workflow(s):"]
    for w in workflows[:10]:
        desc = f" - {w.get('description')}" if w.get("description") else ""
        display_lines.append(f"  - {w.get('name')} ({w.get('type')}){desc}")
    if len(workflows) > 10:
        display_lines.append(f"  ... and {len(workflows) - 10} more")

    return success_result(
        "\n".join(display_lines),
        {"workflows": workflows, "count": len(workflows)},
    )


async def bifrost_workflow_get(context: Any, workflow_ref: str) -> ToolResult:
    """Get one Workflow by UUID, name, or ``path::func`` — thin wrapper over
    ``GET /api/workflows/{uuid}`` (platform admin only, any org, exact id lookup)."""
    if not workflow_ref:
        return error_result("workflow_ref is required")
    try:
        workflow_id = await _resolve_ref(context, "workflow", workflow_ref)
    except Exception as exc:
        return error_result(
            f"could not resolve workflow {workflow_ref!r}", _ref_error_payload(exc)
        )

    status_code, body = await call_rest(context, "GET", f"/api/workflows/{workflow_id}")
    if status_code != 200:
        return _rest_error("Get Workflow", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    desc = f" - {payload.get('description')}" if payload.get("description") else ""
    return success_result(
        f"Workflow: {payload.get('name', workflow_id)} ({payload.get('type')}){desc}",
        payload,
    )


async def bifrost_workflow_validate(
    context: Any, path: str, content: str | None = None
) -> ToolResult:
    """Validate a workflow Python file — thin wrapper over ``POST /api/workflows/validate``."""
    if not path:
        return error_result("path is required")

    body: dict[str, Any] = {"path": path}
    if content is not None:
        body["content"] = content

    status_code, resp = await call_rest(context, "POST", "/api/workflows/validate", json_body=body)
    if status_code != 200:
        return _rest_error("Validate Workflow", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    if payload.get("valid"):
        return success_result(f"Workflow '{path}' is valid", payload)
    issues = payload.get("issues") or []
    issue_msgs = "; ".join(str(i.get("message", i)) for i in issues) if issues else "unknown error"
    return error_result(f"Workflow '{path}' has errors: {issue_msgs}", payload)


async def bifrost_workflow_register(
    context: Any,
    path: str,
    function_name: str,
    organization_id: str | None = None,
    access_level: str | None = None,
    role_ids: list[str] | None = None,
) -> ToolResult:
    """Register a decorated Python function as a workflow — thin wrapper over
    ``POST /api/workflows/register`` (platform admin only).

    Takes a file path and function name; the REST endpoint validates the
    function has a ``@workflow``/``@tool``/``@data_provider``/``@service``
    decorator and registers it. ``organization_id`` accepts a UUID or org
    name (omit for global scope). ``role_ids`` accept UUIDs or role names.
    """
    if not path:
        return error_result("path is required")
    if not function_name:
        return error_result("function_name is required")
    if not path.endswith(".py"):
        return error_result("path must be a .py file")

    body: dict[str, Any] = {"path": path, "function_name": function_name}
    try:
        async with rest_client(context) as http:
            from bifrost.refs import RefResolver

            resolver = RefResolver(http)
            if organization_id:
                body["organization_id"] = await resolver.resolve("org", organization_id)
            if access_level is not None:
                body["access_level"] = access_level
            if role_ids:
                body["role_ids"] = [await resolver.resolve("role", r) for r in role_ids]
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "POST", "/api/workflows/register", json_body=body)
    if status_code not in (200, 201):
        return _rest_error("Register Workflow", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(
        f"Registered {payload.get('type')} '{payload.get('name', function_name)}' from {path}::{function_name}",
        payload,
    )


async def bifrost_workflow_execute(
    context: Any, workflow_id: str, params: dict[str, Any] | None = None
) -> ToolResult:
    """Execute a workflow by ID or name and return results — thin wrapper over
    ``POST /api/workflows/execute`` (``sync=True``, blocks until completion).

    Authorization is unchanged: platform admin, or access via a form/app/
    integration that uses the workflow (enforced by
    ``shared/sdk_workflow_execution.py`` behind the REST endpoint).
    """
    if not workflow_id:
        return error_result("workflow_id is required")

    params = params or {}
    logger.info(f"MCP bifrost_workflow_execute: {workflow_id} with params: {params}")

    body = {"workflow_id": workflow_id, "input_data": params, "sync": True}
    status_code, resp = await call_rest(context, "POST", "/api/workflows/execute", json_body=body)
    if status_code != 200:
        return _rest_error("Execute Workflow", status_code, resp)

    payload = resp if isinstance(resp, dict) else {}
    success = payload.get("status") == "Success"
    data = {
        "success": success,
        "execution_id": payload.get("execution_id"),
        "workflow_id": payload.get("workflow_id"),
        "workflow_name": payload.get("workflow_name"),
        "status": payload.get("status"),
        "duration_ms": payload.get("duration_ms"),
        "result": payload.get("result"),
        "error": payload.get("error"),
        "error_type": payload.get("error_type"),
    }

    workflow_name = payload.get("workflow_name") or workflow_id
    if success:
        return success_result(
            f"Workflow '{workflow_name}' completed successfully ({payload.get('duration_ms')}ms)",
            data,
        )
    return error_result(f"Workflow '{workflow_name}' failed: {payload.get('error')}", data)


async def bifrost_workflow_update(
    context: Any,
    workflow_ref: str,
    organization_id: str | None = None,
    access_level: str | None = None,
    clear_roles: bool | None = None,
    role_ids: list[str] | None = None,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    timeout_seconds: int | None = None,
    tags: list[str] | None = None,
    endpoint_enabled: bool | None = None,
    public_endpoint: bool | None = None,
) -> ToolResult:
    """Update a workflow — ``PATCH /api/workflows/{uuid}`` (platform admin only).

    ``workflow_ref`` is a UUID, workflow name, or ``path::func``.
    ``role_ids`` bulk-replaces the workflow's role assignments when supplied
    (an empty list clears them); pair with ``clear_roles=True`` only when no
    list is provided. ``name`` is the MCP tool name; ``function_name`` is not
    changed by this tool. Fields marked as UI/code-managed in
    :data:`bifrost.dto_flags.DTO_EXCLUDES` (``display_name``,
    ``tool_description``, ``time_saved``, ``value``, ``cache_ttl_seconds``,
    ``allowed_methods``, ``execution_mode``, ``disable_global_key``) are not
    surfaced here.
    """
    if not workflow_ref:
        return error_result("workflow_ref is required")

    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.workflows import WorkflowUpdateRequest

    exclude = DTO_EXCLUDES.get("WorkflowUpdateRequest", set())

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            workflow_uuid = await resolver.resolve("workflow", workflow_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve workflow {workflow_ref!r}",
                _ref_error_payload(exc),
            )

        fields: dict[str, Any] = {
            "organization_id": organization_id,
            "access_level": access_level,
            "clear_roles": clear_roles,
            "role_ids": role_ids,
            "name": name,
            "description": description,
            "category": category,
            "timeout_seconds": timeout_seconds,
            "tags": tags,
            "endpoint_enabled": endpoint_enabled,
            "public_endpoint": public_endpoint,
        }
        try:
            body = await assemble_body(
                WorkflowUpdateRequest,
                {k: v for k, v in fields.items() if k not in exclude},
                resolver=resolver,
            )
        except Exception as exc:
            return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "PATCH", f"/api/workflows/{workflow_uuid}", json_body=body
    )
    if status_code != 200:
        return _rest_error("Update Workflow", status_code, resp)
    return success_result(
        f"Updated workflow {workflow_uuid}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_workflow_delete(
    context: Any,
    workflow_ref: str,
    force_deactivation: bool = False,
) -> ToolResult:
    """Delete a workflow — ``DELETE /api/workflows/{uuid}`` (platform admin only).

    On first call, the endpoint returns 409 with deactivation details if
    the workflow has history or dependencies. Call again with
    ``force_deactivation=True`` to commit the deletion.
    """
    if not workflow_ref:
        return error_result("workflow_ref is required")

    try:
        workflow_uuid = await _resolve_ref(context, "workflow", workflow_ref)
    except Exception as exc:
        return error_result(
            f"could not resolve workflow {workflow_ref!r}", _ref_error_payload(exc)
        )

    body: dict[str, Any] | None = None
    if force_deactivation:
        body = {"force_deactivation": True}

    status_code, resp = await call_rest(
        context, "DELETE", f"/api/workflows/{workflow_uuid}", json_body=body
    )
    if status_code == 409:
        return error_result(
            "workflow has dependencies or history; retry with force_deactivation=true",
            resp if isinstance(resp, dict) else {"body": resp},
        )
    if status_code not in (200, 204):
        return _rest_error("Delete Workflow", status_code, resp)
    return success_result(f"Deleted workflow {workflow_uuid}", {"deleted": workflow_uuid})


async def bifrost_workflow_role_grant(
    context: Any,
    workflow_ref: str,
    role_ref: str,
) -> ToolResult:
    """Grant a role on a workflow — ``POST /api/workflows/{uuid}/roles`` (platform admin only).

    ``workflow_ref`` and ``role_ref`` are UUIDs or names. The REST endpoint
    is a batch assign that skips already-assigned roles, so this wrapper is
    idempotent.
    """
    if not workflow_ref:
        return error_result("workflow_ref is required")
    if not role_ref:
        return error_result("role_ref is required")

    async with rest_client(context) as http:
        from bifrost.refs import RefResolver

        resolver = RefResolver(http)
        try:
            workflow_uuid = await resolver.resolve("workflow", workflow_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve workflow {workflow_ref!r}",
                _ref_error_payload(exc),
            )
        try:
            role_uuid = await resolver.resolve("role", role_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve role {role_ref!r}", _ref_error_payload(exc)
            )

    status_code, resp = await call_rest(
        context,
        "POST",
        f"/api/workflows/{workflow_uuid}/roles",
        json_body={"role_ids": [role_uuid]},
    )
    if status_code not in (200, 201, 204):
        return _rest_error("Grant Workflow Role", status_code, resp)
    return success_result(
        f"Granted role {role_uuid} on workflow {workflow_uuid}",
        {"workflow_id": workflow_uuid, "role_id": role_uuid},
    )


async def bifrost_workflow_role_revoke(
    context: Any,
    workflow_ref: str,
    role_ref: str,
) -> ToolResult:
    """Revoke a role on a workflow — ``DELETE /api/workflows/{uuid}/roles/{role_id}`` (platform admin only)."""
    if not workflow_ref:
        return error_result("workflow_ref is required")
    if not role_ref:
        return error_result("role_ref is required")

    async with rest_client(context) as http:
        from bifrost.refs import RefResolver

        resolver = RefResolver(http)
        try:
            workflow_uuid = await resolver.resolve("workflow", workflow_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve workflow {workflow_ref!r}",
                _ref_error_payload(exc),
            )
        try:
            role_uuid = await resolver.resolve("role", role_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve role {role_ref!r}", _ref_error_payload(exc)
            )

    status_code, resp = await call_rest(
        context,
        "DELETE",
        f"/api/workflows/{workflow_uuid}/roles/{role_uuid}",
    )
    if status_code not in (200, 204):
        return _rest_error("Revoke Workflow Role", status_code, resp)
    return success_result(
        f"Revoked role {role_uuid} on workflow {workflow_uuid}",
        {"workflow_id": workflow_uuid, "role_id": role_uuid},
    )


# Tool metadata for registration
TOOLS = [
    (
        "bifrost_workflow_execute",
        "Execute Workflow",
        "Execute a Bifrost workflow by ID or name using its live input schema. "
        "Use bifrost_workflow_list to find workflows and bifrost_workflow_get to inspect parameters.",
    ),
    ("bifrost_workflow_list", "List Workflows", "List workflows registered in Bifrost (platform admin only)."),
    ("bifrost_workflow_validate", "Validate Workflow", "Validate a workflow Python file for syntax and decorator issues."),
    ("bifrost_workflow_get", "Get Workflow", "Get detailed metadata for a specific workflow by ID, name, or path::func (platform admin only)."),
    ("bifrost_workflow_register", "Register Workflow", "Register a decorated Python function as a workflow. Takes a file path, function name, and optional organization_id."),
    ("bifrost_workflow_update", "Update Workflow", "Update an existing workflow by UUID or name (organization, access level, description, etc.)."),
    ("bifrost_workflow_delete", "Delete Workflow", "Delete a workflow; returns 409 with deactivation details if it has history."),
    ("bifrost_workflow_role_grant", "Grant Workflow Role", "Grant a role access to a workflow."),
    ("bifrost_workflow_role_revoke", "Revoke Workflow Role", "Revoke a role's access to a workflow."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all workflow tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "bifrost_workflow_execute": bifrost_workflow_execute,
        "bifrost_workflow_list": bifrost_workflow_list,
        "bifrost_workflow_validate": bifrost_workflow_validate,
        "bifrost_workflow_get": bifrost_workflow_get,
        "bifrost_workflow_register": bifrost_workflow_register,
        "bifrost_workflow_update": bifrost_workflow_update,
        "bifrost_workflow_delete": bifrost_workflow_delete,
        "bifrost_workflow_role_grant": bifrost_workflow_role_grant,
        "bifrost_workflow_role_revoke": bifrost_workflow_role_revoke,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)


__all__ = [
    "TOOLS",
    "bifrost_workflow_delete",
    "bifrost_workflow_execute",
    "bifrost_workflow_get",
    "bifrost_workflow_list",
    "bifrost_workflow_register",
    "bifrost_workflow_role_grant",
    "bifrost_workflow_role_revoke",
    "bifrost_workflow_update",
    "bifrost_workflow_validate",
    "register_tools",
]
