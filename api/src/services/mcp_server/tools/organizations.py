"""
Organization MCP Tools

Tools for listing, creating, getting, updating, and deleting organizations.
All organization tools are restricted (platform-admin only).

``list_organizations`` / ``get_organization`` / ``create_organization``
predate this plan and continue to use the ORM directly; they are **not
modified** by the Task 6 parity work.

``update_organization`` and ``delete_organization`` are added here as
thin wrappers over the REST API (Task 6). They must not touch the ORM
or repositories — all side effects go through the canonical REST path.
"""

import logging
import re
from typing import Any
from uuid import UUID, uuid4

from fastmcp.tools import ToolResult
from sqlalchemy import select

from src.services.mcp_server.tools.db import get_tool_db
from src.models.orm.organizations import Organization
from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client

logger = logging.getLogger(__name__)


def _require_platform_admin(context: Any) -> ToolResult | None:
    if not getattr(context, "is_platform_admin", False):
        return error_result("Platform administrator privileges are required for organization tools.")
    return None


def _ref_error_payload(exc: Exception) -> dict[str, Any]:
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    if isinstance(exc, AmbiguousRefError):
        return {"kind": exc.kind, "value": exc.value, "candidates": exc.candidates}
    if isinstance(exc, RefNotFoundError):
        return {"kind": exc.kind, "value": exc.value}
    return {"detail": str(exc)}


async def list_organizations(context: Any) -> ToolResult:
    """List all organizations.

    Platform admin only. Returns id, name, domain, is_active for each org.
    """
    logger.info("MCP list_organizations called")
    if denied := _require_platform_admin(context):
        return denied

    try:
        async with get_tool_db(context) as db:
            query = select(Organization).order_by(Organization.name)
            result = await db.execute(query)
            orgs = result.scalars().all()

            orgs_data = [
                {
                    "id": str(org.id),
                    "name": org.name,
                    "domain": org.domain,
                    "is_active": org.is_active,
                }
                for org in orgs
            ]

            display_text = f"Found {len(orgs_data)} organization(s)"
            return success_result(display_text, {"organizations": orgs_data, "count": len(orgs_data)})

    except Exception as e:
        logger.exception(f"Error listing organizations via MCP: {e}")
        return error_result(f"Error listing organizations: {str(e)}")


async def get_organization(
    context: Any,
    organization_id: str | None = None,
    domain: str | None = None,
) -> ToolResult:
    """Get organization details by ID or domain.

    Platform admin only. Must provide at least one of organization_id or domain.
    """
    logger.info(f"MCP get_organization called with id={organization_id}, domain={domain}")
    if denied := _require_platform_admin(context):
        return denied

    if not organization_id and not domain:
        return error_result("Either organization_id or domain is required")

    try:
        async with get_tool_db(context) as db:
            query = select(Organization)

            if organization_id:
                try:
                    query = query.where(Organization.id == UUID(organization_id))
                except ValueError:
                    return error_result(f"Invalid organization_id format: {organization_id}")
            else:
                query = query.where(Organization.domain == domain)

            result = await db.execute(query)
            org = result.scalar_one_or_none()

            if not org:
                identifier = organization_id or domain
                return error_result(f"Organization not found: {identifier}")

            org_data = {
                "id": str(org.id),
                "name": org.name,
                "domain": org.domain,
                "is_active": org.is_active,
                "settings": org.settings,
                "created_at": org.created_at.isoformat() if org.created_at else None,
                "created_by": org.created_by,
                "updated_at": org.updated_at.isoformat() if org.updated_at else None,
            }

            display_text = f"Organization: {org.name}"
            return success_result(display_text, org_data)

    except Exception as e:
        logger.exception(f"Error getting organization via MCP: {e}")
        return error_result(f"Error getting organization: {str(e)}")


async def create_organization(
    context: Any,
    name: str,
    domain: str | None = None,
) -> ToolResult:
    """Create a new organization.

    Platform admin only.

    Args:
        context: MCP context with user permissions
        name: Organization name (required)
        domain: Organization domain (optional, auto-generated from name if not provided)

    Returns:
        ToolResult with created organization details
    """
    logger.info(f"MCP create_organization called with name={name}")
    if denied := _require_platform_admin(context):
        return denied

    if not name:
        return error_result("name is required")

    if len(name) > 255:
        return error_result("name must be 255 characters or less")

    # Generate domain from name if not provided
    if not domain:
        # Convert to lowercase, replace spaces/special chars with hyphens
        domain = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    if len(domain) > 255:
        return error_result("domain must be 255 characters or less")

    try:
        async with get_tool_db(context) as db:
            # Check for duplicate domain
            existing_query = select(Organization).where(Organization.domain == domain)
            existing_result = await db.execute(existing_query)
            if existing_result.scalar_one_or_none():
                return error_result(f"Organization with domain '{domain}' already exists")

            # Create organization
            org = Organization(
                id=uuid4(),
                name=name,
                domain=domain,
                is_active=True,
                settings={},
                created_by=context.user_email,
            )

            db.add(org)
            await db.commit()

            logger.info(f"Created organization {org.id}: {org.name}")

            display_text = f"Created organization: {org.name}"
            return success_result(display_text, {
                "success": True,
                "id": str(org.id),
                "name": org.name,
                "domain": org.domain,
                "is_active": org.is_active,
            })

    except Exception as e:
        logger.exception(f"Error creating organization via MCP: {e}")
        return error_result(f"Error creating organization: {str(e)}")


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
    if denied := _require_platform_admin(context):
        return denied

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
    if denied := _require_platform_admin(context):
        return denied

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
