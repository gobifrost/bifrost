"""Persist solution runtime file-location declarations."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from uuid import UUID

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.file_policies_seed import make_seed_admin_bypass_file
from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution


def normalize_file_locations(
    locations: Iterable[str],
    *,
    make_error: Callable[[str], Exception] = ValueError,
) -> list[str]:
    from shared.file_paths import validate_location_name

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in locations:
        location = str(raw).strip()
        if not location:
            continue
        try:
            validate_location_name(location)
        except ValueError as exc:
            raise make_error(str(exc)) from exc
        if location == "workspace":
            raise make_error("reserved file location 'workspace' cannot be declared")
        if location in seen:
            raise make_error(f"duplicate file location '{location}' in solution bundle")
        seen.add(location)
        normalized.append(location)
    return normalized


async def reconcile_solution_file_locations(
    db: AsyncSession,
    solution_id: UUID,
    locations: Iterable[str],
    *,
    make_error: Callable[[str], Exception] = ValueError,
) -> list[str]:
    declared = normalize_file_locations(locations, make_error=make_error)
    organization_id = (
        await db.execute(
            select(Solution.organization_id).where(Solution.id == solution_id)
        )
    ).scalar_one_or_none()
    existing = set(
        (
            await db.execute(
                select(SolutionFileLocation.location).where(
                    SolutionFileLocation.solution_id == solution_id
                )
            )
        )
        .scalars()
        .all()
    )

    for position, location in enumerate(declared):
        if location in existing:
            await db.execute(
                update(SolutionFileLocation)
                .where(
                    SolutionFileLocation.solution_id == solution_id,
                    SolutionFileLocation.location == location,
                )
                .values(position=position)
            )
        else:
            await db.execute(
                insert(SolutionFileLocation).values(
                    solution_id=solution_id,
                    location=location,
                    position=position,
                )
            )
        existing_policy = (
            await db.execute(
                select(FilePolicy.id).where(
                    FilePolicy.solution_id == solution_id,
                    FilePolicy.location == location,
                    FilePolicy.path == "",
                )
            )
        ).scalar_one_or_none()
        if existing_policy is None:
            await db.execute(
                insert(FilePolicy).values(
                    organization_id=organization_id,
                    solution_id=solution_id,
                    location=location,
                    path="",
                    policies=make_seed_admin_bypass_file(),
                )
            )

    stale = existing - set(declared)
    for location in sorted(stale):
        has_files = (
            await db.execute(
                select(FileMetadata.id)
                .where(
                    FileMetadata.solution_id == solution_id,
                    FileMetadata.location == location,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if has_files is not None:
            raise make_error(
                f"cannot remove file location '{location}' while files still exist"
            )

    if stale:
        await db.execute(
            delete(FilePolicy).where(
                FilePolicy.solution_id == solution_id,
                FilePolicy.location.in_(stale),
            )
        )
        await db.execute(
            delete(SolutionFileLocation).where(
                SolutionFileLocation.solution_id == solution_id,
                SolutionFileLocation.location.in_(stale),
            )
        )

    return declared
