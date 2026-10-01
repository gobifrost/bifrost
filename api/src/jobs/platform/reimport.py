"""Durable platform job for reimporting the S3-backed workspace."""

from pydantic import BaseModel

from src.config import get_settings
from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)


class WorkspaceReimportPayload(BaseModel):
    pass


async def run_workspace_reimport(
    context: PlatformJobContext,
    payload: WorkspaceReimportPayload,
) -> dict:
    from src.services.github_sync import GitHubSyncService, WorkspaceSourceMissing

    await context.report("Reimporting workspace entities", percent=5)
    async with get_db_context() as db:
        service = GitHubSyncService(
            db=db,
            repo_url="unused://reimport-only",
            settings=get_settings(),
        )
        try:
            result = await service.reimport_from_repo()
        except WorkspaceSourceMissing as exc:
            raise PlatformJobFailure("workspace_source_missing", str(exc)) from exc
    await context.report("Workspace reimport complete", percent=100)
    await context.log(
        "info",
        "workspace_reimport_completed",
        f"Reimported {result.entities_imported} entities from workspace storage",
    )
    message = f"Reimported {result.entities_imported} entities from repository"
    if result.pending_deletes:
        names = ", ".join(change.name for change in result.pending_deletes[:5])
        more = len(result.pending_deletes) - 5
        if more > 0:
            names += f" and {more} more"
        message += (
            f". {len(result.pending_deletes)} entities are no longer in workspace "
            f"storage and were kept: {names}. Reimport doesn't delete entities. "
            "To remove them, run a git sync and confirm the deletions."
        )
    return {
        "message": message,
        "entities_imported": result.entities_imported,
        "pending_deletes": [
            change.model_dump(mode="json") for change in result.pending_deletes
        ],
    }


WORKSPACE_REIMPORT_DEFINITION = PlatformJobDefinition(
    job_type="workspace.reimport",
    payload_version=1,
    payload_model=WorkspaceReimportPayload,
    handler=run_workspace_reimport,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=512,
    ),
)
