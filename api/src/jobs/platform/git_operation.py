"""Durable, direct execution for workspace Git operations."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.config import get_settings
from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
    PlatformJobRequiresAction,
)
from src.models.contracts.github import WorkspaceSyncPlan
from src.services.github_config import get_github_config
from src.services.github_sync import GitHubSyncService


WORKSPACE_MUTATION_RESOURCE_LOCK_KEY = "workspace"


class GitOperationPayload(BaseModel):
    operation: Literal[
        "fetch", "status", "commit", "sync", "resolve", "discard", "abort_merge", "diff"
    ]
    organization_id: UUID | None = None
    options: dict[str, Any] = Field(default_factory=dict)


def _authenticated_clone_url(config: Any) -> str:
    """Build the authenticated GitHub remote without exposing it to callers."""
    repo = config.repo_url
    if repo.startswith("https://github.com/"):
        repo = repo.removeprefix("https://github.com/").removesuffix(".git")
    return f"https://x-access-token:{config.token}@github.com/{repo}.git"


async def _report(
    context: PlatformJobContext,
    phase: str,
    current: int = 0,
    total: int = 0,
) -> None:
    await context.report(phase, current=current, total=total or None)


async def dispatch_git_operation(
    service: GitHubSyncService,
    payload: GitOperationPayload,
    context: PlatformJobContext,
) -> dict[str, Any]:
    """Execute one typed operation using the shared PlatformJob transport."""
    options = payload.options
    if payload.operation == "fetch":
        result = await service.desktop_fetch(
            progress_fn=lambda phase, current=0, total=0: _report(
                context, phase, current, total
            )
        )
        if result.success:
            status = await service.desktop_status()
            return {
                **result.model_dump(mode="json"),
                "changed_files": [item.model_dump() for item in status.changed_files],
                "conflicts": [item.model_dump() for item in status.conflicts],
            }
        return result.model_dump(mode="json")
    if payload.operation == "status":
        return {"success": True, **(await service.desktop_status()).model_dump(mode="json")}
    if payload.operation == "commit":
        return (await service.desktop_commit(options.get("message", "Commit from Bifrost"))).model_dump(
            mode="json"
        )
    if payload.operation == "sync":
        retry_plan_payload = options.get("retry_plan")
        retry_plan = (
            WorkspaceSyncPlan.model_validate(retry_plan_payload)
            if retry_plan_payload is not None
            else None
        )
        return (
            await service.desktop_sync(
                confirm_deletes=bool(options.get("confirm_deletes", False)),
                retry_plan=retry_plan,
                progress_fn=lambda phase, current=0, total=0: _report(
                    context, phase, current, total
                ),
            )
        ).model_dump(mode="json")
    if payload.operation == "resolve":
        return (await service.desktop_resolve(options.get("resolutions", {}))).model_dump(mode="json")
    if payload.operation == "abort_merge":
        return (await service.desktop_abort_merge()).model_dump(mode="json")
    if payload.operation == "diff":
        return {"success": True, **(await service.desktop_diff(options["path"])).model_dump(mode="json")}
    if payload.operation == "discard":
        return (await service.desktop_discard(options.get("paths", []))).model_dump(mode="json")
    raise PlatformJobFailure("invalid_git_operation", "Unknown Git operation.")


async def run_git_operation(
    context: PlatformJobContext,
    payload: GitOperationPayload,
) -> dict:
    await _report(context, f"Running {payload.operation.replace('_', ' ')}", total=100)
    async with get_db_context() as db:
        config = await get_github_config(db, payload.organization_id)
        if config is None or not config.token or not config.repo_url:
            raise PlatformJobFailure("github_not_configured", "GitHub is not configured.")
        service = GitHubSyncService(
            db=db,
            repo_url=_authenticated_clone_url(config),
            branch=config.branch,
            settings=get_settings(),
        )
        result = await dispatch_git_operation(service, payload, context)

    if result.get("requires_action"):
        raise PlatformJobRequiresAction("Delete confirmation required", result)
    if not result.get("success", False):
        raise PlatformJobFailure(
            "git_operation_failed",
            result.get("error") or f"{payload.operation.replace('_', ' ').title()} failed.",
            retryable=bool(result.get("retryable")),
            result=result,
        )
    if payload.operation == "sync":
        from src.core.repo_dirty import clear_repo_dirty

        await clear_repo_dirty()
    await _report(context, "Git operation complete", current=100, total=100)
    return result


GIT_OPERATION_DEFINITION = PlatformJobDefinition(
    job_type="workspace.git",
    payload_version=1,
    payload_model=GitOperationPayload,
    handler=run_git_operation,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=512,
    ),
)
