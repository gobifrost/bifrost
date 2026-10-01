"""Platform-job handler behavior for the Maintenance workspace reimport."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.reimport import (
    WorkspaceReimportPayload,
    run_workspace_reimport,
)
from src.services.github_sync import WorkspaceSourceMissing


@pytest.mark.asyncio
async def test_reimport_reports_missing_workspace_source_as_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def fake_db_context():
        yield SimpleNamespace()

    message = "Reimport refused: workspace storage is empty. Nothing was changed."
    monkeypatch.setattr("src.jobs.platform.reimport.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.services.github_sync.GitHubSyncService.reimport_from_repo",
        AsyncMock(side_effect=WorkspaceSourceMissing(message)),
    )

    with pytest.raises(PlatformJobFailure) as error:
        await run_workspace_reimport(
            SimpleNamespace(report=AsyncMock(), log=AsyncMock()),
            WorkspaceReimportPayload(),
        )

    assert error.value.code == "workspace_source_missing"
    assert error.value.message == message
    assert error.value.retryable is False
