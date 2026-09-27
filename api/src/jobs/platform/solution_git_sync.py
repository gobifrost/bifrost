"""Durable managed-Solution Git update handler."""

from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.orm.solutions import Solution

logger = logging.getLogger(__name__)


class SolutionGitSyncPayload(BaseModel):
    solution_id: UUID


async def run_solution_git_sync(
    context: PlatformJobContext,
    payload: SolutionGitSyncPayload,
) -> dict:
    """Pull one connected Solution through its existing single-writer sync."""
    from src.services.solutions.deploy import SolutionFinalizeIncomplete
    from src.services.solutions.git_sync import NotASolutionWorkspace, sync

    await context.report("Loading connected Solution", percent=5)
    async with get_db_context() as db:
        solution = await db.get(Solution, payload.solution_id)
        if solution is None:
            raise PlatformJobFailure("solution_not_found", "Solution not found.")
        if not solution.git_connected:
            raise PlatformJobFailure(
                "solution_not_git_connected",
                "This install is not git-connected; use deploy instead.",
            )
        if not solution.git_repo_url:
            raise PlatformJobFailure(
                "solution_git_remote_missing",
                "This git-connected install has no git_repo_url to pull from.",
            )

        await context.report("Pulling Solution repository", percent=15)
        try:
            synced = await sync(db, solution)
        except NotASolutionWorkspace as exc:
            raise PlatformJobFailure("invalid_solution_workspace", str(exc)) from exc
        except SolutionFinalizeIncomplete as exc:
            raise PlatformJobFailure(
                "solution_git_finalize_incomplete",
                "Solution update committed but source storage was unavailable after "
                "retries. Retry the Git update to complete it.",
                retryable=True,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - job records the safe failure
            logger.exception(
                "Managed Solution Git sync failed",
                extra={"solution_id": str(payload.solution_id)},
            )
            raise PlatformJobFailure(
                "solution_git_sync_failed",
                "Solution update from Git failed; correct the repository and retry.",
                retryable=True,
            ) from exc

        if not synced:
            raise PlatformJobFailure(
                "solution_git_sync_deferred",
                "Another Solution writer is active; retrying the Git update.",
                retryable=True,
            )

        if solution.update_available_version is not None:
            solution.update_available_version = None
            await db.commit()

    await context.report("Solution update complete", percent=100)
    await context.log(
        "info",
        "solution_git_sync_completed",
        f"Solution Git update {payload.solution_id} completed",
    )
    return {"solution_id": str(payload.solution_id), "status": "synced"}


SOLUTION_GIT_SYNC_DEFINITION = PlatformJobDefinition(
    job_type="solution.git_sync",
    payload_version=1,
    payload_model=SolutionGitSyncPayload,
    handler=run_solution_git_sync,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        max_concurrency=1,
        retry_on_runner_loss=True,
        retry_on_failure=True,
        min_memory_headroom_mb=512,
        allow_running_cancellation=True,
    ),
)
