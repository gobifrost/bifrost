from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.services.solutions.deploy_jobs import (
    ClaimedDeployJob,
    _delete_orphan_install,
    create_staged_deploy_job,
    execute_deploy_job,
)


@pytest.mark.asyncio
async def test_create_job_stages_encrypted_central_job(
    db_session, tmp_path, monkeypatch
):
    solution = Solution(slug="demo", name="Demo")
    db_session.add(solution)
    await db_session.flush()
    input_path = tmp_path / "input.zip"
    input_path.write_bytes(b"validated")
    write_path = AsyncMock(return_value=("a" * 64, len(b"validated")))
    publish = AsyncMock()
    monkeypatch.setattr(
        "src.services.solutions.deploy_jobs.SolutionDeployJobStorage.write_path",
        write_path,
    )
    monkeypatch.setattr(
        "src.services.platform_jobs.publish_platform_job_update",
        publish,
    )

    job = await create_staged_deploy_job(
        db_session,
        kind="deploy",
        install_id=solution.id,
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="admin@example.com",
        requested_by_name="Admin",
        options={"password": "never-plaintext", "config_values": {"key": "secret"}},
        input_path=input_path,
    )

    central = await db_session.get(PlatformJob, job.id)
    assert central is not None
    assert central.job_type == "solution.deploy"
    assert central.payload == {"protected": True}
    assert central.encrypted_payload is not None
    assert "never-plaintext" not in central.encrypted_payload
    assert "secret" not in central.encrypted_payload
    assert central.resource_lock_key == f"solution:{solution.id}"
    assert job.input_key == f"_solution_deploy_jobs/{job.id}/input.zip"
    assert job.input_sha256 == "a" * 64
    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_projection_is_not_executed_again(
    db_session,
    async_session_factory,
    monkeypatch,
):
    lease_token = uuid4()
    job = SolutionDeployJob(status="succeeded", kind="deploy")
    central = PlatformJob(
        id=job.id,
        job_type="solution.deploy",
        payload_version=1,
        payload={"protected": True},
        requested_by_user_id=str(uuid4()),
        requested_by_email="admin@example.com",
        requested_by_name="Admin",
        title="Deploy",
        status="running",
        lease_token=lease_token,
    )
    db_session.add_all([job, central])
    await db_session.commit()

    @asynccontextmanager
    async def test_db_context():
        async with async_session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(
        "src.services.solutions.deploy_jobs.get_db_context",
        test_db_context,
    )
    execute = AsyncMock()
    monkeypatch.setattr(
        "src.services.solutions.deploy_jobs._run_claimed_job",
        execute,
    )

    await execute_deploy_job(job.id, lease_token)

    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_repo_install_cleanup_commits_before_returning(monkeypatch):
    deploy_job_id = uuid4()
    install_id = uuid4()
    projection = SolutionDeployJob(
        id=deploy_job_id,
        install_id=install_id,
        status="running",
        kind="install_from_repo",
    )
    orphan = Solution(id=install_id, slug="failed-install", name="Failed install")

    class FakeDB:
        def __init__(self) -> None:
            self.flush = AsyncMock()
            self.delete = AsyncMock()

        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            if model is SolutionDeployJob and row_id == deploy_job_id:
                return projection
            if model is Solution and row_id == install_id:
                return orphan
            return None

    db = FakeDB()
    transaction_committed = False

    @asynccontextmanager
    async def fake_db_context():
        nonlocal transaction_committed
        yield db
        transaction_committed = True

    monkeypatch.setattr(
        "src.services.solutions.deploy_jobs.get_db_context",
        fake_db_context,
    )

    await _delete_orphan_install(
        ClaimedDeployJob(
            id=deploy_job_id,
            install_id=install_id,
            kind="install_from_repo",
            input_key=None,
            input_sha256=None,
            encrypted_options=None,
            lease_token=uuid4(),
        )
    )

    assert transaction_committed is True
    assert projection.install_id is None
    db.flush.assert_awaited_once()
    db.delete.assert_awaited_once_with(orphan)
