"""Write- and read-scope enforcement for the code_editor MCP tools.

``patch_content``, ``replace_content``, and ``delete_content`` all require
scope bypass (platform admin or provider-org member) to touch any `_repo/`
path — own-org ownership of the underlying Application/Workflow is no
longer sufficient, matching REST's admin-only `_repo/` write routes.
``list_content``, ``search_content``, ``read_content_lines``, and
``get_content`` require the same bypass to read, matching REST's
admin-only `_repo/` read routes (files.py's `/editor`, `/editor/content`).
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.file_storage import FileStorageService
from src.services.mcp_server.tools import code_editor


def _fake_tool_db(db_session):
    @asynccontextmanager
    async def _cm(_context):
        yield db_session

    return _cm


def _ctx(*, org_id=None, is_platform_admin=False, is_provider_org=False):
    return SimpleNamespace(
        user_id=uuid4(),
        org_id=org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        user_email="x@y.z",
    )


def _is_error(result) -> bool:
    return bool(result.structured_content) and "error" in result.structured_content


@pytest.mark.asyncio
async def test_replace_content_denied_for_non_bypass(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.replace_content(
        ctx, path="apps/some-app/pages/index.tsx", content="export default () => null;"
    )

    assert _is_error(result)
    write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_content_allowed_for_bypass_caller(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)
    monkeypatch.setattr(FileStorageService, "read_file", AsyncMock(side_effect=FileNotFoundError))

    ctx = _ctx(is_platform_admin=True)
    result = await code_editor.replace_content(
        ctx, path="apps/some-app/pages/index.tsx", content="export default () => null;"
    )

    assert not _is_error(result)
    write_file.assert_awaited()


@pytest.mark.asyncio
async def test_replace_content_allowed_for_provider_org_caller(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)
    monkeypatch.setattr(FileStorageService, "read_file", AsyncMock(side_effect=FileNotFoundError))

    ctx = _ctx(is_provider_org=True)
    result = await code_editor.replace_content(
        ctx, path="apps/some-app/pages/index.tsx", content="export default () => null;"
    )

    assert not _is_error(result)
    write_file.assert_awaited()


@pytest.mark.asyncio
async def test_delete_content_denied_for_non_bypass(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor.RepoStorage, "exists", AsyncMock(return_value=True))

    delete_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "delete_file", delete_file)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.delete_content(ctx, path="workflows/sync.py")

    assert _is_error(result)
    delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_content_allowed_for_bypass_caller(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor.RepoStorage, "exists", AsyncMock(return_value=True))

    delete_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "delete_file", delete_file)

    ctx = _ctx(is_platform_admin=True)
    result = await code_editor.delete_content(ctx, path="workflows/sync.py")

    assert not _is_error(result)
    delete_file.assert_awaited()


@pytest.mark.asyncio
async def test_patch_content_denied_for_non_bypass(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    read_from_s3 = AsyncMock(return_value="old content")
    monkeypatch.setattr(code_editor, "_read_from_s3", read_from_s3)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.patch_content(
        ctx, path="workflows/sync.py", old_string="old", new_string="new"
    )

    assert _is_error(result)
    # Denied before ever reading the file's content.
    read_from_s3.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_content_denied_for_non_bypass(monkeypatch):
    ctx = _ctx(org_id=uuid4())
    result = await code_editor.list_content(ctx)
    assert _is_error(result)


@pytest.mark.asyncio
async def test_list_content_allowed_for_bypass_caller(monkeypatch):
    from src.services.repo_storage import RepoStorage

    monkeypatch.setattr(RepoStorage, "list", AsyncMock(return_value=[]))
    ctx = _ctx(is_platform_admin=True)
    result = await code_editor.list_content(ctx)
    assert not _is_error(result)


@pytest.mark.asyncio
async def test_search_content_denied_for_non_bypass(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    ctx = _ctx(org_id=uuid4())
    result = await code_editor.search_content(ctx, pattern="foo")
    assert _is_error(result)


@pytest.mark.asyncio
async def test_get_content_denied_for_non_bypass(monkeypatch):
    read_from_s3 = AsyncMock(return_value="content")
    monkeypatch.setattr(code_editor, "_read_from_s3", read_from_s3)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.get_content(ctx, path="workflows/sync.py")

    assert _is_error(result)
    read_from_s3.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_content_allowed_for_bypass_caller(monkeypatch):
    read_from_s3 = AsyncMock(return_value="content")
    monkeypatch.setattr(code_editor, "_read_from_s3", read_from_s3)

    ctx = _ctx(is_platform_admin=True)
    result = await code_editor.get_content(ctx, path="workflows/sync.py")

    assert not _is_error(result)
    read_from_s3.assert_awaited()


@pytest.mark.asyncio
async def test_read_content_lines_denied_for_non_bypass(monkeypatch):
    read_from_s3 = AsyncMock(return_value="content")
    monkeypatch.setattr(code_editor, "_read_from_s3", read_from_s3)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.read_content_lines(ctx, path="workflows/sync.py")

    assert _is_error(result)
    read_from_s3.assert_not_awaited()
