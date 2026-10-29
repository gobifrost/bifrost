"""Named Global repository permissions for the code-editor MCP tools."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller
from src.services.file_storage import FileStorageService
from src.services.mcp_server.tools import code_editor


def _fake_tool_db(db_session):
    @asynccontextmanager
    async def _cm(_context):
        yield db_session

    return _cm


def _caller(context, permissions: frozenset[str]) -> Caller:
    principal = UserPrincipal(
        user_id=context.user_id,
        email=context.user_email,
        organization_id=context.org_id,
        # These MCP context flags describe the actor only. The explicit role
        # grant below is the authority the evaluator decides from.
        is_superuser=context.is_platform_admin,
        is_provider_org=context.is_provider_org,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=principal.organization_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(
                RoleGrant(
                    role_id=uuid4(),
                    permissions=permissions,
                    boundaries=(Boundary(BoundaryKind.PLATFORM),),
                ),
            ) if permissions else (),
        ),
    )


def _ctx(
    *, org_id=None, permissions=frozenset(), is_platform_admin=False, is_provider_org=False
):
    context = SimpleNamespace(
        user_id=uuid4(),
        org_id=org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        user_email="x@y.z",
        session=AsyncMock(),
    )
    context.test_caller = _caller(context, permissions)
    return context


def _is_error(result) -> bool:
    return bool(result.structured_content) and "error" in result.structured_content


@pytest.mark.asyncio
async def test_replace_content_denied_without_global_repository_write(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=False))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.replace_content(
        ctx, path="apps/some-app/pages/index.tsx", content="export default () => null;"
    )

    assert _is_error(result)
    write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_content_allowed_with_global_repository_write(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=True))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)
    monkeypatch.setattr(FileStorageService, "read_file", AsyncMock(side_effect=FileNotFoundError))

    ctx = _ctx(permissions=frozenset({"repository.readwrite"}))
    result = await code_editor.replace_content(
        ctx, path="apps/some-app/pages/index.tsx", content="export default () => null;"
    )

    assert not _is_error(result)
    write_file.assert_awaited()


@pytest.mark.asyncio
async def test_replace_content_denies_actor_platform_admin_without_role_grant(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=False))

    write_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "write_file", write_file)
    ctx = _ctx(is_platform_admin=True)
    result = await code_editor.replace_content(
        ctx, path="apps/some-app/pages/index.tsx", content="export default () => null;"
    )

    assert _is_error(result)
    write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_content_denied_without_global_repository_write(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=False))
    monkeypatch.setattr(code_editor.RepoStorage, "exists", AsyncMock(return_value=True))

    delete_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "delete_file", delete_file)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.delete_content(ctx, path="workflows/sync.py")

    assert _is_error(result)
    delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_content_allowed_with_global_repository_write(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(code_editor.RepoStorage, "exists", AsyncMock(return_value=True))

    delete_file = AsyncMock()
    monkeypatch.setattr(FileStorageService, "delete_file", delete_file)

    ctx = _ctx(permissions=frozenset({"repository.readwrite"}))
    result = await code_editor.delete_content(ctx, path="workflows/sync.py")

    assert not _is_error(result)
    delete_file.assert_awaited()


@pytest.mark.asyncio
async def test_patch_content_denied_without_global_repository_write(db_session, monkeypatch):
    monkeypatch.setattr(code_editor, "get_tool_db", _fake_tool_db(db_session))
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=False))
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
async def test_list_content_denied_without_global_repository_read(monkeypatch):
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=False))
    ctx = _ctx(org_id=uuid4())
    result = await code_editor.list_content(ctx)
    assert _is_error(result)


@pytest.mark.asyncio
async def test_list_content_allowed_with_global_repository_read(monkeypatch):
    from src.services.repo_storage import RepoStorage

    monkeypatch.setattr(RepoStorage, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=True))
    ctx = _ctx(permissions=frozenset({"repository.read"}))
    result = await code_editor.list_content(ctx)
    assert not _is_error(result)


@pytest.mark.asyncio
async def test_get_content_denied_without_global_repository_read(monkeypatch):
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=False))
    read_from_s3 = AsyncMock(return_value="content")
    monkeypatch.setattr(code_editor, "_read_from_s3", read_from_s3)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.get_content(ctx, path="workflows/sync.py")

    assert _is_error(result)
    read_from_s3.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_content_allowed_with_global_repository_read(monkeypatch):
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=True))
    read_from_s3 = AsyncMock(return_value="content")
    monkeypatch.setattr(code_editor, "_read_from_s3", read_from_s3)

    ctx = _ctx(permissions=frozenset({"repository.read"}))
    result = await code_editor.get_content(ctx, path="workflows/sync.py")

    assert not _is_error(result)
    read_from_s3.assert_awaited()


@pytest.mark.asyncio
async def test_read_content_lines_denied_without_global_repository_read(monkeypatch):
    monkeypatch.setattr(code_editor, "_has_workspace_permission", AsyncMock(return_value=False))
    read_from_s3 = AsyncMock(return_value="content")
    monkeypatch.setattr(code_editor, "_read_from_s3", read_from_s3)

    ctx = _ctx(org_id=uuid4())
    result = await code_editor.read_content_lines(ctx, path="workflows/sync.py")

    assert _is_error(result)
    read_from_s3.assert_not_awaited()


@pytest.mark.asyncio
async def test_workspace_permission_uses_the_loaded_caller_at_global_boundary(monkeypatch):
    """The helper delegates authority to the current Caller, never context flags."""
    context = _ctx(permissions=frozenset({"repository.read"}))
    load_caller = AsyncMock(return_value=context.test_caller)
    require = MagicMock()
    monkeypatch.setattr(
        "src.services.mcp_server.authorization.load_mcp_caller", load_caller
    )
    monkeypatch.setattr(
        "src.services.authorization.enforce.require_named_permission", require
    )

    assert await code_editor._has_workspace_permission(context, "repository.read")
    load_caller.assert_awaited_once_with(context.session, context)
    require.assert_called_once_with(context.test_caller, "repository.read", None)
