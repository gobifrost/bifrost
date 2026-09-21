"""Scheduler dispatch contracts for workspace publication retries."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.models.contracts.github import SyncResult, WorkspaceSyncPlan
from src.scheduler import main as scheduler_main


@pytest.mark.asyncio
async def test_git_sync_dispatches_server_validated_retry_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued retry reaches desktop_sync as a publication-only plan."""
    plan = WorkspaceSyncPlan(
        merge_sha="a" * 40,
        workspace_fingerprint="b" * 64,
        db_applied=True,
    )
    sync = AsyncMock(return_value=SyncResult(success=True))

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    monkeypatch.setattr(scheduler_main, "get_db_context", db_context)
    monkeypatch.setattr(scheduler_main, "publish_git_op_completed", AsyncMock())
    monkeypatch.setattr(
        "src.services.github_config.get_github_config",
        AsyncMock(return_value=SimpleNamespace(token="token", repo_url="owner/repo", branch="main")),
    )
    monkeypatch.setattr(
        "src.services.github_sync.GitHubSyncService",
        lambda **_kwargs: SimpleNamespace(desktop_sync=sync),
    )

    result = await scheduler_main.Scheduler()._handle_git_operation({
        "type": "git_sync",
        "jobId": "retry-job",
        "orgId": "org-id",
        "retry_plan": plan.model_dump(mode="json"),
    })

    assert result is True
    assert sync.await_args.kwargs["retry_plan"] == plan
