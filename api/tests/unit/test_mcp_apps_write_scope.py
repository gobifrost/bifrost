"""Global ``repository.readwrite`` enforcement for MCP ``push_files``."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller
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


def _caller(context: MCPContext, permissions: frozenset[str]) -> Caller:
    principal = UserPrincipal(
        user_id=context.user_id,
        email=context.user_email,
        organization_id=context.org_id,
        # Flags are preserved as actor facts only; grants below are authority.
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
    *,
    org_id,
    permissions=frozenset(),
    is_platform_admin=False,
    is_provider_org=False,
    session=None,
):
    context = MCPContext(
        user_id=uuid4(),
        org_id=org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        session=session,
    )
    context.test_caller = _caller(context, permissions)
    return context


@pytest.fixture(autouse=True)
def _load_explicit_mcp_caller(monkeypatch):
    async def load(_db, context: MCPContext) -> Caller:
        return context.test_caller

    monkeypatch.setattr("src.services.mcp_server.authorization.load_mcp_caller", load)


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
    async def test_global_app_path_denied_without_global_repository_write(self):
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
        session.execute.assert_not_awaited()

    async def test_own_org_app_path_denied_without_global_repository_write(self):
        """Home-org placement does not replace the Global repository grant."""
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
        session.execute.assert_not_awaited()

    async def test_own_org_app_path_allowed_with_global_repository_write(self):
        org_id = uuid4()
        app = _app(organization_id=org_id, repo_path="apps/org-app")
        session = FakeSession(apps=[app])
        ctx = _ctx(
            org_id=org_id,
            permissions=frozenset({"repository.readwrite"}),
            session=session,
        )

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

    async def test_unresolvable_path_denied_without_global_repository_write(self):
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
        session.execute.assert_not_awaited()

    async def test_mixed_batch_denied_before_any_metadata_query_without_grant(self):
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
        session.execute.assert_not_awaited()

    async def test_global_repository_writer_allowed_for_unresolvable_path(self):
        session = FakeSession(apps=[])
        ctx = _ctx(
            org_id=uuid4(),
            permissions=frozenset({"repository.readwrite"}),
            session=session,
        )

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
async def test_push_files_denies_actor_flags_without_repository_role_grant():
    """Actor provider/admin flags alone never turn into repository authority."""
    org_id = uuid4()
    app = _app(organization_id=org_id, repo_path="apps/org-app")
    ctx = _ctx(
        org_id=org_id,
        is_platform_admin=True,
        is_provider_org=True,
        session=FakeSession(apps=[app]),
    )

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
