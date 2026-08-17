"""Form MCP tools backed by the canonical REST API.

Form CRUD uses the same DTOs, authorization, validation, role propagation,
cache invalidation, audit, and manifest side effects as the web client and CLI.
This module only resolves human references and translates ``ToolResult``.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from shared.form_runtime import DEFAULT_FORM_CONFIRMATION_MARKDOWN
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


async def _assemble_form_body(
    context: Any,
    fields: dict[str, Any],
    *,
    is_update: bool,
    scope: str | None,
) -> dict[str, Any]:
    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.forms import FormCreate, FormUpdate

    model_cls = FormUpdate if is_update else FormCreate
    async with rest_client(context) as http:
        resolver = RefResolver(http)
        body = await assemble_body(model_cls, fields, resolver=resolver)
        if scope is not None:
            if scope == "global":
                body["organization_id"] = None
            else:
                body["organization_id"] = await resolver.resolve("org", scope)
    return body


async def bifrost_list_forms(
    context: Any,
    scope: str | None = None,
) -> ToolResult:
    """List Forms visible to the caller through ``GET /api/forms``."""

    params = {"scope": scope} if scope is not None else None
    status_code, body = await call_rest(
        context,
        "GET",
        "/api/forms",
        params=params,
    )
    if status_code != 200:
        return _rest_error("List Forms", status_code, body)
    forms = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(forms)} Form(s)",
        {"forms": forms, "count": len(forms)},
    )


async def bifrost_get_form(context: Any, form_ref: str) -> ToolResult:
    """Get one Form by UUID or accessible name through the REST API."""

    if not form_ref:
        return error_result("form_ref is required")
    try:
        form_id = await _resolve_ref(context, "form", form_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Form {form_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/forms/{form_id}",
    )
    if status_code != 200:
        return _rest_error("Get Form", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(f"Form: {payload.get('name', form_id)}", payload)


async def bifrost_create_form(
    context: Any,
    name: str,
    form_schema: dict[str, Any],
    description: str | None = None,
    confirmation_markdown: str = DEFAULT_FORM_CONFIRMATION_MARKDOWN,
    workflow_id: str | None = None,
    launch_workflow_id: str | None = None,
    default_launch_params: dict[str, Any] | None = None,
    allowed_query_params: list[str] | None = None,
    access_level: str | None = None,
    role_ids: list[str] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Create a Form through ``POST /api/forms``.

    Workflow, launch-workflow, role, and organization values accept UUIDs or
    human refs. ``scope`` is ``global``, an organization UUID/name, or omitted
    for the caller's home organization.
    """

    try:
        body = await _assemble_form_body(
            context,
            {
                "name": name,
                "description": description,
                "confirmation_markdown": confirmation_markdown,
                "workflow_id": workflow_id,
                "launch_workflow_id": launch_workflow_id,
                "default_launch_params": default_launch_params,
                "allowed_query_params": allowed_query_params,
                "form_schema": form_schema,
                "access_level": access_level,
                "role_ids": role_ids,
            },
            is_update=False,
            scope=scope,
        )
    except Exception as exc:
        return error_result(f"Invalid Form input: {exc}", _ref_error_payload(exc))
    status_code, response = await call_rest(
        context,
        "POST",
        "/api/forms",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Form", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(f"Created Form: {payload.get('name', name)}", payload)


async def bifrost_update_form(
    context: Any,
    form_ref: str,
    name: str | None = None,
    description: str | None = None,
    confirmation_markdown: str | None = None,
    workflow_id: str | None = None,
    launch_workflow_id: str | None = None,
    default_launch_params: dict[str, Any] | None = None,
    allowed_query_params: list[str] | None = None,
    form_schema: dict[str, Any] | None = None,
    is_active: bool | None = None,
    access_level: str | None = None,
    clear_roles: bool | None = None,
    role_ids: list[str] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Update a Form through ``PATCH /api/forms/{id}``.

    Pass an empty string for ``workflow_id`` or ``launch_workflow_id`` to
    clear that nullable reference.
    """

    if not form_ref:
        return error_result("form_ref is required")
    try:
        form_id = await _resolve_ref(context, "form", form_ref)
        body = await _assemble_form_body(
            context,
            {
                "name": name,
                "description": description,
                "confirmation_markdown": confirmation_markdown,
                "workflow_id": workflow_id,
                "launch_workflow_id": launch_workflow_id,
                "default_launch_params": default_launch_params,
                "allowed_query_params": allowed_query_params,
                "form_schema": form_schema,
                "is_active": is_active,
                "access_level": access_level,
                "clear_roles": clear_roles,
                "role_ids": role_ids,
            },
            is_update=True,
            scope=scope,
        )
    except Exception as exc:
        return error_result(f"Invalid Form input: {exc}", _ref_error_payload(exc))
    if not body:
        return error_result("No updates provided")
    status_code, response = await call_rest(
        context,
        "PATCH",
        f"/api/forms/{form_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Form", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(f"Updated Form: {payload.get('name', form_id)}", payload)


async def bifrost_delete_form(
    context: Any,
    form_ref: str,
    purge: bool = False,
) -> ToolResult:
    """Deactivate or purge a Form through the canonical REST endpoint."""

    if not form_ref:
        return error_result("form_ref is required")
    try:
        form_id = await _resolve_ref(context, "form", form_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Form {form_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "DELETE",
        f"/api/forms/{form_id}",
        params={"purge": purge},
    )
    if status_code != 204:
        return _rest_error("Delete Form", status_code, body)
    action = "Purged" if purge else "Deactivated"
    return success_result(
        f"{action} Form {form_id}",
        {"deleted": form_id, "purged": purge},
    )


TOOLS = [
    ("bifrost_list_forms", "List Forms", "List Forms visible to the caller."),
    ("bifrost_get_form", "Get Form", "Get a Form by UUID or accessible name."),
    (
        "bifrost_create_form",
        "Create Form",
        "Create a Form through the canonical platform API.",
    ),
    (
        "bifrost_update_form",
        "Update Form",
        "Update a Form through the canonical platform API.",
    ),
    (
        "bifrost_delete_form",
        "Delete Form",
        "Deactivate or purge a Form through the canonical platform API.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register Form tools with FastMCP."""

    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_forms": bifrost_list_forms,
        "bifrost_get_form": bifrost_get_form,
        "bifrost_create_form": bifrost_create_form,
        "bifrost_update_form": bifrost_update_form,
        "bifrost_delete_form": bifrost_delete_form,
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
    "bifrost_create_form",
    "bifrost_delete_form",
    "bifrost_get_form",
    "bifrost_list_forms",
    "bifrost_update_form",
    "register_tools",
]
