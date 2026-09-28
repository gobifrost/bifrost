"""Write-scope enforcement for the apps MCP ``push_files`` tool (the other app
write tools are thin REST wrappers; REST enforces their scope).

Mirrors the REST rule enforced by ``get_application_for_write_or_404``:
writing to ANY app — own-org or global — requires a platform admin, even
though org members can still read the apps they have access to.
"""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import apps as apps_tool

_NOT_FOUND = object()


def _is_error(result) -> bool:
    return bool(result.structured_content) and "error" in result.structured_content


def _table_name(stmt):
    try:
        return stmt.get_final_froms()[0].name
    except Exception:
        return None


def _literal_sql(stmt) -> str:
    try:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))
    except Exception:
        return str(stmt)


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class FakeSession:
    """Routes db.execute() by target table for apps write-scope tests."""

    def __init__(self, apps=None, workflow_org_by_path=None):
        self.apps = apps or []
        self.workflow_org_by_path = workflow_org_by_path or {}
        self.commit = AsyncMock()
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, stmt):
        table = _table_name(stmt)
        if table == "applications":
            return FakeResult(list(self.apps))
        if table == "workflows":
            sql = _literal_sql(stmt)
            m = re.search(r"path = '([^']*)'", sql)
            path = m.group(1) if m else None
            org = self.workflow_org_by_path.get(path, _NOT_FOUND)
            return FakeResult([] if org is _NOT_FOUND else [(org,)])
        if table == "file_index":
            # content_hash-per-file check: treat every file as new.
            return FakeResult([])
        return FakeResult([])


def _ctx(*, org_id, is_platform_admin=False, is_provider_org=False, session=None):
    return MCPContext(
        user_id=uuid4(),
        org_id=org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        session=session,
    )


def _app(*, organization_id, repo_path=None):
    return SimpleNamespace(
        id=uuid4(),
        organization_id=organization_id,
        repo_path=repo_path,
        solution_id=None,
        deployed_at=None,
        name="App",
        slug="app",
    )


# =============================================================================
# push_files
# =============================================================================


@pytest.mark.asyncio
class TestPushFilesWriteScope:
    async def test_global_app_path_denied_for_non_bypass(self):
        app = _app(organization_id=None, repo_path="apps/global-app")
        session = FakeSession(apps=[app])
        ctx = _ctx(org_id=uuid4(), session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"apps/global-app/pages/index.tsx": "content"}
            )

        assert _is_error(result)
        assert "permission" in result.structured_content["error"].lower()
        write_file.assert_not_awaited()

    async def test_own_org_app_path_denied_for_non_bypass(self):
        """Own-org membership alone no longer grants `_repo/` write access
        — writing any path is bypass-only, matching REST's admin-only
        `_repo/` editor routes."""
        org_id = uuid4()
        app = _app(organization_id=org_id, repo_path="apps/org-app")
        session = FakeSession(apps=[app])
        ctx = _ctx(org_id=org_id, session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"apps/org-app/pages/index.tsx": "content"}
            )

        assert _is_error(result)
        write_file.assert_not_awaited()

    async def test_own_org_app_path_allowed_for_bypass(self):
        org_id = uuid4()
        app = _app(organization_id=org_id, repo_path="apps/org-app")
        session = FakeSession(apps=[app])
        ctx = _ctx(org_id=org_id, is_platform_admin=True, session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"apps/org-app/pages/index.tsx": "content"}
            )

        assert not _is_error(result)
        write_file.assert_awaited()

    async def test_unresolvable_path_denied_for_non_bypass(self):
        session = FakeSession(apps=[])
        ctx = _ctx(org_id=uuid4(), session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"some/unowned/path.txt": "content"}
            )

        assert _is_error(result)
        assert "permission" in result.structured_content["error"].lower()
        write_file.assert_not_awaited()

    async def test_mixed_batch_with_one_denied_path_rejects_whole_batch(self):
        org_id = uuid4()
        app = _app(organization_id=org_id, repo_path="apps/org-app")
        session = FakeSession(apps=[app])
        ctx = _ctx(org_id=org_id, session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx,
                {
                    "apps/org-app/pages/index.tsx": "ok content",
                    "some/unowned/path.txt": "denied content",
                },
            )

        assert _is_error(result)
        assert "permission" in result.structured_content["error"].lower()
        write_file.assert_not_awaited()

    async def test_bypass_caller_allowed_for_unresolvable_path(self):
        session = FakeSession(apps=[])
        ctx = _ctx(org_id=uuid4(), is_platform_admin=True, session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"some/unowned/path.txt": "content"}
            )

        assert not _is_error(result)
        write_file.assert_awaited()



@pytest.mark.asyncio
async def test_push_files_denied_for_provider_org_non_admin():
    """Provider-org membership alone doesn't grant ``_repo/`` writes (REST's
    editor routes are platform-admin only)."""
    org_id = uuid4()
    app = _app(organization_id=org_id, repo_path="apps/org-app")
    ctx = _ctx(org_id=org_id, is_provider_org=True, session=FakeSession(apps=[app]))

    write_file = AsyncMock()
    with patch(
        "src.services.file_storage.FileStorageService.write_file",
        new=write_file,
    ):
        result = await apps_tool.push_files(
            ctx, {"apps/org-app/pages/index.tsx": "content"}
        )

    assert _is_error(result)
    write_file.assert_not_awaited()
