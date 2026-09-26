"""Write-scope enforcement for the code_editor MCP tools.

``patch_content`` and ``replace_content`` both funnel through
``_replace_workspace_file``; ``delete_content`` checks separately.  A
non-bypass caller may only write or delete a path that maps to an owning
entity (Application or Workflow) in their own organization. A path with no
owning entity — such as a bare text/README file, which this module's
docstring says it also serves — is bypass-only, since there is nothing to
check ownership of. Read behavior (list/search/read_content_lines/
get_content) is unchanged by this fix.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.applications import Application
from src.models.orm.organizations import Organization
from src.models.orm.workflows import Workflow
from src.services.file_storage import FileStorageService
from src.services.mcp_server.tools import code_editor


def _fake_tool_db(db_session):
    @asynccontextmanager
    async def _cm(_context):
        yield db_session

    return _cm


async def _org(db) -> Organization:
    row = Organization(name=f"org-{uuid4().hex[:8]}", domain=f"{uuid4().hex}.example", created_by="test")
    db.add(row)
    await db.flush()
    return row


async def _app(db, *, organization_id, repo_path) -> Application:
    row = Application(
        name=f"app-{uuid4().hex[:8]}",
        slug=f"app-{uuid4().hex[:8]}",
        organization_id=organization_id,
        repo_path=repo_path,
        created_by="test",
    )
    db.add(row)
    await db.flush()
    return row


async def _workflow(db, *, organization_id, path) -> Workflow:
    row = Workflow(
        name=f"wf-{uuid4().hex[:8]}",
        function_name="run",
        organization_id=organization_id,
        path=path,
        type="workflow",
    )
    db.add(row)
    await db.flush()
    return row


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
async def test_replace_content_denied_for_global_app_path(db_session, monkeypatch):
    app = await _app(db_session, organization_id=None, repo_path="apps/global-app")
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.replace_content(
        ctx, path="apps/global-app/pages/index.tsx", content="export default () => null;"
    )

    assert _is_error(result)
    write_file.assert_not_awaited()
    assert app.repo_path == "apps/global-app"  # unchanged


@pytest.mark.asyncio
async def test_replace_content_allowed_for_own_org_app_path(db_session, monkeypatch):
    org = await _org(db_session)
    await _app(db_session, organization_id=org.id, repo_path="apps/org-app")
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)
    monkeypatch.setattr(FileStorageService, "read_file", AsyncMock(side_effect=FileNotFoundError))

    ctx = _ctx(org_id=org.id)
    result = await code_editor.replace_content(
        ctx, path="apps/org-app/pages/index.tsx", content="export default () => null;"
    )

    assert not _is_error(result)
    write_file.assert_awaited()


@pytest.mark.asyncio
async def test_delete_content_denied_for_global_workflow_path(db_session, monkeypatch):
    await _workflow(db_session, organization_id=None, path="workflows/global_sync.py")
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor.RepoStorage, "exists", AsyncMock(return_value=True))

    delete_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "delete_file", delete_file)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.delete_content(ctx, path="workflows/global_sync.py")

    assert _is_error(result)
    delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_content_allowed_for_own_org_workflow_path(db_session, monkeypatch):
    org = await _org(db_session)
    await _workflow(db_session, organization_id=org.id, path="workflows/org_sync.py")
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor.RepoStorage, "exists", AsyncMock(return_value=True))

    delete_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "delete_file", delete_file)

    ctx = _ctx(org_id=org.id)
    result = await code_editor.delete_content(ctx, path="workflows/org_sync.py")

    assert not _is_error(result)
    delete_file.assert_awaited()


@pytest.mark.asyncio
async def test_unresolvable_path_denied_for_non_bypass(db_session, monkeypatch):
    """A path that maps to no owning entity (e.g. a bare text file) is
    bypass-only, not "anyone may write it"."""
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.replace_content(
        ctx, path="README.md", content="# notes"
    )

    assert _is_error(result)
    write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_bypass_caller_allowed_for_unresolvable_path(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)
    monkeypatch.setattr(FileStorageService, "read_file", AsyncMock(side_effect=FileNotFoundError))

    ctx = _ctx(is_platform_admin=True)
    result = await code_editor.replace_content(
        ctx, path="README.md", content="# notes"
    )

    assert not _is_error(result)
    write_file.assert_awaited()
