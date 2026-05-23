"""Custom Claim MCP Tools — thin wrappers around the REST API."""

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


async def _assemble_claim_body(
    context: Any,
    fields: dict[str, Any],
    *,
    is_update: bool,
) -> dict[str, Any]:
    """Validate + assemble the REST payload using the shared DTO generator."""
    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.claims import CustomClaimCreate, CustomClaimUpdate

    model_cls = CustomClaimUpdate if is_update else CustomClaimCreate
    exclude = DTO_EXCLUDES.get(model_cls.__name__, set())

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        return await assemble_body(
            model_cls,
            {k: v for k, v in fields.items() if k not in exclude},
            resolver=resolver,
        )


def _scope_params(scope: str | None) -> dict[str, Any] | None:
    return {"scope": scope} if scope else None


async def list_claims(context: Any, scope: str | None = None) -> ToolResult:
    """List custom claims — thin wrapper over ``GET /api/claims``."""
    logger.info("MCP list_claims (HTTP bridge)")
    status_code, body = await call_rest(
        context, "GET", "/api/claims", params=_scope_params(scope)
    )
    if status_code != 200:
        return error_result(f"list_claims failed: HTTP {status_code}", {"body": body})
    items = body.get("claims", []) if isinstance(body, dict) else []
    return success_result(
        f"Found {len(items)} custom claim(s)",
        {"claims": items, "count": len(items)},
    )


async def get_claim(context: Any, name: str, scope: str | None = None) -> ToolResult:
    """Get a custom claim by name — thin wrapper over ``GET /api/claims/{name}``."""
    if not name:
        return error_result("name is required")

    status_code, body = await call_rest(
        context, "GET", f"/api/claims/{name}", params=_scope_params(scope)
    )
    if status_code != 200:
        return error_result(f"get_claim failed: HTTP {status_code}", {"body": body})
    return success_result(
        f"Custom claim: {body.get('name') if isinstance(body, dict) else name}",
        body if isinstance(body, dict) else {"body": body},
    )


async def create_claim(
    context: Any,
    name: str,
    query: dict[str, Any],
    description: str | None = None,
    type: str = "list",
    scope: str | None = None,
) -> ToolResult:
    """Create a custom claim — thin wrapper over ``POST /api/claims``."""
    if not name:
        return error_result("name is required")
    if not query:
        return error_result("query is required")

    fields = {
        "name": name,
        "description": description,
        "type": type,
        "query": query,
    }
    try:
        body = await _assemble_claim_body(context, fields, is_update=False)
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "POST", "/api/claims", json_body=body, params=_scope_params(scope)
    )
    if status_code not in (200, 201):
        return error_result(f"create_claim failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Created custom claim: {resp.get('name') if isinstance(resp, dict) else ''}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def update_claim(
    context: Any,
    name: str,
    description: str | None = None,
    type: str | None = None,
    query: dict[str, Any] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Update a custom claim by name — thin wrapper over ``PATCH /api/claims/{name}``."""
    if not name:
        return error_result("name is required")

    fields = {"description": description, "type": type, "query": query}
    try:
        body = await _assemble_claim_body(context, fields, is_update=True)
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context,
        "PATCH",
        f"/api/claims/{name}",
        json_body=body,
        params=_scope_params(scope),
    )
    if status_code != 200:
        return error_result(f"update_claim failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Updated custom claim: {name}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def delete_claim(
    context: Any, name: str, scope: str | None = None
) -> ToolResult:
    """Delete a custom claim by name — thin wrapper over ``DELETE /api/claims/{name}``."""
    if not name:
        return error_result("name is required")

    status_code, resp = await call_rest(
        context, "DELETE", f"/api/claims/{name}", params=_scope_params(scope)
    )
    if status_code not in (200, 204):
        return error_result(f"delete_claim failed: HTTP {status_code}", {"body": resp})
    return success_result(f"Deleted custom claim {name}", {"deleted": name})


TOOLS = [
    ("list_claims", "List Custom Claims", "List custom claims in the current org."),
    ("get_claim", "Get Custom Claim", "Get a custom claim by name."),
    ("create_claim", "Create Custom Claim", "Create a custom claim."),
    ("update_claim", "Update Custom Claim", "Update a custom claim."),
    ("delete_claim", "Delete Custom Claim", "Delete a custom claim."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all claims tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "list_claims": list_claims,
        "get_claim": get_claim,
        "create_claim": create_claim,
        "update_claim": update_claim,
        "delete_claim": delete_claim,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "create_claim",
    "delete_claim",
    "get_claim",
    "list_claims",
    "register_tools",
    "update_claim",
]
