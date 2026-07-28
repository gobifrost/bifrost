from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from src.services.app_storage import AppStorageService


class _Body:
    def __init__(self, value: bytes):
        self.value = value

    async def read(self) -> bytes:
        return self.value


class _S3Client:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.copy_calls: list[tuple[str, str]] = []
        self.delete_calls: list[list[str]] = []

    async def list_objects_v2(self, *, Prefix: str, **_kwargs):  # noqa: N803
        if Prefix.endswith("/preview/"):
            names = [
                "manifest.json",
                "entry-new.js",
                "chunk-new.js",
                "entry-old.js",
                "chunk-old.js",
            ]
        else:
            names = ["manifest.json", "entry-old.js", "chunk-old.js"]
        return {
            "Contents": [{"Key": f"{Prefix}{name}"} for name in names],
            "IsTruncated": False,
        }

    async def get_object(self, *, Key: str, **_kwargs):  # noqa: N803
        assert Key.endswith("/preview/manifest.json")
        return {"Body": _Body(json.dumps(self.manifest).encode())}

    async def copy_object(
        self,
        *,
        CopySource: dict,  # noqa: N803
        Key: str,  # noqa: N803
        **_kwargs,
    ):
        self.copy_calls.append((CopySource["Key"], Key))

    async def delete_objects(self, *, Delete: dict, **_kwargs):  # noqa: N803
        self.delete_calls.append([item["Key"] for item in Delete["Objects"]])


def _storage(client: _S3Client) -> AppStorageService:
    storage = object.__new__(AppStorageService)
    storage._bucket = "test"
    storage._settings = None

    @asynccontextmanager
    async def _get_client():
        yield client

    storage._get_client = _get_client
    storage.invalidate_render_cache = AsyncMock()
    return storage


@pytest.mark.asyncio
async def test_publish_promotes_only_manifest_outputs_and_copies_manifest_last():
    client = _S3Client(
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
    client = _S3Client(
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
    client = _S3Client(
        {
            "entry": "entry-new.js",
            "outputs": ["entry-new.js", "chunk-new.js"],
        }
    )
    client.delete_objects = AsyncMock(side_effect=RuntimeError("cleanup unavailable"))
    storage = _storage(client)

    published = await storage.publish("app-1")

    assert published == 3
    assert client.copy_calls[-1][0].endswith("/manifest.json")
    storage.invalidate_render_cache.assert_awaited_once_with("app-1")
