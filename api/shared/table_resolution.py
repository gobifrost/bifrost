"""Shared table resolution service.

Table name/UUID resolution with the org gate, Solution install fallback, and
inbound gate, shared by the HTTP router (``src.routers.tables``) and the
worker-local engine socket
(``api/src/services/execution/worker_sdk_http.py``) so both entry points
resolve the same table for the same caller.

``get_table_or_404`` moved here verbatim from the router; the router imports
it. Resolution order is load-bearing — UUID lookup, org gate, own-install /
name fallback, inbound gate — keep it in that order on both paths.

The resolution context carries ``db``, ``user``, ``org_id``, ``app_id``,
``solution_id``, and ``caller_solution_id``. Both entry points build it from
an authenticated request/execution context. Target reach is resolved from the
original user and typed runtime powers; ``solution_id`` is the per-call
target, while a caller installation is separately derived from durable runtime
state.
"""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.core.principal import UserPrincipal
from src.models.orm.tables import Table
from src.repositories.tables import TableRepository
from src.services.solution_scope import (
    resolve_effective_solution_id,
    resolve_solution_table_by_name,
)

logger = logging.getLogger(__name__)


class TableResolutionContext(Protocol):
    """Structural context for table resolution.

    ``src.core.auth.ExecutionContext`` satisfies this structurally.
    """

    db: AsyncSession
    user: UserPrincipal
    org_id: UUID | None
    app_id: str | None
    solution_id: str | None
    caller_solution_id: str | None


async def resolve_target_org_safe(
    ctx: TableResolutionContext, scope: str | None
) -> UUID | None:
    """Resolve a table target from the verified caller's reach."""
    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope
    from src.services.authorization.enforce import load_caller

    try:
        return await resolve_sdk_scope(
            scope, caller=await load_caller(ctx.db, ctx.user)
        )
    except ScopeResolutionError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        )


async def assert_solution_write_targets_owned_table(
    ctx: TableResolutionContext, table: Table
) -> None:
    target_org = table.organization_id
    solution_id = await resolve_effective_solution_id(ctx.db, ctx, target_org)
    if solution_id is None or table.solution_id == solution_id:
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Table '{table.name}' not found",
    )


async def assert_explicit_scope_targets_table(
    ctx: TableResolutionContext, table: Table, scope: str | None
) -> None:
    if scope is None:
        return
    target_org_id = await resolve_target_org_safe(ctx, scope)
    exact_table = (
        await ctx.db.execute(
            select(Table).where(
                Table.id == table.id,
                Table.organization_id == target_org_id,
            )
        )
    ).scalar_one_or_none()
    if exact_table is not None:
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Table '{table.name}' not found",
    )


async def assert_resolved_table_reachable(
    ctx: TableResolutionContext,
    table: Table,
    *,
    target_org_id: UUID | None,
    scope: str | None,
) -> None:
    """Keep a resolved row inside the original user's reach.

    UUID resolution deliberately uses an exact ORM lookup, because a role can
    reach a table outside the caller's default organization.  It therefore
    must check the row's actual organization after lookup.  An explicit scope
    is exact rather than a cascade selector, so it cannot select a row from a
    different organization merely because that row is also reachable.
    """
    from src.services.authorization.enforce import load_caller, user_reach

    caller = await load_caller(ctx.db, ctx.user)
    if (
        not user_reach(caller).covers(table.organization_id)
        or (scope is not None and table.organization_id != target_org_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{table.name}' not found",
        )


async def get_table_or_404(
    ctx: TableResolutionContext,
    name_or_id: str,
    scope: str | None = None,
) -> Table:
    """Get table by name or UUID, raise 404 if not found.

    Resolves the target scope from the verified caller before every lookup.
    The repository then applies only the allowed org→Global cascade.

    Install-scoped name resolution (a Solution app via ``X-Bifrost-App`` OR a
    solution workflow via ``ctx.solution_id``) is handled in
    ``resolve_solution_table_by_name`` — own-first, then the org/_repo/ cascade.
    Gated by the org check.
    """
    target_org_id = await resolve_target_org_safe(ctx, scope)
    repo = TableRepository(
        ctx.db,
        target_org_id,
        is_superuser=False,
        is_external=ctx.user.is_external,
    )

    # Try UUID lookup first after the target reach gate above.
    table: Table | None = None
    name_resolution = False
    try:
        table_uuid = UUID(name_or_id)
        table = await ctx.db.get(Table, table_uuid)
    except ValueError:
        # Not a UUID — fall through to name-based lookup
        logger.debug(
            f"table identifier {log_safe(name_or_id)!r} is not a UUID, "
            "falling back to name lookup"
        )
    if table is not None:
        await assert_resolved_table_reachable(
            ctx, table, target_org_id=target_org_id, scope=scope
        )
    solution_id = await resolve_effective_solution_id(ctx.db, ctx, target_org_id)
    if table is not None and solution_id is not None and table.solution_id != solution_id:
        table = None

    # Fall back to name lookup (cascade scoping: org-specific then global).
    if not table:
        # A Solution app (X-Bifrost-App header) references a table by NAME but
        # can't know the per-install remapped id — resolve its OWN install's
        # table. Without this, the name cascade excludes solution-managed rows
        # and every row op 404s even though the app deployed the table (Codex #15).
        # The lookup is GATED to the caller's org scope (Codex #16): the
        # X-Bifrost-App header is client-supplied, so it must NOT let a caller
        # reach a table in an org they can't see by passing a foreign app id.
        install_table = await resolve_solution_table_by_name(
            ctx.db, ctx, name_or_id, target_org_id
        )
        if install_table is not None:
            table = install_table
        elif solution_id is not None:
            table = None
        else:
            table = await repo.get_by_name(name_or_id)
        name_resolution = table is not None

    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{name_or_id}' not found",
        )

    await assert_resolved_table_reachable(
        ctx,
        table,
        target_org_id=target_org_id,
        # A name goes through the canonical org-then-Global cascade, which a
        # scope only starts; it never reaches another org. A UUID is exact,
        # so an explicit scope must match the table's own org.
        scope=None if name_resolution else scope,
    )

    # SPIKE inbound gate for direct UUID access to install-owned tables
    # (the name path is gated inside resolve_solution_table_by_name).
    # Own-install callers pass; otherwise allow_inbound_access decides.
    if table.solution_id is not None:
        from src.services.solution_scope import (
            inbound_allowed_for_context,
        )

        if not await inbound_allowed_for_context(ctx.db, ctx, table.solution_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{name_or_id}' not found",
            )

    return table
