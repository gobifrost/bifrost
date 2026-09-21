"""Workspace Git platform-job dispatch contracts."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.git_operation import GitOperationPayload, run_git_operation
from src.models.contracts.github import SyncResult, WorkspaceSyncPlan


@pytest.mark.asyncio
async def test_git_job_calls_sync_service_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace Git work runs in the durable job, not through Scheduler."""
    sync = AsyncMock(return_value=SyncResult(success=True))

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_db_context", db_context,
    )
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_github_config",
        AsyncMock(
            return_value=SimpleNamespace(
                token="token", repo_url="owner/repo", branch="main"
            )
        ),
    )
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.GitHubSyncService",
        lambda **_kwargs: SimpleNamespace(desktop_sync=sync),
    )

    context = SimpleNamespace(
        job_id=uuid4(),
        organization_id=uuid4(),
        report=AsyncMock(),
    )
    result = await run_git_operation(context, GitOperationPayload(operation="sync"))

    assert result["success"] is True
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_git_job_passes_server_validated_retry_plan_to_sync_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A publication retry reaches desktop_sync as the exact durable plan."""
    plan = WorkspaceSyncPlan(
        merge_sha="a" * 40,
        workspace_fingerprint="b" * 64,
        db_applied=True,
    )
    sync = AsyncMock(return_value=SyncResult(success=True))

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    monkeypatch.setattr("src.jobs.platform.git_operation.get_db_context", db_context)
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_github_config",
        AsyncMock(return_value=SimpleNamespace(token="token", repo_url="owner/repo", branch="main")),
    )
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.GitHubSyncService",
        lambda **_kwargs: SimpleNamespace(desktop_sync=sync),
    )
    monkeypatch.setattr(
        "src.core.repo_dirty.clear_repo_dirty", AsyncMock(),
    )

    context = SimpleNamespace(job_id=uuid4(), organization_id=uuid4(), report=AsyncMock())
    await run_git_operation(
        context,
        GitOperationPayload(
            operation="sync", options={"retry_plan": plan.model_dump(mode="json")}
        ),
    )

    assert sync.await_args.kwargs["retry_plan"] == plan
