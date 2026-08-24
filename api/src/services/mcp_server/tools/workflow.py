"""Workflow MCP tools backed by the canonical REST API.

Every handler in this module is a transport adapter: it resolves human refs,
assembles the shared request contract, and crosses FastAPI's normal auth,
validation, audit, manifest, cache, Solution, and execution-worker boundary.
Workflow behavior must not be reimplemented against the ORM here.
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


async def _resolve_scope(context: Any, scope: str | None) -> str | None:
    if scope is None or scope == "global":
        return scope
    return await _resolve_ref(context, "org", scope)


async def bifrost_list_workflows(
    context: Any,
    query: str | None = None,
    category: str | None = None,
    type: str | None = None,
    scope: str | None = None,
) -> ToolResult:
    """List Workflows visible to the caller through ``GET /api/workflows``."""

    params: dict[str, Any] = {}
    for key, value in (("query", query), ("category", category), ("type", type)):
        if value is not None:
            params[key] = value
    try:
        resolved_scope = await _resolve_scope(context, scope)
    except Exception as exc:
        return error_result(f"Could not resolve scope {scope!r}", _ref_error_payload(exc))
    if resolved_scope is not None:
        params["scope"] = resolved_scope

    status_code, body = await call_rest(
        context,
        "GET",
        "/api/workflows",
        params=params,
    )
    if status_code != 200:
        return _rest_error("List Workflows", status_code, body)
    workflows = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(workflows)} Workflow(s)",
        {"workflows": workflows, "count": len(workflows)},
    )


async def bifrost_get_workflow(context: Any, workflow_ref: str) -> ToolResult:
    """Get one Workflow by UUID, name, or ``path::function`` ref."""

    if not workflow_ref:
        return error_result("workflow_ref is required")
    try:
        workflow_id = await _resolve_ref(context, "workflow", workflow_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Workflow {workflow_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/workflows/{workflow_id}",
    )
    if status_code != 200:
        return _rest_error("Get Workflow", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(f"Workflow: {payload.get('name', workflow_id)}", payload)


async def bifrost_validate_workflow(
    context: Any,
    path: str,
    content: str | None = None,
) -> ToolResult:
    """Validate a workspace Workflow file through ``POST /api/workflows/validate``."""

    if not path:
        return error_result("path is required")
    body: dict[str, Any] = {"path": path}
    if content is not None:
        body["content"] = content
    status_code, response = await call_rest(
        context,
        "POST",
        "/api/workflows/validate",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Validate Workflow", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    if not payload.get("valid", False):
        return error_result("Workflow validation failed", payload)
    return success_result(f"Workflow {path!r} is valid", payload)


async def bifrost_register_workflow(
    context: Any,
    path: str,
    function_name: str,
    access_level: str | None = None,
    role_ids: list[str] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Register a decorated Workspace function as a Workflow.

    ``scope`` is ``global``, an organization UUID/name, or omitted for the
    caller's home organization. Role values accept UUIDs or names.
    """

    if not path:
        return error_result("path is required")
    if not function_name:
        return error_result("function_name is required")

    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.workflows import RegisterWorkflowRequest

    fields: dict[str, Any] = {
        "path": path,
        "function_name": function_name,
        "access_level": access_level,
        "role_ids": role_ids,
    }
    try:
        async with rest_client(context) as http:
            resolver = RefResolver(http)
            body = await assemble_body(
                RegisterWorkflowRequest,
                fields,
                resolver=resolver,
            )
            if scope is not None:
                body["organization_id"] = (
                    None
                    if scope == "global"
                    else await resolver.resolve("org", scope)
                )
    except Exception as exc:
        return error_result(f"Invalid Workflow input: {exc}", _ref_error_payload(exc))

    status_code, response = await call_rest(
        context,
        "POST",
        "/api/workflows/register",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Register Workflow", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Registered Workflow: {payload.get('name', function_name)}",
        payload,
    )


