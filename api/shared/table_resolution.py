"""Shared table resolution service.

Table name/UUID resolution with the org gate, Solution install fallback, and
inbound gate, shared by the HTTP router (``src.routers.tables``) and the
engine SDK parent dispatcher (``src.services.execution.sdk_local_dispatch``)
so both transports resolve the same table for the same caller.

``get_table_or_404`` moved here verbatim from the router; the router imports
it. Resolution order is load-bearing — UUID lookup, org gate, own-install /
name fallback, inbound gate — keep it in that order on both paths.

Trust contract for the parent dispatcher: the resolution context carries
``db``, ``user``, ``org_id``, ``app_id``, ``solution_id``, and
``caller_solution_id``. HTTP supplies its existing request context. The parent
builds a ``LocalTableContext`` from trusted execution/service metadata only:
the ``user`` is a token-equivalent ``UserPrincipal`` (engine superuser or
service non-superuser — never the initiating user's admin flag), ``solution_id``
is the child-supplied per-call *target* (falling back to the parent-owned own
install), and ``app_id``/``caller_solution_id`` stay parent-owned (None for
engine children — never child frame claims).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.core.org_filter import resolve_target_org
from src.core.principal import UserPrincipal
from src.models.orm.tables import Table
from src.repositories.tables import TableRepository
from src.services.solution_scope import (
    resolve_effective_solution_id,
    resolve_solution_table_by_name,
)

logger = logging.getLogger(__name__)


class TableResolutionContext(Protocol):
    """Structural context for table resolution (HTTP or parent-local).

    ``src.core.auth.ExecutionContext`` satisfies this structurally; the parent
    dispatcher builds :class:`LocalTableContext`.
    """

    db: AsyncSession
    user: UserPrincipal
    org_id: UUID | None
    app_id: str | None
    solution_id: str | None
    caller_solution_id: str | None


@dataclass
class LocalTableContext:
    """Parent-built resolution context for engine-local table reads.

    Constructed per request from the parent-owned dispatch principal plus the
    child frame's untrusted ``scope``/``solution`` target strings. ``user`` is
    the token-equivalent principal (engine sentinel superuser for workflows,
    org-scoped service identity for ``@service`` children); ``org_id`` is the
    parent-owned caller org; ``solution_id`` is the per-call target install
    ref (UUID or slug/name, resolved inside the target org downstream).
    ``app_id`` and ``caller_solution_id`` are never taken from child frames —
    engine children have no app header and attest their install through the
    signed engine claims on ``user``.
    """

    db: AsyncSession
    user: UserPrincipal
    org_id: UUID | None
    app_id: str | None = None
    solution_id: str | None = None
    caller_solution_id: str | None = None


def resolve_target_org_safe(
    ctx: TableResolutionContext, scope: str | None
) -> UUID | None:
    """Resolve the target organization ID from scope parameter (with auth check)."""
    try:
        return resolve_target_org(ctx.user, scope, ctx.org_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


async def get_table_or_404(
    ctx: TableResolutionContext,
    name_or_id: str,
    scope: str | None = None,
) -> Table:
    """Get table by name or UUID, raise 404 if not found.

    Routes both UUID and name lookups through ``OrgScopedRepository.get``,
    which already enforces the org gate (its ID-lookup branch returns None
    for non-superusers reaching outside their own-or-global scope). Avoids
    bypassing the gate with raw SELECT.

    Install-scoped name resolution (a Solution app via ``X-Bifrost-App`` OR a
    solution workflow via ``ctx.solution_id``) is handled in
    ``resolve_solution_table_by_name`` — own-first, then the org/_repo/ cascade.
    Gated by the org check.
    """
    target_org_id = resolve_target_org_safe(ctx, scope)
    repo = TableRepository(
        ctx.db,
        target_org_id,
        is_superuser=ctx.user.is_superuser,
        is_external=ctx.user.is_external,
    )

    # Try UUID lookup first — repo.get(id=...) enforces the org gate for
    # non-superusers (returns None if entity is in a different org).
    table: Table | None = None
    try:
        table_uuid = UUID(name_or_id)
        table = await repo.get(id=table_uuid)
    except ValueError:
        # Not a UUID — fall through to name-based lookup
        logger.debug(
            f"table identifier {log_safe(name_or_id)!r} is not a UUID, "
            "falling back to name lookup"
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

    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{name_or_id}' not found",
        )

    # SPIKE inbound gate for direct UUID access to install-owned tables
    # (the name path is gated inside resolve_solution_table_by_name).
    # Own-install callers pass; otherwise allow_inbound_access decides.
    if table.solution_id is not None:
        from src.services.solution_scope import (
            check_inbound_allowed,
            resolve_trustworthy_caller,
        )

        caller = await resolve_trustworthy_caller(ctx.db, ctx)
        if not await check_inbound_allowed(ctx.db, table.solution_id, caller):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{name_or_id}' not found",
            )

    return table
