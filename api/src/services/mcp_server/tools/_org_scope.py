"""
Shared org-cascade helper for MCP tools.

MCP tools authenticate as the user directly (not the engine sentinel), so they
must enforce the same org-scoping that ``OrgScopedRepository`` enforces on the
REST path. Historically each tool hand-rolled ``org == X OR org IS NULL``,
which drifted (cross-org by-name role matches, unscoped discovery queries).

This helper is the ONE place that applies the cascade for MCP tool queries.
Every tool that filters an execution-resolution model by org must route
through it so the scoping can never drift back in.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Select, or_


def apply_mcp_org_scope(
    query: Select[Any],
    model: Any,
    context: Any,
) -> Select[Any]:
    """Apply the org cascade to an MCP tool query.

    Rules (mirrors ``OrgScopedRepository`` / ``resolve_org_filter``):

    - Platform admin (``context.is_platform_admin``): no filter — full visibility.
    - Org user: cascade — own org OR global.
    - Non-admin with no org: global only (legacy behavior).

    Args:
        query: The SELECT to scope.
        model: The ORM model being filtered (must have ``organization_id``).
        context: The MCP context carrying ``is_platform_admin`` and ``org_id``.
    """
    if getattr(context, "is_platform_admin", False):
        return query

    org_id = getattr(context, "org_id", None)
    if isinstance(org_id, str) and org_id:
        org_id = UUID(org_id)

    if org_id is not None:
        return query.where(
            or_(
                model.organization_id == org_id,
                model.organization_id.is_(None),
            )
        )

    # Legacy: a non-admin with no org sees only global.
    return query.where(model.organization_id.is_(None))
