from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.services.app_compiler import AppCompilerInfrastructureError, CompileResult
from src.services.app_storage import AppStorageService


class _Body:
    def __init__(self, value: bytes) -> None:
        self._value = value

    async def read(self) -> bytes:
        return self._value


class _PreviewClient:
    def __init__(self, source: bytes) -> None:
        self.source = source
        self.put_object = AsyncMock()
        self.delete_object = AsyncMock()

    async def list_objects_v2(self, *, Prefix: str, **_kwargs):  # noqa: N803
        if Prefix == "_repo/apps/demo/":
            return {
                "Contents": [{"Key": "_repo/apps/demo/pages/index.tsx", "Size": len(self.source)}],
                "IsTruncated": False,
            }
        return {
            "Contents": [{"Key": "_apps/app-1/preview/entry-old.js", "Size": 1}],
            "IsTruncated": False,
        }

    async def get_object(self, *, Key: str, **_kwargs):  # noqa: N803
        assert Key == "_repo/apps/demo/pages/index.tsx"
        return {"Body": _Body(self.source)}


def _storage(client: _PreviewClient) -> AppStorageService:
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
async def test_compilation_failure_keeps_existing_preview_and_cache() -> None:
    client = _PreviewClient(b"export default function Broken() { return <div>; }")
    storage = _storage(client)

    with patch(
        "src.services.app_compiler.AppCompilerService.compile_batch",
        new=AsyncMock(return_value=[
            CompileResult(path="pages/index.tsx", success=False, error="Unexpected token")
        ]),
    ):
        with pytest.raises(ValueError, match="existing preview artifacts were retained"):
            await storage.sync_preview_compiled("app-1", "apps/demo")

    client.put_object.assert_not_awaited()
    client.delete_object.assert_not_awaited()
    storage.invalidate_render_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_compiler_response_preserves_existing_preview_and_cache() -> None:
    client = _PreviewClient(b"export default function Page() { return null; }")
    storage = _storage(client)

    with patch(
        "src.services.app_compiler.AppCompilerService.compile_batch",
        new=AsyncMock(return_value=[]),
    ):
        with pytest.raises(AppCompilerInfrastructureError, match="did not cover"):
            await storage.sync_preview_compiled("app-1", "apps/demo")

    client.put_object.assert_not_awaited()
    client.delete_object.assert_not_awaited()
    storage.invalidate_render_cache.assert_not_awaited()
