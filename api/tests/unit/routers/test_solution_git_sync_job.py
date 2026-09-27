"""Shared PlatformJob transport for managed Solution Git updates."""

from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.models.contracts.solutions import SolutionUpdate
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solutions import Solution
from src.routers import solutions


class _NoRows:
    def scalars(self):  # noqa: ANN201
        return self

    def all(self):  # noqa: ANN201
        return []

    def scalar_one_or_none(self):  # noqa: ANN201
        return None


def test_git_connected_solution_sync_declares_shared_job_contract() -> None:
    route = next(
        route
        for route in solutions.router.routes
        if route.path == "/api/solutions/{solution_id}/sync"
    )

    assert route.status_code == 202
    assert route.response_model is PlatformJobAccepted


@pytest.mark.asyncio
async def test_git_connected_solution_sync_enqueues_one_notified_solution_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution_id = uuid4()
    requested_by = uuid4()
    solution = Solution(
        id=solution_id,
        slug="managed-git",
        name="Managed Git",
        git_connected=True,
        git_repo_url="https://example.test/managed-git.git",
    )
    job = SimpleNamespace(id=uuid4(), status="queued", notification_id=None)
    enqueue = AsyncMock(return_value=(job, False))
    notification = AsyncMock()
    publish = AsyncMock()
    db = SimpleNamespace(
        get=AsyncMock(return_value=solution),
        execute=AsyncMock(return_value=_NoRows()),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(solutions, "enqueue_platform_job", enqueue)
    monkeypatch.setattr(solutions, "ensure_platform_job_notification", notification)
    monkeypatch.setattr(solutions, "publish_platform_job_update", publish)

    response = Response()
    accepted = await solutions.sync_solution(
        solution_id,
        response,
        SimpleNamespace(db=db),
        SimpleNamespace(
            user_id=requested_by,
            email="admin@example.test",
            name="Admin",
        ),
    )

    assert accepted == PlatformJobAccepted(
        job_id=job.id,
        status="queued",
        reused=False,
        notification_id=None,
    )
    assert response.headers["Location"] == f"/api/platform-jobs/{job.id}"
    assert enqueue.await_args.args[1] is solutions.SOLUTION_GIT_SYNC_DEFINITION
    assert enqueue.await_args.kwargs == {
        "dedupe_key": str(solution_id),
        "resource_lock_key": f"solution:{solution_id}",
        "priority": 500,
        "organization_id": solution.organization_id,
        "requested_by_user_id": requested_by,
        "requested_by_email": "admin@example.test",
        "requested_by_name": "Admin",
        "resource_type": "solution",
        "resource_id": str(solution_id),
        "title": "Update Managed Git from Git",
        "action_url": f"/solutions/{solution_id}",
    }
    notification.assert_awaited_once_with(db, job)
    db.commit.assert_awaited_once()
    publish.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_git_sync_refuses_when_an_owned_app_sdk_update_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(
        id=uuid4(),
        slug="managed-git",
        name="Managed Git",
        git_connected=True,
        git_repo_url="https://example.test/managed-git.git",
    )

    class _Results:
        def scalars(self):  # noqa: ANN201
            return self

        def all(self):  # noqa: ANN201
            return [uuid4()]

        def scalar_one_or_none(self):  # noqa: ANN201
            return uuid4()

    db = SimpleNamespace(
        get=AsyncMock(return_value=solution),
        execute=AsyncMock(return_value=_Results()),
        commit=AsyncMock(),
    )
    queued_job = SimpleNamespace(id=uuid4(), status="queued", notification_id=uuid4())
    enqueue = AsyncMock(return_value=(queued_job, False))
    monkeypatch.setattr(solutions, "enqueue_platform_job", enqueue)

    with pytest.raises(HTTPException) as error:
        await solutions.sync_solution(
            solution.id,
            Response(),
            SimpleNamespace(db=db),
            SimpleNamespace(user_id=uuid4(), email="admin@example.test", name="Admin"),
        )

    assert error.value.status_code == 409
    assert "sdk update" in str(error.value.detail).lower()
    enqueue.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_refuses_an_active_queued_manual_deploy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(
        id=uuid4(),
        slug="managed-git",
        name="Managed Git",
        git_connected=False,
    )
    active_deploy = PlatformJob(
        id=uuid4(),
        job_type="solution.deploy",
        payload={},
        status="queued",
        resource_lock_key=f"solution:{solution.id}",
        requested_by_user_id=str(uuid4()),
        requested_by_email="admin@example.test",
        requested_by_name="Admin",
        title="Solution deploy",
    )

    class _Result:
        def scalar_one_or_none(self):
            return active_deploy.id

    db = SimpleNamespace(
        get=AsyncMock(return_value=solution),
        execute=AsyncMock(return_value=_Result()),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    @asynccontextmanager
    async def lock(_solution_id):  # noqa: ANN001
        yield

    monkeypatch.setattr("src.services.solutions.write_lock.solution_write_lock", lock)

    with pytest.raises(HTTPException) as error:
        await solutions.update_solution(
            solution.id,
            SolutionUpdate(
                git_connected=True,
                git_repo_url="https://example.test/managed-git.git",
                repo_subpath=None,
                git_ref="main",
            ),
            SimpleNamespace(db=db),
            SimpleNamespace(),
        )

    assert error.value.status_code == 409
    assert "deployment" in str(error.value.detail).lower()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_refuses_an_active_owned_app_sdk_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(
        id=uuid4(),
        slug="managed-git",
        name="Managed Git",
        git_connected=False,
    )

    class _NoActiveResult:
        def scalars(self):  # noqa: ANN201
            return self

        def all(self):  # noqa: ANN201
            return []

        def scalar_one_or_none(self):  # noqa: ANN201
            return None

    class _ActiveSdkUpdateResult(_NoActiveResult):
        def scalar_one_or_none(self):  # noqa: ANN201
            return uuid4()

    class _OwnedAppResult(_NoActiveResult):
        def all(self):  # noqa: ANN201
            return [uuid4()]

    async def execute(statement, *_args, **_kwargs):  # noqa: ANN001, ANN202
        parameters = statement.compile().params
        if "application.sdk_update" in parameters.values():
            return _ActiveSdkUpdateResult()
        if "solution_id_1" in parameters:
            return _OwnedAppResult()
        return _NoActiveResult()

    db = SimpleNamespace(
        get=AsyncMock(return_value=solution),
        execute=AsyncMock(side_effect=execute),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    @asynccontextmanager
    async def lock(_solution_id):  # noqa: ANN001
        yield

    monkeypatch.setattr("src.services.solutions.write_lock.solution_write_lock", lock)
    monkeypatch.setattr(solutions, "_validate_solution_git_connection_source", AsyncMock())

    with pytest.raises(HTTPException) as error:
        await solutions.update_solution(
            solution.id,
            SolutionUpdate(
                git_connected=True,
                git_repo_url="https://example.test/managed-git.git",
                repo_subpath=None,
                git_ref="main",
            ),
            SimpleNamespace(db=db),
            SimpleNamespace(),
        )

    assert error.value.status_code == 409
    assert "sdk update" in str(error.value.detail).lower()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_keeps_install_disconnected_when_git_source_cannot_be_cloned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(
        id=uuid4(),
        slug="managed-git",
        name="Managed Git",
        git_connected=False,
    )

    class _Result:
        def scalars(self):  # noqa: ANN201
            return self

        def all(self):  # noqa: ANN201
            return []

        def scalar_one_or_none(self):
            return None

    db = SimpleNamespace(
        get=AsyncMock(return_value=solution),
        execute=AsyncMock(return_value=_Result()),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    @asynccontextmanager
    async def lock(_solution_id):  # noqa: ANN001
        yield

    monkeypatch.setattr("src.services.solutions.write_lock.solution_write_lock", lock)
    monkeypatch.setattr(
        "src.services.solutions.git_sync.clone_repo_to_dir",
        AsyncMock(side_effect=RuntimeError("network unavailable")),
    )

    with pytest.raises(HTTPException) as error:
        await solutions.update_solution(
            solution.id,
            SolutionUpdate(
                git_connected=True,
                git_repo_url="https://example.test/managed-git.git",
                repo_subpath=None,
                git_ref="main",
            ),
            SimpleNamespace(db=db),
            SimpleNamespace(),
        )

    assert error.value.status_code == 422
    assert solution.git_connected is False
    db.commit.assert_not_awaited()
