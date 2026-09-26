"""
Organization MCP Tools

Tools for listing, creating, getting, updating, and deleting organizations.
Every organization route is platform-admin only in REST
(``CurrentSuperuser``), so every tool in this file is a thin wrapper over
the REST API — none of them touch the ORM or repositories directly, so
they inherit REST's exact gate and behavior.
"""

import logging
import re
from typing import Any
from uuid import UUID

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


async def list_organizations(context: Any) -> ToolResult:
    """List all organizations — thin wrapper over ``GET /api/organizations``."""
    logger.info("MCP list_organizations called")

    status_code, resp = await call_rest(context, "GET", "/api/organizations")
    if status_code != 200:
        return error_result(f"list_organizations failed: HTTP {status_code}", {"body": resp})

    orgs = resp if isinstance(resp, list) else []
    orgs_data = [
        {
            "id": org.get("id"),
            "name": org.get("name"),
            "domain": org.get("domain"),
            "is_active": org.get("is_active"),
        }
        for org in orgs
    ]
    display_text = f"Found {len(orgs_data)} organization(s)"
    return success_result(display_text, {"organizations": orgs_data, "count": len(orgs_data)})


async def get_organization(
    context: Any,
    organization_id: str | None = None,
    domain: str | None = None,
) -> ToolResult:
    """Get organization details by ID or domain.

    ID lookup is a thin wrapper over ``GET /api/organizations/{id}``.
    Domain lookup filters the thin-wrapped list result (REST has no
    by-domain route) — both paths inherit REST's platform-admin gate.
    """
    logger.info(f"MCP get_organization called with id={organization_id}, domain={domain}")

    if not organization_id and not domain:
        return error_result("Either organization_id or domain is required")

    if organization_id:
        try:
            UUID(organization_id)
        except ValueError:
            return error_result(f"Invalid organization_id format: {organization_id}")
        status_code, resp = await call_rest(
            context, "GET", f"/api/organizations/{organization_id}"
        )
        if status_code != 200:
            return error_result(f"Organization not found: {organization_id}", {"body": resp})
        org = resp if isinstance(resp, dict) else {}
    else:
        status_code, resp = await call_rest(context, "GET", "/api/organizations")
        if status_code != 200:
            return error_result(f"get_organization failed: HTTP {status_code}", {"body": resp})
        org = next((o for o in (resp or []) if o.get("domain") == domain), None)
        if org is None:
            return error_result(f"Organization not found: {domain}")

    display_text = f"Organization: {org.get('name')}"
    return success_result(display_text, org)


async def create_organization(
    context: Any,
    name: str,
    domain: str | None = None,
) -> ToolResult:
    """Create a new organization — thin wrapper over ``POST /api/organizations``.

    Args:
        context: MCP context with user permissions
        name: Organization name (required)
        domain: Organization domain (optional, auto-generated from name if not provided)

    Returns:
        ToolResult with created organization details
    """
    logger.info(f"MCP create_organization called with name={name}")

    if not name:
        return error_result("name is required")
    if len(name) > 255:
        return error_result("name must be 255 characters or less")

    if not domain:
        domain = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if len(domain) > 255:
        return error_result("domain must be 255 characters or less")

    status_code, resp = await call_rest(
        context, "POST", "/api/organizations", json_body={"name": name, "domain": domain}
    )
    if status_code not in (200, 201):
        return error_result(f"create_organization failed: HTTP {status_code}", {"body": resp})

    org = resp if isinstance(resp, dict) else {}
    display_text = f"Created organization: {org.get('name', name)}"
    return success_result(display_text, {"success": True, **org})


# ---------------------------------------------------------------------------
# Thin-wrapper parity tools (Task 6)
# ---------------------------------------------------------------------------


async def update_organization(
    context: Any,
    organization_ref: str,
    name: str | None = None,
    is_active: bool | None = None,
) -> ToolResult:
    """Update an organization — ``PATCH /api/organizations/{uuid}``.

    ``organization_ref`` is a UUID or organization name. ``domain`` and
    ``settings`` are excluded by design (see
    :data:`bifrost.dto_flags.DTO_EXCLUDES` — ``domain`` is
    auto-provisioning policy, ``settings`` is a UI-managed JSON blob).
    """
    if not organization_ref:
        return error_result("organization_ref is required")

    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.organizations import OrganizationUpdate

    exclude = DTO_EXCLUDES.get("OrganizationUpdate", set())

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            org_uuid = await resolver.resolve("org", organization_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve organization {organization_ref!r}",
                _ref_error_payload(exc),
            )

        fields: dict[str, Any] = {"name": name, "is_active": is_active}
        try:
            body = await assemble_body(
                OrganizationUpdate,
                {k: v for k, v in fields.items() if k not in exclude},
                resolver=resolver,
            )
        except Exception as exc:
            return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "PATCH", f"/api/organizations/{org_uuid}", json_body=body
    )
    if status_code != 200:
        return error_result(
            f"update_organization failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Updated organization {org_uuid}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def delete_organization(context: Any, organization_ref: str) -> ToolResult:
    """Delete an organization — ``DELETE /api/organizations/{uuid}``.

    ``organization_ref`` is a UUID or organization name. Soft-delete
    semantics are owned by the REST endpoint.
    """
    if not organization_ref:
        return error_result("organization_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            org_uuid = await resolver.resolve("org", organization_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve organization {organization_ref!r}",
                _ref_error_payload(exc),
            )

    status_code, resp = await call_rest(
        context, "DELETE", f"/api/organizations/{org_uuid}"
    )
    if status_code not in (200, 204):
        return error_result(
            f"delete_organization failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(f"Deleted organization {org_uuid}", {"deleted": org_uuid})


# Tool metadata for registration
TOOLS = [
    ("list_organizations", "List Organizations", "List all organizations in the platform."),
    ("get_organization", "Get Organization", "Get organization details by ID or domain."),
    ("create_organization", "Create Organization", "Create a new organization."),
    ("update_organization", "Update Organization", "Update an organization (name, is_active)."),
    ("delete_organization", "Delete Organization", "Delete (soft-delete) an organization."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all organizations tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "list_organizations": list_organizations,
        "get_organization": get_organization,
        "create_organization": create_organization,
        "update_organization": update_organization,
        "delete_organization": delete_organization,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
