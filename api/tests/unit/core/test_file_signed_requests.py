"""URL-only consumers cannot silently discard provider-required headers."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.core.storage.contracts import SignedRequest, StorageError
from src.services.file_storage.object_storage import FileObjectStorage


def facade(monkeypatch, ticket):
    store = MagicMock()
    store.__aenter__ = AsyncMock(return_value=store)
    store.__aexit__ = AsyncMock(return_value=None)
    store.signed_request = AsyncMock(return_value=ticket)
    storage = FileObjectStorage(Settings(_env_file=None))
    monkeypatch.setattr(storage, "get_store", lambda: store)
    return storage, store


async def test_complete_upload_request_retains_provider_headers(monkeypatch):
    ticket = SignedRequest("PUT", "https://example.invalid?sig=fixture", {"x-ms-blob-type": "BlockBlob", "Content-Type": "text/plain"}, 600)
    storage, store = facade(monkeypatch, ticket)
    assert await storage.generate_signed_upload_request("key", "text/plain") is ticket
    store.signed_request.assert_awaited_once_with("key", "PUT", content_type="text/plain", expires_in=600)


async def test_download_link_refuses_headers_but_complete_request_preserves_them(monkeypatch):
    ticket = SignedRequest("GET", "fixture", {"x-required": "value"}, 600)
    storage, _ = facade(monkeypatch, ticket)
    assert await storage.generate_signed_download_request("key") is ticket
    with pytest.raises(StorageError) as caught:
        await storage.generate_presigned_download_url("key")
    assert caught.value.kind == "unsupported"
