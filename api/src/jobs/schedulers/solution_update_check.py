"""Solution update-check scheduler.

For each git-connected Solution install, read the descriptor ``version:`` at the
repo's ref HEAD and compare (PEP-440) to the installed version. Stores
``update_available_version`` and emits ``solution.update_available`` the first
time an update appears, so the catalog can badge it and an operator can subscribe.

The version source is the descriptor, NOT git tags (a repo-wide tag can't version
N solutions in an omni-repo's subfolders). Runs every 6 hours.
"""
import logging
from typing import Any

from sqlalchemy import select

from src.core.database import get_db_context
from src.models.orm.solutions import Solution
from src.services.events.builtins import emit_solution_update_available
from src.services.solutions.update_check import (
    compute_update_available,
    fetch_remote_version,
)

logger = logging.getLogger(__name__)


async def check_solution_updates() -> dict[str, Any]:
    """Sweep git-connected installs for a newer descriptor version."""
    checked = 0
    updates_found = 0
    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(Solution).where(
                    Solution.git_connected.is_(True),
                    Solution.git_repo_url.is_not(None),
                )
            )
        ).scalars().all()
        for s in rows:
            checked += 1
            try:
                remote = await fetch_remote_version(
                    repo_url=s.git_repo_url, repo_subpath=s.repo_subpath, ref=s.git_ref
                )
                available = compute_update_available(installed=s.version, remote=remote)
                previous = s.update_available_version
                if available != previous:
                    s.update_available_version = available
                    if available is not None and previous is None:
                        await emit_solution_update_available(
                            solution_id=s.id,
                            slug=s.slug,
                            organization_id=s.organization_id,
                            installed_version=s.version,
                            available_version=available,
                        )
                if available is not None:
                    updates_found += 1
            except Exception:  # noqa: BLE001 - one bad install must not abort the sweep
                logger.exception("Update check failed for solution %s", s.id)
        await db.commit()
    logger.info(
        "Solution update check: %d checked, %d with updates", checked, updates_found
    )
    return {"checked": checked, "updates_found": updates_found}
