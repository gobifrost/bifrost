"""Application MCP tools backed by the canonical REST API.

Application metadata, dependency, validation, publish, and source-path tools
reuse the same REST authorization, validation, audit, manifest, cache, and
Platform Job behavior as the web client and CLI. Workspace source edits live
in the canonical file tools rather than this Application domain.
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


async def _resolve_app_ref(context: Any, app_ref: str) -> str:
    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        return await RefResolver(http).resolve("app", app_ref)


async def _assemble_app_body(
    context: Any,
    fields: dict[str, Any],
    *,
    is_update: bool,
    scope: str | None,
) -> dict[str, Any]:
    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.applications import ApplicationCreate, ApplicationUpdate

    model_cls = ApplicationUpdate if is_update else ApplicationCreate
    async with rest_client(context) as http:
        resolver = RefResolver(http)
        body = await assemble_body(model_cls, fields, resolver=resolver)
        if scope is not None:
            if scope == "global":
                body["organization_id"] = None
            else:
                body["organization_id"] = await resolver.resolve("org", scope)
    return body


async def bifrost_list_apps(
    context: Any,
    scope: str | None = None,
) -> ToolResult:
    """List Applications visible to the caller through REST."""

    status_code, body = await call_rest(
        context,
        "GET",
        "/api/applications",
        params={"scope": scope} if scope is not None else None,
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("List Applications", status_code, body)
    applications = body.get("applications", [])
    return success_result(
        f"Found {len(applications)} Application(s)",
        {"applications": applications, "count": len(applications)},
    )


async def bifrost_get_app(context: Any, app_ref: str) -> ToolResult:
    """Get one Application by UUID, slug, or unambiguous name."""

    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(context, "GET", "/api/applications")
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Get Application", status_code, body)
    payload = next(
        (
            app
            for app in body.get("applications", [])
            if isinstance(app, dict) and str(app.get("id")) == app_id
        ),
        None,
    )
    if payload is None:
        return error_result(
            f"Application {app_ref!r} is not in the accessible list",
            {"app_id": app_id},
        )
    return success_result(f"Application: {payload.get('name', app_id)}", payload)


async def bifrost_create_app(
    context: Any,
    name: str,
    slug: str,
    description: str | None = None,
    access_level: str = "authenticated",
    app_model: str = "standalone_v2",
    role_ids: list[str] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Create an Application through ``POST /api/applications``.

    ``scope`` is ``global``, an organization UUID/name, or omitted for the
    caller's home organization. Loose Applications must explicitly use
    ``app_model='inline_v1'``; v2 Apps are created by Solution deployment.
    """

    try:
        body = await _assemble_app_body(
            context,
            {
                "name": name,
                "slug": slug,
                "description": description,
                "access_level": access_level,
                "app_model": app_model,
                "role_ids": role_ids,
            },
            is_update=False,
            scope=scope,
        )
    except Exception as exc:
        return error_result(
            f"Invalid Application input: {exc}", _ref_error_payload(exc)
        )
    status_code, response = await call_rest(
        context,
        "POST",
        "/api/applications",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Application", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(f"Created Application: {payload.get('name', name)}", payload)


async def bifrost_update_app(
    context: Any,
    app_ref: str,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    access_level: str | None = None,
    role_ids: list[str] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Update Application metadata through ``PATCH /api/applications/{id}``."""

    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
        body = await _assemble_app_body(
            context,
            {
                "name": name,
                "slug": slug,
                "description": description,
                "access_level": access_level,
                "role_ids": role_ids,
            },
            is_update=True,
            scope=scope,
        )
    except Exception as exc:
        return error_result(
            f"Invalid Application input: {exc}", _ref_error_payload(exc)
        )
    if not body:
        return error_result("No updates provided")
    status_code, response = await call_rest(
        context,
        "PATCH",
        f"/api/applications/{app_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Application", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Updated Application: {payload.get('name', app_id)}", payload
    )


async def bifrost_delete_app(context: Any, app_ref: str) -> ToolResult:
    """Delete an Application through the canonical REST endpoint."""

    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "DELETE",
        f"/api/applications/{app_id}",
    )
    if status_code != 204:
        return _rest_error("Delete Application", status_code, body)
    return success_result("Deleted Application", {"success": True, "id": app_id})


async def bifrost_publish_app(
    context: Any,
    app_ref: str,
    message: str | None = None,
) -> ToolResult:
    """Queue publishing through the canonical REST build-and-promote path."""
    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    logger.info("MCP bifrost_publish_app (HTTP bridge) id=%s", app_id)
    status_code, body = await call_rest(
        context,
        "POST",
        f"/api/applications/{app_id}/publish",
        json_body={"message": message} if message else {},
    )
    if status_code != 202 or not isinstance(body, dict):
        return _rest_error("Publish Application", status_code, body)
    job_id = body.get("job_id")
    return success_result(
        f"Application publish queued: {job_id}",
        body,
    )


async def bifrost_get_app_publish_status(
    context: Any,
    publish_job_id: str,
) -> ToolResult:
    """Read durable publish progress through the canonical REST endpoint."""
    logger.info(
        "MCP bifrost_get_app_publish_status (HTTP bridge) job=%s",
        publish_job_id,
    )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/platform-jobs/{publish_job_id}",
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Get Application publish status", status_code, body)
    status_value = body.get("status", "unknown")
    progress = body.get("progress") or {}
    phase = progress.get("phase")
    description = f"Application publish {status_value}"
    if phase:
        description += f": {phase}"
    if status_value in ("failed", "cancelled"):
        error = body.get("error") or {}
        return error_result(
            error.get("message") or description,
            body,
        )
    return success_result(description, body)


async def bifrost_replace_app(
    context: Any,
    app_ref: str,
    repo_path: str,
    force: bool = False,
) -> ToolResult:
    """Repoint an application's source directory — thin wrapper over
    ``POST /api/applications/{app_id}/replace``.

    Updates ``repo_path`` after source files have been moved/renamed. The
    server validates that the new path is unique, non-nested with other
    apps, and has source files under it. ``force=True`` bypasses all
    three checks.
    """
    if not app_ref:
        return error_result("app_ref is required")
    if not repo_path:
        return error_result("repo_path is required")

    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )

    body: dict[str, Any] = {"repo_path": repo_path, "force": force}
    status_code, resp = await call_rest(
        context, "POST", f"/api/applications/{app_id}/replace", json_body=body
    )
    if status_code != 200:
        return _rest_error("Replace Application source path", status_code, resp)
    return success_result(
        f"Repointed application {app_id} to {repo_path}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_validate_app(context: Any, app_ref: str) -> ToolResult:
    """Validate an Application through ``POST /api/applications/{id}/validate``."""

    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "POST",
        f"/api/applications/{app_id}/validate",
    )
    if status_code != 200:
        return _rest_error("Validate Application", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    errors = payload.get("errors", [])
    warnings = payload.get("warnings", [])
    return success_result(
        f"Application validation: {len(errors)} error(s), {len(warnings)} warning(s)",
        payload,
    )


async def bifrost_get_app_dependencies(
    context: Any,
    app_ref: str,
) -> ToolResult:
    """Get one Application's npm dependencies through REST."""
    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "GET",
        f"/api/applications/{app_id}/dependencies",
    )
    if status_code != 200 or not isinstance(response, dict):
        return _rest_error("Get Application dependencies", status_code, response)
    return success_result(
        f"Application dependencies: {len(response)} package(s)",
        {"app_id": app_id, "dependencies": response},
    )


async def bifrost_update_app_dependencies(
    context: Any,
    app_ref: str,
    dependencies: dict[str, str],
) -> ToolResult:
    """Replace one Application's npm dependencies through REST."""
    if not app_ref:
        return error_result("app_ref is required")
    try:
        app_id = await _resolve_app_ref(context, app_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Application {app_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "PUT",
        f"/api/applications/{app_id}/dependencies",
        json_body=dependencies,
    )
    if status_code != 200 or not isinstance(response, dict):
        return _rest_error("Update Application dependencies", status_code, response)
    return success_result(
        f"Updated Application dependencies: {len(response)} package(s)",
        {"app_id": app_id, "dependencies": response},
    )


# Tool metadata for registration
TOOLS = [
    (
        "bifrost_list_apps",
        "List Applications",
        "List Applications visible to the caller.",
    ),
    (
        "bifrost_get_app",
        "Get Application",
        "Get Application metadata by UUID, slug, or unambiguous name.",
    ),
    (
        "bifrost_create_app",
        "Create Application",
        "Create a loose Application through the canonical REST contract.",
    ),
    (
        "bifrost_update_app",
        "Update Application",
        "Update Application metadata and access through REST.",
    ),
    ("bifrost_delete_app", "Delete Application", "Delete an Application through REST."),
    (
        "bifrost_publish_app",
        "Publish Application",
        "Queue a durable rebuild and publish.",
    ),
    (
        "bifrost_get_app_publish_status",
        "Get Application Publish Status",
        "Get progress, result, or error for an Application publish job.",
    ),
    (
        "bifrost_replace_app",
        "Replace Application Source Path",
        "Repoint an Application's workspace source directory.",
    ),
    (
        "bifrost_validate_app",
        "Validate Application",
        "Compile and validate an Application through REST.",
    ),
    (
        "bifrost_get_app_dependencies",
        "Get Application Dependencies",
        "Get npm dependencies declared for an Application.",
    ),
    (
        "bifrost_update_app_dependencies",
        "Update Application Dependencies",
        "Replace npm dependencies for an Application.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all apps tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_apps": bifrost_list_apps,
        "bifrost_get_app": bifrost_get_app,
        "bifrost_create_app": bifrost_create_app,
        "bifrost_update_app": bifrost_update_app,
        "bifrost_delete_app": bifrost_delete_app,
        "bifrost_publish_app": bifrost_publish_app,
        "bifrost_get_app_publish_status": bifrost_get_app_publish_status,
        "bifrost_replace_app": bifrost_replace_app,
        "bifrost_validate_app": bifrost_validate_app,
        "bifrost_get_app_dependencies": bifrost_get_app_dependencies,
        "bifrost_update_app_dependencies": bifrost_update_app_dependencies,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )
