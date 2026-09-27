from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solutions import Solution
from src.routers.solutions import (
    _enqueue_solution_deploy_job,
    _run_deploy_job,
)


@pytest.mark.asyncio
async def test_deploy_job_is_staged_as_encrypted_central_job(
    db_session,
    tmp_path,
    monkeypatch,
):
    sol = Solution(slug="demo-memory-profile", name="Demo memory profile")
    db_session.add(sol)
    await db_session.flush()
    path = tmp_path / "deploy.zip"
    path.write_bytes(b"validated")
    monkeypatch.setattr(
        "src.routers.solutions.SolutionDeployJobStorage.write_path",
        AsyncMock(return_value=("a" * 64, len(b"validated"))),
    )
    monkeypatch.setattr(
        "src.routers.solutions.publish_platform_job_update", AsyncMock()
    )

    projection = await _enqueue_solution_deploy_job(
        db_session,
        kind="deploy",
        install_id=sol.id,
        organization_id=None,
        options={"force": True, "password": "not-plaintext"},
        requested_by_user_id=uuid4(),
        requested_by_email="admin@example.com",
        requested_by_name="Admin",
        input_path=path,
    )
    central = await db_session.get(PlatformJob, projection.id)
    assert central is not None
    assert central.id == projection.id
    assert central.job_type == "solution.deploy"
    assert central.payload == {"protected": True}
    assert central.encrypted_payload is not None
    assert "not-plaintext" not in central.encrypted_payload
    assert central.resource_lock_key == f"solution:{sol.id}"


@pytest.mark.asyncio
async def test_deploy_job_passes_memory_profile_key_to_platform_job(
    db_session,
    tmp_path,
    monkeypatch,
):
    sol = Solution(slug="demo", name="Demo")
    db_session.add(sol)
    await db_session.flush()
    path = tmp_path / "deploy.zip"
    path.write_bytes(b"validated")
    enqueue = AsyncMock(return_value=(PlatformJob(id=uuid4(), status="queued"), False))
    monkeypatch.setattr("src.routers.solutions.enqueue_platform_job", enqueue)
    monkeypatch.setattr(
        "src.routers.solutions.SolutionDeployJobStorage.write_path",
        AsyncMock(return_value=("a" * 64, len(b"validated"))),
    )
    monkeypatch.setattr(
        "src.routers.solutions.publish_platform_job_update", AsyncMock()
    )

    await _enqueue_solution_deploy_job(
        db_session,
        kind="deploy",
        install_id=sol.id,
        organization_id=None,
        options={"force": True},
        requested_by_user_id=uuid4(),
        requested_by_email="admin@example.com",
        requested_by_name="Admin",
        input_path=path,
        memory_profile_key="solution.deploy.memory.v1:test",
    )

    assert (
        enqueue.await_args.kwargs["memory_profile_key"]
        == "solution.deploy.memory.v1:test"
    )


@pytest.mark.asyncio
async def test_run_deploy_job_does_not_start_after_job_is_terminal(
    tmp_path, monkeypatch
):
    job = SolutionDeployJob(id=uuid4(), install_id=None, status="failed")

    class FakeDB:
        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            assert model is SolutionDeployJob
            assert row_id == job.id
            return job

    @asynccontextmanager
    async def fake_db_context():
        yield FakeDB()

    from src.core import database
    from src.services.solutions import zip_install

    deploy = AsyncMock()
    monkeypatch.setattr(database, "get_db_context", fake_db_context)
    monkeypatch.setattr(zip_install, "deploy_zip_to_solution_path", deploy)
    zip_path = tmp_path / "deploy.zip"
    zip_path.write_bytes(b"not used")

    await _run_deploy_job(job.id, uuid4(), zip_path, force=False)

    deploy.assert_not_awaited()
    assert not zip_path.exists()


@pytest.mark.asyncio
async def test_queued_manual_deploy_rechecks_git_connection_inside_write_lock(
    tmp_path, monkeypatch
):
    """Connecting after enqueue must stop the stale manual deploy before writes."""
    job = SolutionDeployJob(id=uuid4(), install_id=None, status="queued")
    solution = Solution(
        id=uuid4(),
        slug="managed-git",
        name="Managed Git",
        git_connected=True,
        git_repo_url="https://example.test/managed-git.git",
    )

    class FakeDB:
        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            if model is SolutionDeployJob:
                assert row_id == job.id
                return job
            if model is Solution:
                assert row_id == solution.id
                return solution
            return None

        async def commit(self) -> None:
            pass

    @asynccontextmanager
    async def fake_db_context():
        yield FakeDB()

    @asynccontextmanager
    async def fake_write_lock(_solution_id):  # noqa: ANN001
        yield

    from src.core import database
    from src.services.solutions import zip_install
    from src.services.solutions import write_lock

    deploy = AsyncMock()
    monkeypatch.setattr(database, "get_db_context", fake_db_context)
    monkeypatch.setattr(write_lock, "solution_write_lock", fake_write_lock)
    monkeypatch.setattr(zip_install, "deploy_zip_to_solution_path", deploy)
    zip_path = tmp_path / "deploy.zip"
    zip_path.write_bytes(b"not used")

    await _run_deploy_job(job.id, solution.id, zip_path, force=False)

    deploy.assert_not_awaited()
    assert job.status == "failed"
    assert "git-connected" in (job.error or "").lower()
