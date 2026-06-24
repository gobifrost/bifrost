"""Shared helpers for solution-scoped storage declarations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import ExecutionContext
from src.core.org_filter import resolve_target_org
from src.models.orm.applications import Application
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.repositories.tables import TableRepository


@dataclass(frozen=True)
class FileTier:
    name: Literal["solution", "org", "global"]
    scope: str
    organization_id: UUID | None
    solution_id: UUID | None


async def get_active_solution(db: AsyncSession, solution_id: UUID) -> Solution | None:
    solution = await db.get(Solution, solution_id)
    if solution is None or solution.status != "active":
        return None
    return solution


async def solution_allows_global(db: AsyncSession, solution_id: UUID) -> bool:
    solution = await db.get(Solution, solution_id)
    return bool(solution and solution.global_repo_access)


async def solution_declares_file_location(
    db: AsyncSession,
    solution_id: UUID,
    location: str,
) -> bool:
    result = await db.execute(
        select(SolutionFileLocation.id).where(
            SolutionFileLocation.solution_id == solution_id,
            SolutionFileLocation.location == location,
        )
    )
    return result.scalar_one_or_none() is not None


async def solution_declares_table_name(
    db: AsyncSession,
    solution_id: UUID,
    name: str,
) -> bool:
    result = await db.execute(
        select(Table.id).where(
            Table.solution_id == solution_id,
            Table.name == name,
        )
    )
    return result.scalar_one_or_none() is not None


async def solution_context_id(
    db: AsyncSession,
    ctx: ExecutionContext,
) -> UUID | None:
    """Resolve the active install id from request context.

    Auth populates ``ctx.solution_id`` for both ``?solution=`` and solution app
    calls via ``X-Bifrost-App``. The app-id fallback keeps older call sites and
    unit tests that construct contexts manually on the same resolver path.
    """
    if ctx.solution_id:
        try:
            return UUID(str(ctx.solution_id))
        except ValueError:
            return None

    if not ctx.app_id:
        return None
    try:
        app_uuid = UUID(str(ctx.app_id))
    except ValueError:
        return None

    return (
        await db.execute(
            select(Application.solution_id).where(Application.id == app_uuid)
        )
    ).scalar_one_or_none()


async def _resolve_solution_table_by_name(
    db: AsyncSession,
    ctx: ExecutionContext,
    name: str,
    target_org_id: UUID | None,
) -> Table | None:
    """Resolve a table name from solution context.

    Tier order:
    1. the solution-owned table for this install, when deployed under ``name``;
    2. for open solutions only, the ordinary org/global _repo cascade.

    The fallback table, when returned, is still a shared _repo table with
    ``solution_id IS NULL``. Callers that mutate documents must reject that case.
    """
    solution_id = await solution_context_id(db, ctx)
    if solution_id is None:
        return None

    solution = await get_active_solution(db, solution_id)
    if solution is None:
        return None

    own_stmt = select(Table).where(
        Table.name == name,
        Table.solution_id == solution_id,
    )
    if not ctx.user.is_superuser:
        own_stmt = own_stmt.where(
            or_(
                Table.organization_id == target_org_id,
                Table.organization_id.is_(None),
            )
        )
    own = (await db.execute(own_stmt)).scalar_one_or_none()
    if own is not None:
        return own

    if not solution.global_repo_access:
        return None

    repo = TableRepository(
        db,
        target_org_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_superuser,
        is_external=ctx.user.is_external,
    )
    return await repo.get_by_name(name)


async def file_read_tiers(
    db: AsyncSession,
    ctx: ExecutionContext,
    location: str,
    requested_scope: str | None,
) -> list[FileTier]:
    """Return candidate storage tiers for file read/list/exists operations."""
    if ctx.solution_id is None:
        org_id = _file_org_id(ctx, location, requested_scope)
        return [
            FileTier(
                "global" if org_id is None else "org",
                _storage_scope(org_id),
                org_id,
                None,
            )
        ]

    if location == "workspace":
        raise ValueError("workspace is not available in solution file context")

    solution_id = UUID(str(ctx.solution_id))
    solution = await db.get(Solution, solution_id)
    if solution is None:
        return []

    tiers = [
        FileTier(
            "solution",
            str(solution_id),
            solution.organization_id,
            solution_id,
        )
    ]
    if solution.global_repo_access:
        if solution.organization_id is not None:
            tiers.append(
                FileTier(
                    "org",
                    str(solution.organization_id),
                    solution.organization_id,
                    None,
                )
            )
        tiers.append(FileTier("global", "global", None, None))
    return tiers


def _file_org_id(
    ctx: ExecutionContext,
    location: str,
    requested_scope: str | None,
) -> UUID | None:
    if location == "workspace":
        return None
    return resolve_target_org(ctx.user, requested_scope, ctx.org_id)


def _storage_scope(org_id: UUID | None) -> str:
    return str(org_id) if org_id is not None else "global"