async def bifrost_execute_workflow(
    context: Any,
    workflow_ref: str,
    input_data: dict[str, Any] | None = None,
    sync: bool = False,
    scope: str | None = None,
    run_as: str | None = None,
    scheduled_at: str | None = None,
    delay_seconds: int | None = None,
) -> ToolResult:
    """Execute a Workflow through the shared execution-worker boundary.

    Async execution is the default. Use the returned ``execution_id`` with
    ``bifrost_get_execution`` for progress and results. ``sync=True`` is useful
    for short Workflow/data-provider calls. ``scope`` is an optional execution
    organization UUID/name and requires platform-admin authority; ``run_as`` is
    an optional user UUID and follows the REST impersonation policy.
    """

    if not workflow_ref:
        return error_result("workflow_ref is required")
    try:
        async with rest_client(context) as http:
            from bifrost.refs import RefResolver

            resolver = RefResolver(http)
            workflow_id = await resolver.resolve("workflow", workflow_ref)
            body: dict[str, Any] = {
                "workflow_id": workflow_id,
                "input_data": input_data or {},
                "sync": sync,
            }
            if scope is not None:
                if scope == "global":
                    return error_result(
                        "Execution scope must be an organization, not global"
                    )
                body["org_id"] = await resolver.resolve("org", scope)
            if run_as is not None:
                body["run_as"] = run_as
            if scheduled_at is not None:
                body["scheduled_at"] = scheduled_at
            if delay_seconds is not None:
                body["delay_seconds"] = delay_seconds
    except Exception as exc:
        return error_result(
            f"Could not resolve Workflow execution input: {exc}",
            _ref_error_payload(exc),
        )

    status_code, response = await call_rest(
        context,
        "POST",
        "/api/workflows/execute",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Execute Workflow", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    status_value = str(payload.get("status", ""))
    if status_value in {"Failed", "CompletedWithErrors", "Timeout", "Cancelled"}:
        return error_result(
            f"Workflow execution {status_value.lower()}",
            payload,
        )
    if status_value in {"Pending", "Running", "Scheduled"}:
        return success_result(
            f"Workflow execution {status_value.lower()}: {payload.get('execution_id')}",
            payload,
        )
    return success_result(
        f"Workflow execution completed: {payload.get('execution_id')}",
        payload,
    )


async def bifrost_update_workflow(
    context: Any,
    workflow_ref: str,
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
    scope: str | None = None,
) -> ToolResult:
    """Update a Workflow through ``PATCH /api/workflows/{id}``.

    ``scope`` is ``global`` or an organization UUID/name; omit it to preserve
    the current target. Role values accept UUIDs or names.
    """

    if not workflow_ref:
        return error_result("workflow_ref is required")

    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.workflows import WorkflowUpdateRequest

    fields: dict[str, Any] = {
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
    exclude = DTO_EXCLUDES.get("WorkflowUpdateRequest", set())
    try:
        async with rest_client(context) as http:
            resolver = RefResolver(http)
            workflow_id = await resolver.resolve("workflow", workflow_ref)
            body = await assemble_body(
                WorkflowUpdateRequest,
                {key: value for key, value in fields.items() if key not in exclude},
                resolver=resolver,
            )
            if scope is not None:
                body["organization_id"] = (
                    None
                    if scope == "global"
                    else await resolver.resolve("org", scope)
                )
    except Exception as exc:
        return error_result(f"Invalid Workflow input: {exc}", _ref_error_payload(exc))
    if not body:
        return error_result("No updates provided")

    status_code, response = await call_rest(
        context,
        "PATCH",
        f"/api/workflows/{workflow_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Workflow", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Updated Workflow: {payload.get('name', workflow_id)}",
        payload,
    )


async def bifrost_delete_workflow(
    context: Any,
    workflow_ref: str,
    force_deactivation: bool = False,
) -> ToolResult:
    """Delete a Workflow, optionally confirming dependent deactivation."""

    if not workflow_ref:
        return error_result("workflow_ref is required")
    try:
        workflow_id = await _resolve_ref(context, "workflow", workflow_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Workflow {workflow_ref!r}",
            _ref_error_payload(exc),
        )
    body = {"force_deactivation": True} if force_deactivation else None
    status_code, response = await call_rest(
        context,
        "DELETE",
        f"/api/workflows/{workflow_id}",
        json_body=body,
    )
    if status_code == 409:
        return error_result(
            "Workflow has dependencies; retry with force_deactivation=true",
            response if isinstance(response, dict) else {"body": response},
        )
    if status_code not in {200, 204}:
        return _rest_error("Delete Workflow", status_code, response)
    payload = response if isinstance(response, dict) else {"deleted": workflow_id}
    return success_result(f"Deleted Workflow {workflow_id}", payload)


async def bifrost_grant_workflow_role(
    context: Any,
    workflow_ref: str,
    role_ref: str,
) -> ToolResult:
    """Grant one Role access to a Workflow."""

    if not workflow_ref:
        return error_result("workflow_ref is required")
    if not role_ref:
        return error_result("role_ref is required")
    try:
        async with rest_client(context) as http:
            from bifrost.refs import RefResolver

            resolver = RefResolver(http)
            workflow_id = await resolver.resolve("workflow", workflow_ref)
            role_id = await resolver.resolve("role", role_ref)
    except Exception as exc:
        return error_result("Could not resolve Workflow or Role", _ref_error_payload(exc))

    status_code, response = await call_rest(
        context,
        "POST",
        f"/api/workflows/{workflow_id}/roles",
        json_body={"role_ids": [role_id]},
    )
    if status_code not in {200, 201, 204}:
        return _rest_error("Grant Workflow Role", status_code, response)
    return success_result(
        f"Granted Role {role_id} on Workflow {workflow_id}",
        {"workflow_id": workflow_id, "role_id": role_id},
    )


async def bifrost_revoke_workflow_role(
    context: Any,
    workflow_ref: str,
    role_ref: str,
) -> ToolResult:
    """Revoke one Role's access from a Workflow."""

    if not workflow_ref:
        return error_result("workflow_ref is required")
    if not role_ref:
        return error_result("role_ref is required")
    try:
        async with rest_client(context) as http:
            from bifrost.refs import RefResolver

            resolver = RefResolver(http)
            workflow_id = await resolver.resolve("workflow", workflow_ref)
            role_id = await resolver.resolve("role", role_ref)
    except Exception as exc:
        return error_result("Could not resolve Workflow or Role", _ref_error_payload(exc))

    status_code, response = await call_rest(
        context,
        "DELETE",
        f"/api/workflows/{workflow_id}/roles/{role_id}",
    )
    if status_code not in {200, 204}:
        return _rest_error("Revoke Workflow Role", status_code, response)
    return success_result(
        f"Revoked Role {role_id} on Workflow {workflow_id}",
        {"workflow_id": workflow_id, "role_id": role_id},
    )


TOOLS = [
    (
        "bifrost_list_workflows",
        "List Workflows",
        "List Workflows visible to the caller, with optional query, category, type, and organization filters.",
    ),
    (
        "bifrost_get_workflow",
        "Get Workflow",
        "Get one Workflow by UUID, name, or path::function reference.",
    ),
    (
        "bifrost_validate_workflow",
        "Validate Workflow",
        "Validate a workspace Python file or supplied Python content as a Workflow.",
    ),
    (
        "bifrost_register_workflow",
        "Register Workflow",
        "Register a decorated workspace Python function as a Workflow.",
    ),
    (
        "bifrost_execute_workflow",
        "Execute Workflow",
        "Execute a Workflow through the shared execution worker; async by default and returns an execution ID.",
    ),
    (
        "bifrost_update_workflow",
        "Update Workflow",
        "Update an existing Workflow through the canonical REST policy boundary.",
    ),
    (
        "bifrost_delete_workflow",
        "Delete Workflow",
        "Delete a Workflow, returning dependency details when confirmation is required.",
    ),
    (
        "bifrost_grant_workflow_role",
        "Grant Workflow Role",
        "Grant a Role access to a Workflow.",
    ),
    (
        "bifrost_revoke_workflow_role",
        "Revoke Workflow Role",
        "Revoke a Role's access from a Workflow.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all canonical Workflow tools with FastMCP."""

    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_workflows": bifrost_list_workflows,
        "bifrost_get_workflow": bifrost_get_workflow,
        "bifrost_validate_workflow": bifrost_validate_workflow,
        "bifrost_register_workflow": bifrost_register_workflow,
        "bifrost_execute_workflow": bifrost_execute_workflow,
        "bifrost_update_workflow": bifrost_update_workflow,
        "bifrost_delete_workflow": bifrost_delete_workflow,
        "bifrost_grant_workflow_role": bifrost_grant_workflow_role,
        "bifrost_revoke_workflow_role": bifrost_revoke_workflow_role,
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
    "bifrost_delete_workflow",
    "bifrost_execute_workflow",
    "bifrost_get_workflow",
    "bifrost_grant_workflow_role",
    "bifrost_list_workflows",
    "bifrost_register_workflow",
    "bifrost_revoke_workflow_role",
    "bifrost_update_workflow",
    "bifrost_validate_workflow",
    "register_tools",
]
