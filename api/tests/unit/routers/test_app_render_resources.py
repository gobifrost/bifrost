from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.app_code_files import FileMode, render_app


@pytest.mark.asyncio
async def test_render_rejects_oversized_legacy_preview_before_reading_bodies_or_caching() -> None:
    app_id = uuid4()
    storage = SimpleNamespace(
        get_render_cache=AsyncMock(return_value=None),
        list_files_with_metadata=AsyncMock(return_value={"pages/huge.js": 17 * 1024 * 1024}),
        read_file=AsyncMock(),
        set_render_cache=AsyncMock(),
    )
    app = SimpleNamespace(id=app_id, dependencies={})

    with patch("src.routers.app_code_files.AppStorageService", return_value=storage), patch(
        "src.routers.app_code_files._get_application_by_id_or_404",
        new=AsyncMock(return_value=app),
    ):
        with pytest.raises(HTTPException) as error:
            await render_app(app_id, FileMode.draft, ctx=None, _user=None)

    assert error.value.status_code == 413
    storage.read_file.assert_not_awaited()
    storage.set_render_cache.assert_not_awaited()
