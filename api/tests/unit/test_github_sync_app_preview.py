from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from bifrost.manifest import Manifest, ManifestApp

from src.models.contracts.github import PushResult, WorkspaceSyncPlan
from src.services.app_resource_limits import AppSourceCompilationError, AppSourceLimitError
from src.services.github_sync import AppPreviewSyncError, GitHubSyncService


def _service() -> GitHubSyncService:
    service = object.__new__(GitHubSyncService)
    service.repo_manager = SimpleNamespace(_settings=None)
    return service


def _manifest() -> Manifest:
    return Manifest(
        apps={
            "first": ManifestApp(id="app-1", path="apps/first"),
            "second": ManifestApp(id="app-2", path="apps/second"),
        }
    )


@pytest.mark.asyncio
async def test_preview_source_limit_is_preserved_for_non_retryable_publication() -> None:
    storage = SimpleNamespace(
        sync_preview_compiled=AsyncMock(side_effect=AppSourceLimitError("source too large"))
    )
    with patch("src.services.github_sync.read_manifest_from_dir", return_value=_manifest()), patch(
        "src.services.app_storage.AppStorageService", return_value=storage
    ):
        with pytest.raises(AppSourceLimitError, match="source too large"):
            await _service()._sync_app_previews(Path("/tmp/workspace"))


@pytest.mark.asyncio
async def test_preview_infrastructure_failure_is_retryable_publication_error() -> None:
    storage = SimpleNamespace(
        sync_preview_compiled=AsyncMock(side_effect=RuntimeError("S3 unavailable"))
    )
    with patch("src.services.github_sync.read_manifest_from_dir", return_value=_manifest()), patch(
        "src.services.app_storage.AppStorageService", return_value=storage
    ):
        with pytest.raises(AppPreviewSyncError, match="successful peer previews remain published"):
            await _service()._sync_app_previews(Path("/tmp/workspace"))


@pytest.mark.asyncio
async def test_authored_preview_error_makes_final_publication_nonretryable(tmp_path: Path) -> None:
    service = _service()
    service.repo_manager = SimpleNamespace(
        sync_up=AsyncMock(),
        delete_workspace_checkpoint=AsyncMock(),
    )
    service._do_push = Mock(return_value=PushResult(success=True, commit_sha="abc", pushed_commits=1))
    service._sync_app_previews = AsyncMock(
        side_effect=AppSourceCompilationError("Unexpected token in pages/index.tsx")
    )
    repo = SimpleNamespace(
        head=SimpleNamespace(
            is_valid=lambda: True,
            commit=SimpleNamespace(hexsha="abc"),
        )
    )
    plan = WorkspaceSyncPlan(merge_sha="abc", workspace_fingerprint="fingerprint", db_applied=True)

    with patch("src.services.github_sync._workspace_fingerprint", return_value="fingerprint"), patch(
        "src.core.module_cache.refresh_modules_from_directory", new=AsyncMock()
    ):
        result = await service.apply_desktop_sync(
            tmp_path, repo, plan, confirm_deletes=False
        )

    assert result.retryable is False
    assert result.retry_plan is None
    assert "Unexpected token" in (result.error or "")
