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

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.scope_resolver import has_scope_bypass

# Sentinel: a repo path did not resolve to any owning entity (Application or
# Workflow). Distinct from an owner whose organization_id is None (a global
# entity) — an unresolved path is bypass-only for a different reason (there
# is nothing to check ownership of).
NO_OWNING_ENTITY = object()


def apply_mcp_org_scope(
    query: Select[Any],
    model: Any,
    context: Any,
) -> Select[Any]:
    """Apply the org cascade to an MCP tool query.

    Rules (mirrors ``OrgScopedRepository`` / ``resolve_org_filter``):

    - Platform admin or provider-org caller: no filter — full visibility.
    - Org user: cascade — own org OR global.
    - Non-admin with no org: global only (legacy behavior).

    Args:
        query: The SELECT to scope.
        model: The ORM model being filtered (must have ``organization_id``).
        context: The MCP context carrying principal flags and ``org_id``.
    """
    if has_scope_bypass(
        is_platform_admin=getattr(context, "is_platform_admin", False),
        is_provider_org=getattr(context, "is_provider_org", False),
    ):
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


def mcp_write_scope_bypass(context: Any) -> bool:
    """Whether ``context`` may write regardless of organization ownership."""
    return has_scope_bypass(
        is_platform_admin=getattr(context, "is_platform_admin", False),
        is_provider_org=getattr(context, "is_provider_org", False),
    )


def mcp_caller_org_id(context: Any) -> UUID | None:
    org_id = getattr(context, "org_id", None)
    if isinstance(org_id, UUID):
        return org_id
    if isinstance(org_id, str) and org_id:
        return UUID(org_id)
    return None


async def resolve_repo_path_owner_org(db: AsyncSession, repo_path: str) -> Any:
    """Resolve the organization that owns a `_repo/` path, for write scoping.

    Maps ``apps/<slug>/...`` to its Application and a ``.py`` path to its
    Workflow (by exact path match). Returns the owner's ``organization_id``
    (which may be ``None`` for a global entity), or ``NO_OWNING_ENTITY`` if
    the path maps to nothing Bifrost tracks ownership for — such a path is
    bypass-only.
    """
    from src.models.orm.applications import Application
    from src.models.orm.workflows import Workflow

    if repo_path.startswith("apps/"):
        result = await db.execute(
            select(Application.repo_path, Application.organization_id).where(
                Application.repo_path.is_not(None)
            )
        )
        for app_repo_path, org_id in result.all():
            prefix = app_repo_path.rstrip("/") + "/"
            if repo_path.startswith(prefix):
                return org_id
        return NO_OWNING_ENTITY

    if repo_path.endswith(".py"):
        result = await db.execute(
            select(Workflow.organization_id).where(Workflow.path == repo_path).limit(1)
        )
        row = result.first()
        if row is not None:
            return row[0]
        return NO_OWNING_ENTITY

    return NO_OWNING_ENTITY


def write_scope_denied(owner_org_id: Any, caller_org_id: UUID | None) -> bool:
    """True if a resolved owner org (or lack thereof) denies the caller."""
    return (
        owner_org_id is NO_OWNING_ENTITY
        or owner_org_id is None
        or owner_org_id != caller_org_id
    )
