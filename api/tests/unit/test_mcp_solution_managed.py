"""MCP direct-ORM mutation tools refuse solution-managed entities with the
locked read-only message (criterion 6, MCP surface).

The MCP tools for tables/agents/forms/events mutate the ORM object directly
(e.g. ``table.name = ...``) and rely on the session-wide before_flush backstop
(``install_solution_write_guard``), which fires on AsyncSession flush. The tool's
``except Exception`` wraps the raised ``SolutionManagedWriteError`` — whose
message IS the locked wording — into a clean ``error_result``. So an MCP edit of
a managed entity returns the same read-only message the REST guard returns, not
a generic 500.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.services.solutions.guard import (
    SOLUTION_MANAGED_MESSAGE,
    install_solution_write_guard,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _guard_installed():
    install_solution_write_guard()
    yield


async def _managed_table(db) -> uuid.UUID:
    from src.models.orm.solutions import Solution
    from src.models.orm.tables import Table

    sol = Solution(id=uuid.uuid4(), slug=f"mcp-{uuid.uuid4().hex[:8]}", name="MCP", organization_id=None)
    db.add(sol)
    await db.flush()
    tid = uuid.uuid4()
    db.add(Table(
        id=tid, name=f"t_{uuid.uuid4().hex[:8]}", organization_id=None,
        solution_id=sol.id, schema={"columns": []}, access={"policies": []},
    ))
    await db.flush()
    return tid


async def test_mcp_update_table_refuses_managed(db_session, monkeypatch):
    from contextlib import asynccontextmanager

    from src.services.mcp_server.tools import tables as mcp_tables

    tid = await _managed_table(db_session)

    # Point the MCP tool's db at this test session (it normally opens its own).
    @asynccontextmanager
    async def _fake_tool_db(_context):
        yield db_session

    monkeypatch.setattr(mcp_tables, "get_tool_db", _fake_tool_db)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_tables.update_table(context, table_id=str(tid), name="hijacked-via-mcp")

    # The tool returns an error result carrying the locked read-only message.
    payload = result.model_dump() if hasattr(result, "model_dump") else result
    text = str(payload)
    assert SOLUTION_MANAGED_MESSAGE in text, text


async def _managed_app(db, repo_path: str) -> uuid.UUID:
    from src.models.orm.applications import Application
    from src.models.orm.solutions import Solution

    sol = Solution(id=uuid.uuid4(), slug=f"mcp-{uuid.uuid4().hex[:8]}", name="MCP", organization_id=None)
    db.add(sol)
    await db.flush()
    aid = uuid.uuid4()
    db.add(Application(
        id=aid,
        name=f"app_{uuid.uuid4().hex[:8]}",
        slug=f"app-{uuid.uuid4().hex[:8]}",
        organization_id=None,
        solution_id=sol.id,
        repo_path=repo_path,
        created_by="system",
    ))
    await db.flush()
    return aid


def _fake_db_cm(db_session):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm(_context):
        yield db_session

    return _cm


async def test_mcp_publish_app_refuses_managed_without_s3_write(db_session, monkeypatch):
    """publish_app must reject a solution-managed app BEFORE copying preview→live."""
    from src.services.app_storage import AppStorageService
    from src.services.mcp_server.tools import apps as mcp_apps

    aid = await _managed_app(db_session, repo_path="apps/managed-pub")

    monkeypatch.setattr(mcp_apps, "get_tool_db", _fake_db_cm(db_session))

    # Sentinel: publish() (the preview→live S3 copy) must never be invoked.
    published = {"called": False}

    async def _boom_publish(self, app_id):  # noqa: ANN001
        published["called"] = True
        raise AssertionError("S3 publish must not run for a solution-managed app")

    monkeypatch.setattr(AppStorageService, "publish", _boom_publish)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_apps.publish_app(context, app_id=str(aid))

    payload = result.model_dump() if hasattr(result, "model_dump") else result
    text = str(payload)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    assert published["called"] is False


async def test_mcp_push_files_refuses_managed_without_s3_write(db_session, monkeypatch):
    """push_files must reject files under a managed app's repo_path BEFORE any S3 write."""
    from src.services.app_storage import AppStorageService
    from src.services.file_storage import FileStorageService
    from src.services.mcp_server.tools import apps as mcp_apps

    await _managed_app(db_session, repo_path="apps/managed-push")

    monkeypatch.setattr(mcp_apps, "get_tool_db", _fake_db_cm(db_session))

    # Sentinels: neither the _repo write nor the preview write may run.
    writes = {"repo": False, "preview": False}

    async def _boom_write_file(self, *args, **kwargs):  # noqa: ANN001
        writes["repo"] = True
        raise AssertionError("_repo write must not run for a solution-managed app")

    async def _boom_write_preview(self, *args, **kwargs):  # noqa: ANN001
        writes["preview"] = True
        raise AssertionError("preview write must not run for a solution-managed app")

    monkeypatch.setattr(FileStorageService, "write_file", _boom_write_file)
    monkeypatch.setattr(AppStorageService, "write_preview_file", _boom_write_preview)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_apps.push_files(
        context,
        files={"apps/managed-push/pages/index.tsx": "export default () => null;"},
    )

    payload = result.model_dump() if hasattr(result, "model_dump") else result
    text = str(payload)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    assert writes["repo"] is False
    assert writes["preview"] is False


