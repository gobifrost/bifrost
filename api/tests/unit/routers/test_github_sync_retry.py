"""Production request wiring for durable workspace-sync publication retries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.github import SyncRequest, SyncResult, WorkspaceSyncPlan
from src.models.orm.platform_jobs import PlatformJob
from src.routers import github


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
