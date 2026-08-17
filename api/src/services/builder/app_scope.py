"""Exact-``solution_id`` resource resolution for the Solution app runtime.

Every other resolution path in Bifrost cascades: a name is looked up in the
caller's organization and then falls back to the global row (see
``api/src/repositories/README.md``). **The app runtime must not do that.**

Cascade-by-name would let a declared table/config name resolve onto an
organization or global row the launching user happens to be able to read,
turning a name collision into unintended data exposure — the generated app
chooses the name, and the owner cannot audit every org row it might land on.
So these helpers filter ``solution_id == principal.solution_id`` exactly: no
organization fallback, no global fallback, no ``_repo`` fallback. An
undeclared or unmatched resource returns ``None`` and the caller answers 404.
Nothing here ever auto-creates a row.

The seal has exactly one planned loosening, deferred past the POC: a
per-Solution ``shared_data_access`` flag, default off and settable only by an
administrator during promotion review, which would let a Solution's runtime
reach declared organization/global tables and files with the actor's normal
policies as the gate. Until that flag lands, exact-match is unconditional —
do not add a fallback here for any other reason.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.app_actor import SolutionAppPrincipal
from src.models.orm.config import Config
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.tables import Table


async def resolve_solution_table(
    db: AsyncSession, principal: SolutionAppPrincipal, name: str
) -> Table | None:
    """Resolve a table by name within the principal's Solution only."""
    return (
        await db.execute(
            select(Table).where(
                Table.solution_id == principal.solution_id,
                Table.name == name,
            )
        )
    ).scalar_one_or_none()


async def resolve_solution_file_location(
    db: AsyncSession, principal: SolutionAppPrincipal, location: str
) -> SolutionFileLocation | None:
    """Resolve a declared file location within the principal's Solution only."""
    return (
        await db.execute(
            select(SolutionFileLocation).where(
                SolutionFileLocation.solution_id == principal.solution_id,
                SolutionFileLocation.location == location,
            )
        )
    ).scalar_one_or_none()


async def resolve_solution_config(
    db: AsyncSession, principal: SolutionAppPrincipal, key: str
) -> Config | None:
    """Resolve a config value by key within the principal's Solution only."""
    return (
        await db.execute(
            select(Config).where(
                Config.solution_id == principal.solution_id,
                Config.key == key,
            )
        )
    ).scalar_one_or_none()
