"""Required deletion cleanup must not be reported as optional maintenance."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.file_storage.file_ops import FileOperationsService
from src.services.file_storage.service import FileStorageService


@pytest.fixture
def operations(monkeypatch):
    service = FileOperationsService.__new__(FileOperationsService)
    for name in (
        "_delete_from_s3", "_invalidate_module_cache_if_python",
        "_remove_metadata", "_remove_from_search_index", "_handle_app_file_cleanup",
    ):
        setattr(service, name, AsyncMock())
    monkeypatch.setattr("src.core.pubsub.publish_file_activity", AsyncMock())
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("step", [
    "_invalidate_module_cache_if_python", "_remove_metadata", "_remove_from_search_index",
])
async def test_required_cleanup_failure_reaches_the_caller(operations, step):
    getattr(operations, step).side_effect = RuntimeError("required cleanup failed")

    with pytest.raises(RuntimeError, match="required cleanup failed"):
        await operations.delete_file("workflows/example.py")

    operations._handle_app_file_cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_outage_does_not_fail_completed_retirement(operations):
    operations._handle_app_file_cleanup.side_effect = RuntimeError("preview unavailable")

    await operations.delete_file("workflows/example.py")

    operations._remove_metadata.assert_awaited_once_with("workflows/example.py")
    operations._remove_from_search_index.assert_awaited_once_with("workflows/example.py")


@pytest.mark.asyncio
async def test_search_failure_cannot_skip_workflow_retirement(operations):
    operations._remove_from_search_index.side_effect = RuntimeError("index unavailable")

    with pytest.raises(RuntimeError, match="index unavailable"):
        await operations.delete_file("workflows/example.py")

    operations._remove_metadata.assert_awaited_once_with("workflows/example.py")


@pytest.mark.asyncio
async def test_diagnostic_outage_cannot_prevent_workflow_retirement():
    service = FileStorageService.__new__(FileStorageService)
    service._workflow_indexer = MagicMock()
    service._workflow_indexer.delete_workflows_for_file = AsyncMock()
    service._diagnostics = MagicMock()
    service._diagnostics.clear_diagnostic_notification = AsyncMock(
        side_effect=RuntimeError("notifications unavailable"),
    )

    await service._remove_metadata("workflows/example.py")

    service._workflow_indexer.delete_workflows_for_file.assert_awaited_once_with(
        "workflows/example.py",
    )


@pytest.mark.asyncio
async def test_retirement_failure_is_not_swallowed_by_notification_cleanup():
    service = FileStorageService.__new__(FileStorageService)
    service._workflow_indexer = MagicMock()
    service._workflow_indexer.delete_workflows_for_file = AsyncMock(
        side_effect=RuntimeError("retirement failed"),
    )
    service._diagnostics = MagicMock()
    service._diagnostics.clear_diagnostic_notification = AsyncMock()

    with pytest.raises(RuntimeError, match="retirement failed"):
        await service._remove_metadata("workflows/example.py")

    service._diagnostics.clear_diagnostic_notification.assert_not_awaited()
