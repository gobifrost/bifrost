"""Shared helpers for solution-scoped storage declarations."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table


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
