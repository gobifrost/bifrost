from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from src.core.storage.contracts import ContentProperties, ObjectInfo, ObjectPage, ObjectSummary, ObjectVersion
from unittest.mock import AsyncMock

import pytest

from src.services.app_storage import AppStorageService


class _Body:
    def __init__(self, value: bytes):
        self.value = value

    async def read(self) -> bytes:
        return self.value


class _Store:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.copy_calls: list[tuple[str, str]] = []
        self.delete_calls: list[list[str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def list_page(self, prefix, **_kwargs):
        names = (["manifest.json", "entry-new.js", "chunk-new.js", "entry-old.js", "chunk-old.js"]
                 if prefix.endswith("/preview/") else ["manifest.json", "entry-old.js", "chunk-old.js"])
        return ObjectPage(tuple(ObjectSummary(prefix + name, 1, ObjectVersion("snapshot"), None) for name in names), (), None)

    async def stat(self, key):
        return ObjectInfo(key, 1, ObjectVersion("snapshot"), None, ContentProperties(), {}, None)

    @asynccontextmanager
    async def open_read(self, key):
        assert key.endswith("/preview/manifest.json")
        yield SimpleNamespace(info=await self.stat(key), body=_Body(json.dumps(self.manifest).encode()))

    async def copy(self, source, destination):
        self.copy_calls.append((source.key, destination))

    async def delete_many(self, keys):
        self.delete_calls.append(list(keys))


def _storage(client: _Store) -> AppStorageService:
    storage = object.__new__(AppStorageService)
    storage._settings = None
    storage._get_store = lambda: client
    storage.invalidate_render_cache = AsyncMock()
    return storage


@pytest.mark.asyncio
async def test_publish_promotes_only_manifest_outputs_and_copies_manifest_last():
    client = _Store(
        {
            "entry": "entry-new.js",
            "outputs": ["entry-new.js", "chunk-new.js"],
        }
    )
    storage = _storage(client)
    progress: list[tuple[int, int]] = []

    published = await storage.publish(
        "app-1",
        progress_callback=lambda current, total: _record(
            progress, current, total
        ),
    )

    assert published == 3
    copied_sources = [source.rsplit("/", 1)[-1] for source, _ in client.copy_calls]
    assert set(copied_sources) == {
        "entry-new.js",
        "chunk-new.js",
        "manifest.json",
    }
    assert copied_sources[-1] == "manifest.json"
    deleted = {key.rsplit("/", 1)[-1] for call in client.delete_calls for key in call}
    assert deleted == {"entry-old.js", "chunk-old.js"}
    assert progress[0] == (0, 3)
    assert progress[-1] == (3, 3)
    storage.invalidate_render_cache.assert_awaited_once_with("app-1")


async def _record(
    target: list[tuple[int, int]],
    current: int,
    total: int,
) -> None:
    target.append((current, total))


@pytest.mark.asyncio
async def test_publish_rejects_manifest_missing_declared_output_before_copy():
    client = _Store(
        {
            "entry": "entry-new.js",
            "outputs": ["entry-new.js", "missing.js"],
        }
    )
    storage = _storage(client)

    with pytest.raises(ValueError, match="missing artifact"):
        await storage.publish("app-1")

    assert client.copy_calls == []
    assert client.delete_calls == []


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_turn_promoted_manifest_into_failed_publish():
    client = _Store(
        {
            "entry": "entry-new.js",
            "outputs": ["entry-new.js", "chunk-new.js"],
        }
    )
    client.delete_many = AsyncMock(side_effect=RuntimeError("cleanup unavailable"))
    storage = _storage(client)

    published = await storage.publish("app-1")

    assert published == 3
    assert client.copy_calls[-1][0].endswith("/manifest.json")
    storage.invalidate_render_cache.assert_awaited_once_with("app-1")
