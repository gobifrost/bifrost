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
from src.models.orm.solutions import Solution


@pytest.mark.asyncio
async def test_failed_repo_install_raises_job_failure_without_touching_install(
    monkeypatch,
):
    """The brand-new install row for a failed from-repo first deploy is now
    deleted atomically by ``_run_deploy_job`` itself, in the same transaction
    as its ``failed`` status write (see ``_run_deploy_job``'s docstring and
    ``tests/unit/routers/test_solution_deploy_jobs.py``). ``run_solution_deploy``
    only needs to read the terminal job status back and surface its error as a
    ``PlatformJobFailure`` — it must not also reach for the install row itself.
    """
    deploy_job_id = uuid4()
    install_id = uuid4()
    projection = SolutionDeployJob(
        id=deploy_job_id,
        install_id=None,  # already cleared by _run_deploy_job's own failure path
        status="failed",
        error="manifest invalid",
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

    context = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.SolutionDeployJobStorage.copy_to_path",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.SolutionDeployJobStorage.delete",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.get_db_context", fake_db_context
    )
    monkeypatch.setattr("src.routers.solutions._run_deploy_job", AsyncMock())

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
    # No install-cleanup reach-in from this layer anymore: the orphan lookup
    # (db.get(Solution, ...)) never happens, and neither does a flush/delete.
    db.flush.assert_not_awaited()
    db.delete.assert_not_awaited()
