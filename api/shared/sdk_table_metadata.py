"""Shared business service for SDK table metadata operations.

Single implementation used by the HTTP handlers serving external SDK
callers:

- ``POST /api/sdk/tables/create`` and ``POST /api/sdk/tables/list``
  (``api/src/routers/cli.py``), and
- ``DELETE /api/tables/{table_id}`` (``api/src/routers/tables.py``).

The engine-local dispatcher will call the same service with
parent-derived authority after the aggregate import-channel stage
finishes; there is no local transport in this stage.

All inputs are already-authoritative scalars: the HTTP edge resolves
scope through the shared ``resolve_sdk_scope`` semantics, the Solution
presence signal, and the caller identity (org, admin flag, external
flag, actor email) from the auth-verified principal — never from child
frame claims. A future local caller passes the same scalars from
parent-owned execution/service metadata.

All failures raise :class:`SDKTableMetadataError` (transport-neutral);
the HTTP adapter maps them to ``HTTPException`` preserving the exact
historical status and detail.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.policies.probe import make_seed_admin_bypass
from src.core.log_safety import log_safe
from src.models.orm.tables import Table
from src.services.solutions.guard import SOLUTION_MANAGED_MESSAGE

import src.repositories.tables as tables_repo_module

logger = logging.getLogger(__name__)


class SDKTableMetadataError(Exception):
    """SDK table-metadata failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and the local dispatcher (``ok: false`` frames) can map the same
    failure to their own transport.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def ensure_sdk_table_create_allowed(solution_present: bool) -> None:
    """Reject Solution contexts before scope validation or table lookup."""
    if solution_present:
        raise SDKTableMetadataError(
            404, "Tables must be declared by the solution manifest"
        )


def _table_info_dict(table: Table) -> dict[str, Any]:
    """JSON-serializable ``SDKTableInfo`` shape for one table row."""
    return {
        "id": str(table.id),
        "name": table.name,
        "organization_id": str(table.organization_id)
        if table.organization_id
        else None,
        "table_schema": table.schema,
        "description": table.description,
        "created_at": table.created_at.isoformat(),
        "updated_at": table.updated_at.isoformat(),
    }


async def create_sdk_table(
    session: AsyncSession,
    *,
    name: str,
    table_schema: dict[str, Any] | None,
    description: str | None,
    org_id: UUID | None,
    actor_email: str,
    solution_present: bool,
) -> dict[str, Any]:
    """Create one table in the exact resolved scope.

    Args:
        session: Short-lived parent/HTTP database session.
        name: Table name (already validated by the request DTO).
        table_schema: Optional schema hints.
        description: Optional table description.
        org_id: Already-resolved effective scope (None for global).
        actor_email: Parent-derived actor email for the ``created_by``
            audit column.
        solution_present: True when the caller runs in a Solution
            execution context (``?solution=`` or ``X-Bifrost-App``,
            resolved by auth onto the request context). Such callers may
            not create tables ad hoc — tables are declared by the
            Solution manifest and created at deploy.

    Returns:
        The ``SDKTableInfo`` shape as a plain dict (the caller builds
        the DTO).

    Raises:
        SDKTableMetadataError: 404 for a Solution execution context,
            409 when a ``_repo/`` table with the same name already
            exists in the exact scope.
    """
    ensure_sdk_table_create_allowed(solution_present)

    # Exact-scope uniqueness check (not a cascade): "is there already a
    # table named X in MY scope?" Cascade would mask collisions when a
    # global Table with the same name exists. See repositories/README.md.
    # NOTE: attribute access at call time (not a top-level from-import)
    # keeps the ``TableRepository`` seam patchable for both entry points.
    stmt = select(Table).where(
        Table.name == name,
        Table.organization_id == org_id,
        Table.solution_id.is_(None),
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        raise SDKTableMetadataError(409, f"Table '{name}' already exists")

    # Seed admin_bypass so platform admins can still operate on tables
    # created via the SDK. SDK callers can override this later by setting
    # explicit policies through the REST `PATCH /api/tables/{id}` endpoint.
    table = Table(
        name=name,
        description=description,
        schema=table_schema,
        organization_id=org_id,
        created_by=actor_email,
        access=make_seed_admin_bypass(),
    )
    session.add(table)
    await session.commit()
    await session.refresh(table)

    logger.info(f"CLI created table '{log_safe(name)}' for user {actor_email}")

    return _table_info_dict(table)


async def list_sdk_tables(
    session: AsyncSession,
    *,
    org_id: UUID | None,
    external: bool,
) -> list[dict[str, Any]]:
    """List tables visible in the resolved scope, sorted by name.

    Args:
        session: Short-lived parent/HTTP database session.
        org_id: Already-resolved effective scope (None for global).
        external: True for a direct EXTERNAL portal caller — drops the
            sentinel ``is_superuser`` trust so externals get the regular
            user cascade instead. Engine executions always pass False.

    Returns:
        The ``SDKTableInfo`` shapes as plain dicts (the caller builds
        the DTOs), sorted by table name.
    """
    # Principal-derived sentinel trust (OPEN-B): the sentinel/admins keep
    # is_superuser=True (their is_external claim is neutralized at mint); an
    # EXTERNAL principal must not inherit it — they get the regular-user
    # cascade instead. NOTE: attribute access at call time (not a top-level
    # from-import) keeps the ``TableRepository`` seam patchable for both
    # entry points.
    repo = tables_repo_module.TableRepository(
        session,
        org_id=org_id,
        is_superuser=not external,
        is_external=external,
    )
    tables = await repo.list()
    tables = sorted(tables, key=lambda t: t.name)

    return [_table_info_dict(t) for t in tables]


async def delete_sdk_table(
    session: AsyncSession,
    *,
    table_id: UUID,
    org_id: UUID | None,
) -> bool:
    """Delete one table by ID.

    Args:
        session: Short-lived parent/HTTP database session.
        table_id: Table UUID.
        org_id: Caller org for repository construction (the delete itself
            resolves by globally-unique ID, matching the historical
            router behavior).

    Returns:
        True when a row was deleted, False when the table does not exist
        (the caller maps False to 404).

    Raises:
        SDKTableMetadataError: 409 when the table is Solution-managed
            (deploy owns schema + policies; the raw lookup preserves the
            specific read-only error instead of a misleading 404).
    """
    solution_id = (
        await session.execute(select(Table.solution_id).where(Table.id == table_id))
    ).scalar_one_or_none()
    if solution_id is not None:
        raise SDKTableMetadataError(409, SOLUTION_MANAGED_MESSAGE)

    # NOTE: attribute access at call time (not a top-level from-import)
    # keeps the ``TableRepository`` seam patchable for both entry points.
    repo = tables_repo_module.TableRepository(session, org_id, is_superuser=True)
    return await repo.delete_table(table_id)


__all__ = [
    "SDKTableMetadataError",
    "create_sdk_table",
    "delete_sdk_table",
    "ensure_sdk_table_create_allowed",
    "list_sdk_tables",
]
