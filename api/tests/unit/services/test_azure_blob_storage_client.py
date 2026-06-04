"""Unit tests for the Azure Blob S3-shaped storage adapter."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.file_storage.azure_blob_client import AzureBlobStorageClient


class _AsyncPageIterator:
    def __init__(self, pages):
        self._pages = list(pages)
        self.continuation_token = "next-token"

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._pages:
            raise StopAsyncIteration
        return self._pages.pop(0)


class _BlobPager:
    def __init__(self, container):
        self._container = container

    def by_page(self, **kwargs):
        self._container.by_page_kwargs = kwargs
        return _AsyncPageIterator(
            [[SimpleNamespace(name="workspace/report.txt", size=12, etag='"etag"')]]
        )


class _ContainerClient:
    def __init__(self):
        self.list_blobs_kwargs = None
        self.by_page_kwargs = None

    def list_blobs(self, **kwargs):
        self.list_blobs_kwargs = kwargs
        return _BlobPager(self)


@pytest.mark.asyncio
async def test_list_objects_v2_passes_page_size_to_list_blobs_not_by_page():
    """Azure async paging accepts results_per_page on list_blobs, not by_page."""
    client = object.__new__(AzureBlobStorageClient)
    container = _ContainerClient()
    client._container_client = container
    client._ensure_client = AsyncMock()

    response = await client.list_objects_v2(
        Bucket="ignored",
        Prefix="workspace/",
        ContinuationToken="cursor",
        MaxKeys=25,
    )

    assert container.list_blobs_kwargs == {
        "name_starts_with": "workspace/",
        "results_per_page": 25,
    }
    assert container.by_page_kwargs == {"continuation_token": "cursor"}
    assert response["Contents"][0]["Key"] == "workspace/report.txt"
    assert response["NextContinuationToken"] == "next-token"
