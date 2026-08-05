"""Durable platform job for reimporting the S3-backed workspace."""

from pydantic import BaseModel

from src.config import get_settings
from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobPolicy,
)


class WorkspaceReimportPayload(BaseModel):
    pass


async def run_workspace_reimport(
    context: PlatformJobContext,
    payload: WorkspaceReimportPayload,
) -> dict:
    from src.services.github_sync import GitHubSyncService

    await context.report("Reimporting workspace entities", percent=5)
    async with get_db_context() as db:
        service = GitHubSyncService(
            db=db,
            repo_url="unused://reimport-only",
            settings=get_settings(),
        )
        count = await service.reimport_from_repo()
    await context.report("Workspace reimport complete", percent=100)
    await context.log(
        "info",
        "workspace_reimport_completed",
        f"Reimported {count} entities from workspace storage",
    )
    return {
        "message": f"Reimported {count} entities from repository",
        "entities_imported": count,
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