async def test_mcp_push_files_delete_sweep_refuses_managed(db_session, monkeypatch):
    """push_files(files={}, delete_missing_prefix=<managed repo_path>) must NOT
    delete the managed app's _repo files and must return the read-only message.

    The delete-sweep is a separate code path from the files-key guard: an empty
    ``files`` dict slips past the key check, but ``delete_missing_prefix`` pointed
    at a managed app's repo_path would still sweep its files.
    """
    from sqlalchemy import select

    from src.models.orm.file_index import FileIndex
    from src.services.file_storage import FileStorageService
    from src.services.mcp_server.tools import apps as mcp_apps

    await _managed_app(db_session, repo_path="apps/managed-sweep")

    # Seed a FileIndex row under the managed prefix so the sweep would find it.
    managed_file = "apps/managed-sweep/pages/index.tsx"
    db_session.add(FileIndex(
        path=managed_file,
        content_hash="deadbeef",
    ))
    await db_session.flush()

    monkeypatch.setattr(mcp_apps, "get_tool_db", _fake_db_cm(db_session))

    deleted = {"paths": []}

    async def _track_delete(self, path):  # noqa: ANN001
        deleted["paths"].append(path)
        raise AssertionError(f"delete must not run for managed file {path}")

    monkeypatch.setattr(FileStorageService, "delete_file", _track_delete)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_apps.push_files(
        context,
        files={},
        delete_missing_prefix="apps/managed-sweep",
    )

    payload = result.model_dump() if hasattr(result, "model_dump") else result
    text = str(payload)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    assert deleted["paths"] == [], deleted["paths"]

    # The managed FileIndex row is still present (nothing was swept).
    still = await db_session.execute(
        select(FileIndex.path).where(FileIndex.path == managed_file)
    )
    assert still.scalar_one_or_none() == managed_file


async def test_mcp_push_files_delete_sweep_refuses_parent_of_managed(db_session, monkeypatch):
    """A delete prefix that CONTAINS a managed prefix (e.g. 'apps/' sweeping
    'apps/managed-...') must also be refused — the sweep would touch managed files."""
    from src.services.file_storage import FileStorageService
    from src.services.mcp_server.tools import apps as mcp_apps

    await _managed_app(db_session, repo_path="apps/managed-parent")

    monkeypatch.setattr(mcp_apps, "get_tool_db", _fake_db_cm(db_session))

    deleted = {"paths": []}

    async def _track_delete(self, path):  # noqa: ANN001
        deleted["paths"].append(path)
        raise AssertionError(f"delete must not run, would touch managed: {path}")

    monkeypatch.setattr(FileStorageService, "delete_file", _track_delete)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_apps.push_files(
        context,
        files={},
        delete_missing_prefix="apps",
    )

    payload = result.model_dump() if hasattr(result, "model_dump") else result
    text = str(payload)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    assert deleted["paths"] == [], deleted["paths"]


