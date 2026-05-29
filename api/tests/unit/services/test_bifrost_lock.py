"""Regression tests for treating `.bifrost/` as ordinary workspace files."""

import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_delete_file_allows_bifrost_paths():
    """delete_file() should not special-case or block `.bifrost/` paths."""
    from src.services.file_storage.file_ops import FileOperationsService

    service = FileOperationsService.__new__(FileOperationsService)
    service._delete_from_s3 = AsyncMock()
    service._remove_from_search_index = AsyncMock()
    service._handle_app_file_cleanup = AsyncMock()
    service._remove_metadata = AsyncMock()
    service._invalidate_module_cache_if_python = AsyncMock()

    await service.delete_file(".bifrost/workflows.yaml")

    service._delete_from_s3.assert_awaited_once_with(".bifrost/workflows.yaml")
    service._remove_from_search_index.assert_awaited_once_with(".bifrost/workflows.yaml")
    service._handle_app_file_cleanup.assert_awaited_once_with(".bifrost/workflows.yaml")
    service._remove_metadata.assert_awaited_once_with(".bifrost/workflows.yaml")
    service._invalidate_module_cache_if_python.assert_awaited_once_with(
        ".bifrost/workflows.yaml"
    )
