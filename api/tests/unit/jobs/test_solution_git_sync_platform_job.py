"""Platform-job handler behavior for managed Solution Git updates."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.solution_git_sync import (
    SOLUTION_GIT_SYNC_DEFINITION,
    SolutionGitSyncPayload,
    run_solution_git_sync,
)
from src.models.orm.solutions import Solution
from src.services.solutions.deploy import SolutionFinalizeIncomplete
from src.services.solutions.git_sync import NotASolutionWorkspace


def test_solution_git_sync_definition_has_durable_per_install_policy() -> None:
    policy = SOLUTION_GIT_SYNC_DEFINITION.policy

    assert SOLUTION_GIT_SYNC_DEFINITION.job_type == "solution.git_sync"
    assert SOLUTION_GIT_SYNC_DEFINITION.payload_model is SolutionGitSyncPayload
    assert policy.timeout_seconds == 60 * 60
    assert policy.max_attempts == 2
    assert policy.retry_on_runner_loss is True
    assert policy.allow_running_cancellation is True


@pytest.mark.asyncio
async def test_solution_git_sync_runs_the_existing_single_writer_and_clears_update_badge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(
        id=uuid4(),
        slug="managed-git",
        name="Managed Git",
        git_connected=True,
        git_repo_url="https://example.test/managed-git.git",
        update_available_version="2.0.0",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=solution), commit=AsyncMock())
    context = SimpleNamespace(report=AsyncMock(), log=AsyncMock())
    sync = AsyncMock()

    @asynccontextmanager
    async def fake_db_context():
        yield db

    monkeypatch.setattr(
        "src.jobs.platform.solution_git_sync.get_db_context", fake_db_context
    )
    monkeypatch.setattr(
        "src.services.solutions.git_sync.sync", sync
    )

    result = await run_solution_git_sync(
        context, SolutionGitSyncPayload(solution_id=solution.id)
    )

    assert result == {"solution_id": str(solution.id), "status": "synced"}
    sync.assert_awaited_once_with(db, solution)
    assert solution.update_available_version is None
    db.commit.assert_awaited_once()
    assert context.report.await_args_list[-1].kwargs == {"percent": 100}


@pytest.mark.asyncio
async def test_solution_git_sync_retries_when_the_single_writer_only_queued_a_pending_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock-contended sync is not a completed Git update."""
    solution = Solution(
        id=uuid4(),
        slug="managed-git",
        name="Managed Git",
        git_connected=True,
        git_repo_url="https://example.test/managed-git.git",
        update_available_version="2.0.0",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=solution), commit=AsyncMock())

    @asynccontextmanager
    async def fake_db_context():
        yield db

    monkeypatch.setattr(
        "src.jobs.platform.solution_git_sync.get_db_context", fake_db_context
    )
    monkeypatch.setattr(
        "src.services.solutions.git_sync.sync", AsyncMock(return_value=False)
    )

    with pytest.raises(PlatformJobFailure) as error:
        await run_solution_git_sync(
            SimpleNamespace(report=AsyncMock(), log=AsyncMock()),
            SolutionGitSyncPayload(solution_id=solution.id),
        )

    assert error.value.code == "solution_git_sync_deferred"
    assert error.value.retryable is True
    assert solution.update_available_version == "2.0.0"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_solution_git_sync_reports_invalid_workspace_as_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(
        id=uuid4(),
        slug="managed-git",
        name="Managed Git",
        git_connected=True,
        git_repo_url="https://example.test/managed-git.git",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=solution), commit=AsyncMock())

    @asynccontextmanager
    async def fake_db_context():
        yield db

    monkeypatch.setattr(
        "src.jobs.platform.solution_git_sync.get_db_context", fake_db_context
    )
    monkeypatch.setattr(
        "src.services.solutions.git_sync.sync",
        AsyncMock(side_effect=NotASolutionWorkspace("descriptor missing")),
    )

    with pytest.raises(PlatformJobFailure) as error:
        await run_solution_git_sync(
            SimpleNamespace(report=AsyncMock(), log=AsyncMock()),
            SolutionGitSyncPayload(solution_id=solution.id),
        )

    assert error.value.code == "invalid_solution_workspace"
    assert error.value.message == "descriptor missing"


@pytest.mark.asyncio
async def test_solution_git_sync_keeps_update_badge_when_storage_finalization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(
        id=uuid4(),
        slug="managed-git",
        name="Managed Git",
        git_connected=True,
        git_repo_url="https://example.test/managed-git.git",
        update_available_version="2.0.0",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=solution), commit=AsyncMock())
    context = SimpleNamespace(report=AsyncMock(), log=AsyncMock())

    @asynccontextmanager
    async def fake_db_context():
        yield db

    monkeypatch.setattr(
        "src.jobs.platform.solution_git_sync.get_db_context", fake_db_context
    )
    monkeypatch.setattr(
        "src.services.solutions.git_sync.sync",
        AsyncMock(side_effect=SolutionFinalizeIncomplete(str(solution.id))),
    )

    with pytest.raises(PlatformJobFailure) as error:
        await run_solution_git_sync(
            context, SolutionGitSyncPayload(solution_id=solution.id)
        )

    assert error.value.code == "solution_git_finalize_incomplete"
    assert solution.update_available_version == "2.0.0"
    db.commit.assert_not_awaited()
    assert all(
        report.kwargs.get("percent") != 100
        for report in context.report.await_args_list
    )