async def test_mcp_push_files_delete_sweep_allows_unmanaged(db_session, monkeypatch):
    """A delete-sweep under a NON-managed prefix still deletes normally — the
    guard must not over-block."""
    from src.models.orm.file_index import FileIndex
    from src.services.file_storage import FileStorageService
    from src.services.mcp_server.tools import apps as mcp_apps

    # A managed app exists elsewhere, but the sweep targets an unrelated prefix.
    await _managed_app(db_session, repo_path="apps/managed-other")

    stale_file = "apps/adhoc-sweep/pages/old.tsx"
    db_session.add(FileIndex(
        path=stale_file,
        content_hash="cafef00d",
    ))
    await db_session.flush()

    monkeypatch.setattr(mcp_apps, "get_tool_db", _fake_db_cm(db_session))

    deleted = {"paths": []}

    async def _ok_delete(self, path):  # noqa: ANN001
        deleted["paths"].append(path)

    monkeypatch.setattr(FileStorageService, "delete_file", _ok_delete)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_apps.push_files(
        context,
        files={},
        delete_missing_prefix="apps/adhoc-sweep",
    )

    payload = result.model_dump() if hasattr(result, "model_dump") else result
    text = str(payload)
    assert SOLUTION_MANAGED_MESSAGE not in text, text
    assert stale_file in deleted["paths"], deleted["paths"]


async def test_mcp_push_files_allows_unmanaged(db_session, monkeypatch):
    """An ad-hoc (non-managed) app's files still push — the guard is a no-op for them."""
    from src.services.app_storage import AppStorageService
    from src.services.file_storage import FileStorageService
    from src.services.mcp_server.tools import apps as mcp_apps

    monkeypatch.setattr(mcp_apps, "get_tool_db", _fake_db_cm(db_session))

    wrote = {"repo": False}

    async def _ok_write_file(self, path, content, updated_by):  # noqa: ANN001
        wrote["repo"] = True

    async def _noop_write_preview(self, *args, **kwargs):  # noqa: ANN001
        pass

    monkeypatch.setattr(FileStorageService, "write_file", _ok_write_file)
    monkeypatch.setattr(AppStorageService, "write_preview_file", _noop_write_preview)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_apps.push_files(
        context,
        files={"apps/adhoc-app/pages/index.tsx": "export default () => null;"},
    )

    payload = result.model_dump() if hasattr(result, "model_dump") else result
    text = str(payload)
    assert SOLUTION_MANAGED_MESSAGE not in text, text
    assert wrote["repo"] is True


