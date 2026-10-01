"""Workspace Git platform-job dispatch contracts."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.git_operation import (
    GitOperationPayload,
    dispatch_git_operation,
    run_git_operation,
)
from src.models.contracts.github import GitConnectRequest, SyncResult, WorkspaceSyncPlan


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
    monkeypatch.setattr("src.core.repo_dirty.clear_repo_dirty", AsyncMock())

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


@pytest.mark.asyncio
async def test_git_job_dispatches_reviewed_connect_through_the_existing_workspace_job() -> None:
    """First connection is another typed workspace.git operation, not a bespoke worker."""
    connect = AsyncMock(return_value=SyncResult(success=True))
    service = SimpleNamespace(desktop_connect=connect)
    context = SimpleNamespace(
        requested_by_user_id="requester",
        organization_id=None,
        report=AsyncMock(),
    )

    result = await dispatch_git_operation(
        service,
        GitOperationPayload(operation="connect", options={"request": GitConnectRequest(
            preview_token="preview", strategy="publish_local",
        ).model_dump(mode="json")}),
        context,
    )

    assert result["success"] is True
    assert connect.await_args.args[0].preview_token == "preview"
    assert connect.await_args.kwargs["requested_by_user_id"] == "requester"


@pytest.mark.asyncio
async def test_connect_job_revalidates_its_preview_and_persists_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable runner is the only place that marks a reviewed repo connected."""
    connect = AsyncMock(return_value=SyncResult(success=True))
    save = AsyncMock()

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    monkeypatch.setattr("src.jobs.platform.git_operation.get_db_context", db_context)
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_github_config",
        AsyncMock(return_value=SimpleNamespace(token="token", repo_url=None, branch="main")),
    )
    class ServiceFactory:
        load_connect_preview = AsyncMock(return_value=SimpleNamespace(
            repository_url="https://github.com/acme/workspace", branch="main",
        ))

        def __new__(cls, **_kwargs):
            return SimpleNamespace(desktop_connect=connect)

    monkeypatch.setattr(
        "src.jobs.platform.git_operation.GitHubSyncService",
        ServiceFactory,
    )
    monkeypatch.setattr("src.jobs.platform.git_operation.save_github_config", save)

    organization_id = uuid4()
    context = SimpleNamespace(
        job_id=uuid4(),
        organization_id=organization_id,
        requested_by_user_id="requester",
        requested_by_email="admin@example.com",
        report=AsyncMock(),
    )
    result = await run_git_operation(
        context,
        GitOperationPayload(
            operation="connect",
            options={"request": {"preview_token": "preview", "strategy": "publish_local"}},
        ),
    )

    assert result["success"] is True
    save.assert_awaited_once_with(
        db=ANY,
        org_id=organization_id,
        token="token",
        repo_url="https://github.com/acme/workspace",
        branch="main",
        updated_by="admin@example.com",
    )


PAT = "ghp_jobTestSecretToken0123456789"


async def _run_with_service(
    monkeypatch: pytest.MonkeyPatch, service: SimpleNamespace, operation: str,
) -> dict:
    captured: dict = {}

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    def build_service(**kwargs):
        captured.update(kwargs)
        return service

    monkeypatch.setattr("src.jobs.platform.git_operation.get_db_context", db_context)
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_github_config",
        AsyncMock(return_value=SimpleNamespace(token=PAT, repo_url="owner/repo", branch="main")),
    )
    monkeypatch.setattr("src.jobs.platform.git_operation.GitHubSyncService", build_service)
    context = SimpleNamespace(job_id=uuid4(), organization_id=uuid4(), report=AsyncMock())
    await run_git_operation(context, GitOperationPayload(operation=operation))  # type: ignore[arg-type]
    return captured


@pytest.mark.asyncio
async def test_git_job_keeps_the_token_out_of_the_remote_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.models.contracts.github import WorkingTreeStatus

    service = SimpleNamespace(desktop_status=AsyncMock(return_value=WorkingTreeStatus()))

    captured = await _run_with_service(monkeypatch, service, "status")

    assert captured["repo_url"] == "https://github.com/owner/repo.git"
    assert captured["token"] == PAT


@pytest.mark.asyncio
async def test_git_job_redacts_the_token_from_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.platform.base import PlatformJobFailure
    from src.models.contracts.github import CommitResult

    leaked = f"fatal: https://x-access-token:{PAT}@github.com/owner/repo.git denied"
    service = SimpleNamespace(
        desktop_commit=AsyncMock(return_value=CommitResult(success=False, error=leaked)),
    )

    with pytest.raises(PlatformJobFailure) as failure:
        await _run_with_service(monkeypatch, service, "commit")

    assert PAT not in failure.value.message
    assert PAT not in str(failure.value.result)
