"""Read-only enforcement for solution-managed entities.

A solution-managed entity (``solution_id IS NOT NULL``) has exactly one writer:
the deploy path (or git auto-pull). Every other mutation surface — REST routers,
MCP tools, CLI mutation commands — must refuse to change it (success-criteria
§3.2, criterion 6). Routers call :func:`assert_not_solution_managed` right after
loading the target row, before applying any change.

The instance still owns the few things that cannot be portable — OAuth token
mappings and secret config *values* (criterion 7). Those live on their own
records (OAuthToken / Config), not on the managed entity, so they simply never
call this guard; the guard is about the portable entity itself.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SOLUTION_MANAGED_MESSAGE = (
    "Solution-managed entities can only be managed by deployment methods."
)


def is_solution_managed(entity: Any) -> bool:
    """True if ``entity`` carries a non-null ``solution_id``."""
    return getattr(entity, "solution_id", None) is not None


def assert_not_solution_managed(entity: Any) -> None:
    """Raise HTTP 409 with the locked message if ``entity`` is solution-managed.

    No-op for ad-hoc ``_repo/`` entities (``solution_id`` None or absent), so
    existing mutation paths are unchanged for non-solution entities.
    """
    if is_solution_managed(entity):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=SOLUTION_MANAGED_MESSAGE,
        )


class SolutionManagedWriteError(Exception):
    """A flush would mutate or delete a solution-managed entity outside deploy.

    Raised by the before_flush backstop (install_solution_write_guard). Deploy
    writes via Core update()/insert() statements, which do NOT go through the
    ORM unit-of-work, so this never fires for deployment — only for ORM-object
    mutations (routers, MCP tools, anything that loads a row and edits it).
    """


# Models whose instances are solution-managed when solution_id is set. Other
# ORM classes never carry solution_id and are skipped cheaply.
def _instance_is_managed(obj: Any) -> bool:
    return getattr(obj, "solution_id", None) is not None


def install_solution_write_guard() -> None:
    """Install a session-wide before_flush backstop enforcing read-only.

    Defense in depth for criterion 6: even if a mutation surface forgets the
    explicit guard (e.g. an old direct-ORM MCP tool, or a secondary endpoint),
    a flush that has a solution-managed entity in ``session.dirty`` or
    ``session.deleted`` is rejected. Idempotent.
    """
    from sqlalchemy import event
    from sqlalchemy.orm import Session as _SyncSession

    if getattr(install_solution_write_guard, "_installed", False):
        return

    @event.listens_for(_SyncSession, "before_flush")
    def _before_flush(session, _flush_context, _instances):  # noqa: ANN001
        for obj in list(session.dirty):
            if session.is_modified(obj, include_collections=False) and _instance_is_managed(obj):
                raise SolutionManagedWriteError(SOLUTION_MANAGED_MESSAGE)
        for obj in list(session.deleted):
            if _instance_is_managed(obj):
                raise SolutionManagedWriteError(SOLUTION_MANAGED_MESSAGE)

    install_solution_write_guard._installed = True  # type: ignore[attr-defined]


async def assert_entity_id_not_solution_managed(
    db: AsyncSession, model: type, entity_id: UUID
) -> None:
    """Guard by id with a RAW lookup (no cascade scope).

    Repository ``get()`` now filters out solution-managed rows (cascade is
    _repo/-only), so a router that wants to return the specific read-only error
    — rather than a misleading 404 — must look the row up directly. A missing
    row is left alone (the caller's own not-found handling applies).
    """
    solution_id = (
        await db.execute(select(model.solution_id).where(model.id == entity_id))  # type: ignore[attr-defined]
    ).scalar_one_or_none()
    if solution_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=SOLUTION_MANAGED_MESSAGE,
        )
