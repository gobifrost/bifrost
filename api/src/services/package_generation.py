"""The desired package generation, in PostgreSQL.

A package change is recorded in the same transaction as the requirements
update. Executors read the current generation before installing requirements
at boot, then apply every newer change in order, so no worker misses one.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.orm.package_changes import PackageChange
from src.services.work_dispatch import wake_pool_workers_after_commit


# Held until the recording transaction ends, so generations commit in order
# and an executor that has seen generation N never later finds an N-1.
_CHANGE_LOCK_KEY = "bifrost:package-changes"


async def record_package_change(
    db: AsyncSession,
    *,
    action: str,
    package: str | None,
    version: str | None,
    run_id: str,
) -> None:
    """Add a change to the caller's transaction; it is visible on commit,
    when executors are woken to apply it."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _CHANGE_LOCK_KEY}
    )
    db.add(PackageChange(action=action, package=package, version=version, run_id=run_id))
    await db.flush()
    wake_pool_workers_after_commit(db)


async def current_package_generation() -> int:
    async with get_db_context() as db:
        return int(await db.scalar(select(func.coalesce(func.max(PackageChange.id), 0))) or 0)


async def package_changes_after(generation: int) -> list[PackageChange]:
    async with get_db_context() as db:
        return list(
            await db.scalars(
                select(PackageChange)
                .where(PackageChange.id > generation)
                .order_by(PackageChange.id)
            )
        )


def change_message(change: PackageChange) -> dict[str, Any]:
    """The install consumer's message for a change."""
    return {
        "action": change.action,
        "package": change.package,
        "version": change.version,
        "run_id": change.run_id,
    }
