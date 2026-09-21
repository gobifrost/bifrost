"""Durable adapter for workspace git operations."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)


class GitOperationPayload(BaseModel):
    operation: str
    organization_id: UUID | None = None
    options: dict[str, Any] = Field(default_factory=dict)


async def run_git_operation(
    context: PlatformJobContext,
    payload: GitOperationPayload,
) -> dict:
    from src.scheduler.main import Scheduler

    await context.report(f"Running {payload.operation.replace('_', ' ')}", percent=5)
    data = {
        "type": payload.operation,
        "jobId": str(context.job_id),
        "orgId": str(payload.organization_id) if payload.organization_id else "",
        **payload.options,
    }
    completed = await Scheduler()._handle_git_operation(data)
    sync_result = data.get("_sync_result")
    if not completed:
        raise PlatformJobFailure(
            "git_operation_failed",
            f"{payload.operation.replace('_', ' ').title()} failed.",
            retryable=bool(sync_result and sync_result.get("retryable")),
            result={"sync_result": sync_result} if sync_result else None,
        )
    await context.report("Git operation complete", percent=100)
    result = {"operation": payload.operation}
    if sync_result is not None:
        result["sync_result"] = sync_result
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
