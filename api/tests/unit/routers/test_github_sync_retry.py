"""Production request wiring for durable workspace-sync publication retries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from src.models.contracts.github import (
    GitConnectItem,
    GitConnectRequest,
    SyncRequest,
    SyncResult,
    WorkspaceSyncPlan,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.models.orm.platform_jobs import PlatformJob
from src.routers import github


def test_git_operation_routes_explicitly_return_accepted_platform_jobs() -> None:
    """Every Git enqueue endpoint documents the shared async HTTP contract."""
    operation_paths = {
        "/api/github/fetch",
        "/api/github/commit",
        "/api/github/sync",
        "/api/github/abort-merge",
        "/api/github/changes",
        "/api/github/resolve",
        "/api/github/diff",
        "/api/github/discard",
    }
    routes = {route.path: route for route in github.router.routes}

    assert set(routes).issuperset(operation_paths)
    for path in operation_paths:
        route = routes[path]
        assert route.status_code == 202
        assert route.response_model is PlatformJobAccepted

    assert routes["/api/github/connect"].status_code == 202
    assert routes["/api/github/connect"].response_model is PlatformJobAccepted


def test_legacy_configure_route_cannot_bypass_reviewed_first_connection() -> None:
    """A repository can only become configured through preview + workspace.git."""
    routes = {route.path for route in github.router.routes}

    assert "/api/github/configure" not in routes


@pytest.mark.asyncio
async def test_connect_refuses_unconfirmed_destructive_remote_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reviewed local-only path cannot be discarded just by choosing the strategy."""
    org_id = uuid4()
    user_id = uuid4()
    monkeypatch.setattr(
        github,
        "get_github_config",
        AsyncMock(return_value=SimpleNamespace(token="token", repo_url=None)),
    )
    monkeypatch.setattr(
        github.GitHubSyncService,
        "load_connect_preview",
        AsyncMock(return_value=SimpleNamespace(items=[GitConnectItem(
            path="modules/local.py", classification="local_only", local_sha256="local",
        )])),
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(github, "_enqueue_git_operation", enqueue)

    with pytest.raises(HTTPException) as error:
        await github.enqueue_git_connect(
            body=GitConnectRequest(
                preview_token="preview", strategy="start_from_remote",
            ),
            response=Response(),
            ctx=SimpleNamespace(org_id=org_id),
            user=SimpleNamespace(user_id=user_id, email="admin@example.com"),
            db=_Db(SimpleNamespace()),
        )

    assert error.value.status_code == 422
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_enqueue_sets_shared_platform_job_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted first-connect jobs use the shared notification and Location contract."""
    job = SimpleNamespace(id=uuid4(), status="queued", notification_id=None)
    enqueue = AsyncMock(return_value=(job, False))
    notification = AsyncMock()
    monkeypatch.setattr(github, "enqueue_platform_job", enqueue)
    monkeypatch.setattr(github, "ensure_platform_job_notification", notification)
    monkeypatch.setattr(github, "publish_platform_job_update", AsyncMock())
    response = Response()

    accepted = await github._enqueue_git_operation(
        _Db(SimpleNamespace()),
        operation="connect",
        organization_id=None,
        user=SimpleNamespace(user_id=uuid4(), email="admin@example.com"),
        job_id=job.id,
        response=response,
        ensure_notification=True,
    )

    assert accepted.job_id == job.id
    assert response.headers["Location"] == f"/api/platform-jobs/{job.id}"
    notification.assert_awaited_once()


class _Db:
    def __init__(self, job: PlatformJob) -> None:
        self.job = job

    async def get(self, model, job_id):
        assert model is PlatformJob
        assert job_id == self.job.id
        return self.job

    async def commit(self) -> None:
        pass


@pytest.mark.asyncio
async def test_sync_retry_loads_the_callers_durable_publication_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public request never trusts a client assertion that DB apply happened."""
    org_id = uuid4()
    user_id = uuid4()
    plan = WorkspaceSyncPlan(
        merge_sha="a" * 40,
        workspace_fingerprint="b" * 64,
        db_applied=True,
    )
    previous_job = SimpleNamespace(
        id=uuid4(),
        organization_id=org_id,
        requested_by_user_id=str(user_id),
        job_type="workspace.git",
        status="failed",
        result=SyncResult(retryable=True, retry_plan=plan).model_dump(mode="json"),
    )
    job = SimpleNamespace(id=uuid4(), status="queued", notification_id=None)
    enqueue = AsyncMock(return_value=(job, False))
    monkeypatch.setattr(
        github,
        "get_github_config",
        AsyncMock(return_value=SimpleNamespace(token="token", repo_url="owner/repo")),
    )
    monkeypatch.setattr(github, "enqueue_platform_job", enqueue)
    monkeypatch.setattr(github, "publish_platform_job_update", AsyncMock())

    response = await github.git_sync(
        ctx=SimpleNamespace(org_id=org_id),
        user=SimpleNamespace(user_id=user_id, email="admin@example.com"),
        db=_Db(previous_job),
        request=SyncRequest(retry_job_id=previous_job.id),
    )

    assert response.job_id == job.id
    assert enqueue.await_args.args[2].options["retry_plan"] == plan.model_dump(mode="json")


@pytest.mark.asyncio
async def test_sync_retry_rejects_another_callers_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry job id is an opaque capability only for its original caller."""
    org_id = uuid4()
    previous_job = SimpleNamespace(
        id=uuid4(),
        organization_id=org_id,
        requested_by_user_id=str(uuid4()),
        job_type="workspace.git",
        status="failed",
        result={},
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(
        github,
        "get_github_config",
        AsyncMock(return_value=SimpleNamespace(token="token", repo_url="owner/repo")),
    )
    monkeypatch.setattr(github, "enqueue_platform_job", enqueue)

    with pytest.raises(HTTPException) as error:
        await github.git_sync(
            ctx=SimpleNamespace(org_id=org_id),
            user=SimpleNamespace(user_id=uuid4(), email="admin@example.com"),
            db=_Db(previous_job),
            request=SyncRequest(retry_job_id=previous_job.id),
        )

    assert error.value.status_code == 404
    enqueue.assert_not_awaited()
