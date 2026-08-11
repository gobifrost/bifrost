from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.solution_deploy import (
    SolutionDeployPayload,
    run_solution_deploy,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob


@pytest.mark.asyncio
async def test_failed_projection_commits_before_platform_job_failure(monkeypatch):
    deploy_job_id = uuid4()
    install_id = uuid4()
    lease_token = uuid4()
    projection = SolutionDeployJob(
        id=deploy_job_id,
        install_id=install_id,
        status="failed",
        error="manifest invalid",
    )

    class FakeDB:
        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            if model is SolutionDeployJob and row_id == deploy_job_id:
                return projection
            return None

    db = FakeDB()
    transaction_committed = False

    @asynccontextmanager
    async def fake_db_context():
        nonlocal transaction_committed
        yield db
        transaction_committed = True

    execute = AsyncMock()
    monkeypatch.setattr(
        "src.services.solutions.deploy_jobs.execute_deploy_job",
        execute,
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.get_db_context", fake_db_context
    )
    context = AsyncMock()
    context.lease_token = lease_token

    with pytest.raises(PlatformJobFailure, match="manifest invalid"):
        await run_solution_deploy(
            context,
            SolutionDeployPayload(
                deploy_job_id=deploy_job_id,
                kind="install_from_repo",
                install_id=install_id,
                input_sha256="a" * 64,
                options={},
            ),
        )

    assert transaction_committed is True
    execute.assert_awaited_once_with(deploy_job_id, lease_token)