async def _two_install_apps(db, slug: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Two solution-managed Application rows sharing one slug across two orgs —
    the multi-install shape (criterion 9, permitted by the per-install partial
    unique indexes). Returns (org_a_id, org_b_id, app_a_id, app_b_id)."""
    from src.models.orm.applications import Application
    from src.models.orm.organizations import Organization
    from src.models.orm.solutions import Solution

    org_a = Organization(id=uuid.uuid4(), name=f"A-{uuid.uuid4().hex[:6]}", created_by="dev@x")
    org_b = Organization(id=uuid.uuid4(), name=f"B-{uuid.uuid4().hex[:6]}", created_by="dev@x")
    db.add_all([org_a, org_b])
    await db.flush()

    app_ids: list[uuid.UUID] = []
    for org in (org_a, org_b):
        sol = Solution(
            id=uuid.uuid4(), slug=f"mcp-{uuid.uuid4().hex[:8]}", name="MCP",
            organization_id=org.id,
        )
        db.add(sol)
        await db.flush()
        aid = uuid.uuid4()
        db.add(Application(
            id=aid,
            name=f"app_{uuid.uuid4().hex[:8]}",
            slug=slug,
            organization_id=org.id,
            solution_id=sol.id,
            repo_path=f"solutions/{sol.slug}/apps/{slug}",
            created_by="system",
        ))
        await db.flush()
        app_ids.append(aid)
    return org_a.id, org_b.id, app_ids[0], app_ids[1]


async def test_mcp_get_app_multi_install_slug_does_not_error(db_session, monkeypatch):
    """A slug held by two installs must resolve to ONE app for a platform admin
    (prefer caller-org row), not blow up with MultipleResultsFound."""
    from src.services.app_storage import AppStorageService
    from src.services.mcp_server.tools import apps as mcp_apps

    slug = f"multi-{uuid.uuid4().hex[:8]}"
    org_a, _org_b, app_a, app_b = await _two_install_apps(db_session, slug)

    monkeypatch.setattr(mcp_apps, "get_tool_db", _fake_db_cm(db_session))

    async def _no_files(self, app_id, target):  # noqa: ANN001
        return []

    monkeypatch.setattr(AppStorageService, "list_files", _no_files)

    context = SimpleNamespace(is_platform_admin=True, org_id=org_a, user_id=uuid.uuid4())
    result = await mcp_apps.get_app(context, app_slug=slug)

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert "Multiple rows" not in text, text
    assert "Error getting app" not in text, text
    # Caller-org row preferred over the other install's row.
    assert str(app_a) in text, text
    assert str(app_b) not in text, text


async def test_mcp_get_app_dependencies_multi_install_slug_does_not_error(db_session, monkeypatch):
    """Same multi-install seed for get_app_dependencies: one deterministic app,
    no MultipleResultsFound."""
    from src.services.mcp_server.tools import apps as mcp_apps

    slug = f"multi-{uuid.uuid4().hex[:8]}"
    org_a, _org_b, app_a, app_b = await _two_install_apps(db_session, slug)

    monkeypatch.setattr(mcp_apps, "get_tool_db", _fake_db_cm(db_session))

    context = SimpleNamespace(is_platform_admin=True, org_id=org_a, user_id=uuid.uuid4())
    result = await mcp_apps.get_app_dependencies(context, app_slug=slug)

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert "Multiple rows" not in text, text
    assert "Error getting dependencies" not in text, text
    assert str(app_a) in text, text
    assert str(app_b) not in text, text


async def test_mcp_create_app_allows_repo_slug_shadowing_solution(db_session, monkeypatch):
    """A SOLUTION-managed row (another org's install) holding slug X must not
    block create_app(slug=X) — the partial unique index only constrains the
    solution_id IS NULL namespace."""
    from src.models.orm.organizations import Organization
    from src.models.orm.solutions import Solution
    from src.models.orm.applications import Application
    from src.services.file_storage import FileStorageService
    from src.services.mcp_server.tools import apps as mcp_apps

    slug = f"shadow-{uuid.uuid4().hex[:8]}"

    org_a = Organization(id=uuid.uuid4(), name=f"A-{uuid.uuid4().hex[:6]}", created_by="dev@x")
    org_b = Organization(id=uuid.uuid4(), name=f"B-{uuid.uuid4().hex[:6]}", created_by="dev@x")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    sol = Solution(
        id=uuid.uuid4(), slug=f"mcp-{uuid.uuid4().hex[:8]}", name="MCP",
        organization_id=org_b.id,
    )
    db_session.add(sol)
    await db_session.flush()
    db_session.add(Application(
        id=uuid.uuid4(),
        name=f"app_{uuid.uuid4().hex[:8]}",
        slug=slug,
        organization_id=org_b.id,
        solution_id=sol.id,
        repo_path=f"solutions/{sol.slug}/apps/{slug}",
        created_by="system",
    ))
    await db_session.flush()

    monkeypatch.setattr(mcp_apps, "get_tool_db", _fake_db_cm(db_session))

    async def _noop_write(self, *args, **kwargs):  # noqa: ANN001
        pass

    monkeypatch.setattr(FileStorageService, "write_file", _noop_write)

    context = SimpleNamespace(
        is_platform_admin=True, org_id=org_a.id, user_id=uuid.uuid4()
    )
    result = await mcp_apps.create_app(
        context,
        name=f"Shadow {slug}",
        slug=slug,
        scope="organization",
        organization_id=str(org_a.id),
    )

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert "already exists" not in text, text
    assert "Created application" in text, text


async def _managed_agent_with_tool(db) -> tuple[uuid.UUID, uuid.UUID]:
    """A solution-managed agent with one AgentTool binding. Returns (agent_id,
    workflow_id of the tool)."""
    from src.models.orm.agents import Agent, AgentTool
    from src.models.orm.solutions import Solution
    from src.models.orm.workflows import Workflow

    sol = Solution(id=uuid.uuid4(), slug=f"mcp-{uuid.uuid4().hex[:8]}", name="MCP", organization_id=None)
    db.add(sol)
    await db.flush()
    wf = Workflow(
        id=uuid.uuid4(), name="tool_wf", function_name="run", path="workflows/t.py",
        type="tool", organization_id=None, is_active=True,
    )
    db.add(wf)
    aid = uuid.uuid4()
    db.add(Agent(
        id=aid, name=f"a_{uuid.uuid4().hex[:8]}", system_prompt="hi",
        organization_id=None, solution_id=sol.id, created_by="test",
    ))
    await db.flush()
    db.add(AgentTool(agent_id=aid, workflow_id=wf.id))
    await db.flush()
    return aid, wf.id


async def test_mcp_update_agent_refuses_managed_without_deleting_tools(db_session, monkeypatch):
    """Codex #13: update_agent on a solution-managed agent returns the read-only
    error AND does NOT bulk-delete its AgentTool bindings (the Core delete must
    not run / persist)."""
    from contextlib import asynccontextmanager

    from sqlalchemy import func, select

    from src.models.orm.agents import AgentTool
    from src.services.mcp_server.tools import agents as mcp_agents

    aid, _wf = await _managed_agent_with_tool(db_session)

    @asynccontextmanager
    async def _fake_tool_db(_context):
        yield db_session

    monkeypatch.setattr(mcp_agents, "get_tool_db", _fake_tool_db)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_agents.update_agent(context, agent_id=str(aid), tool_ids=[])

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    # The binding SURVIVED — the bulk delete never persisted.
    count = (await db_session.execute(
        select(func.count()).select_from(AgentTool).where(AgentTool.agent_id == aid)
    )).scalar()
    assert count == 1


async def _managed_form_with_field(db) -> uuid.UUID:
    from src.models.orm.forms import Form, FormField
    from src.models.orm.solutions import Solution

    sol = Solution(id=uuid.uuid4(), slug=f"mcp-{uuid.uuid4().hex[:8]}", name="MCP", organization_id=None)
    db.add(sol)
    await db.flush()
    fid = uuid.uuid4()
    db.add(Form(
        id=fid, name=f"f_{uuid.uuid4().hex[:8]}", organization_id=None, solution_id=sol.id,
        created_by="test",
    ))
    await db.flush()
    db.add(FormField(id=uuid.uuid4(), form_id=fid, name="field1", type="text", label="F1", position=0))
    await db.flush()
    return fid


async def test_mcp_update_form_refuses_managed_without_deleting_fields(db_session, monkeypatch):
    """Codex #13: update_form on a solution-managed form returns the read-only
    error AND does NOT bulk-delete its FormField rows."""
    from contextlib import asynccontextmanager

    from sqlalchemy import func, select

    from src.models.orm.forms import FormField
    from src.services.mcp_server.tools import forms as mcp_forms

    fid = await _managed_form_with_field(db_session)

    @asynccontextmanager
    async def _fake_tool_db(_context):
        yield db_session

    monkeypatch.setattr(mcp_forms, "get_tool_db", _fake_tool_db)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_forms.update_form(
        context, form_id=str(fid), fields=[{"name": "new", "field_type": "text", "label": "New"}]
    )

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    count = (await db_session.execute(
        select(func.count()).select_from(FormField).where(FormField.form_id == fid)
    )).scalar()
    assert count == 1
