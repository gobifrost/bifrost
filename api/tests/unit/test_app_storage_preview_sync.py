from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from src.core.storage.contracts import ObjectPage, ObjectSummary, ObjectVersion
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
        self.write = AsyncMock()
        self.delete = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def list_page(self, prefix, **_kwargs):
        key, size = (("_repo/apps/demo/pages/index.tsx", len(self.source)) if prefix == "_repo/apps/demo/"
                     else ("_apps/app-1/preview/entry-old.js", 1))
        return ObjectPage((ObjectSummary(key, size, ObjectVersion("snapshot"), None),), (), None)

    @asynccontextmanager
    async def open_read(self, key):
        assert key == "_repo/apps/demo/pages/index.tsx"
        yield SimpleNamespace(body=_Body(self.source))


def _storage(client: _PreviewClient) -> AppStorageService:
    storage = object.__new__(AppStorageService)
    storage._settings = None
    storage._get_store = lambda: client
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

    client.write.assert_not_awaited()
    client.delete.assert_not_awaited()
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

    client.write.assert_not_awaited()
    client.delete.assert_not_awaited()
    storage.invalidate_render_cache.assert_not_awaited()
