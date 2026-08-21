"""MCP mutations refuse Solution-managed entities without partial writes.

Canonical thin REST adapters (Agents and Forms) are tested at the shared HTTP
guard. Legacy direct-ORM tools remain covered at the session-wide
``before_flush`` guard until their domain reaches the same parity architecture.
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
    from sqlalchemy import select

    from src.models.orm.tables import Table
    from src.services.mcp_server.tools import tables as mcp_tables

    tid = await _managed_table(db_session)

    async def _fake_resolve(_context, kind, value):
        assert (kind, value) == ("table", str(tid))
        return str(tid)

    async def _fake_assemble(_context, fields, *, is_update, scope):
        assert fields["name"] == "hijacked-via-mcp"
        assert is_update is True
        assert scope is None
        return {"name": fields["name"]}

    async def _fake_call_rest(_context, method, path, *, json_body=None, params=None):
        assert (method, path) == ("PATCH", f"/api/tables/{tid}")
        assert json_body == {"name": "hijacked-via-mcp"}
        assert params is None
        return 409, {"detail": SOLUTION_MANAGED_MESSAGE}

    monkeypatch.setattr(mcp_tables, "_resolve_ref", _fake_resolve)
    monkeypatch.setattr(mcp_tables, "_assemble_table_body", _fake_assemble)
    monkeypatch.setattr(mcp_tables, "call_rest", _fake_call_rest)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_tables.bifrost_update_table(
        context,
        table_ref=str(tid),
        name="hijacked-via-mcp",
    )

    # The tool returns an error result carrying the locked read-only message.
    payload = result.model_dump() if hasattr(result, "model_dump") else result
    text = str(payload)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    name = (
        await db_session.execute(select(Table.name).where(Table.id == tid))
    ).scalar_one()
    assert name != "hijacked-via-mcp"


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
    """publish_app delegates the managed-app guard to canonical REST."""
    from src.services.app_storage import AppStorageService
    from src.services.mcp_server.tools import apps as mcp_apps

    aid = await _managed_app(db_session, repo_path="apps/managed-pub")

    # Sentinel: publish() (the preview→live S3 copy) must never be invoked.
    published = {"called": False}

    async def _boom_publish(self, app_id):  # noqa: ANN001
        published["called"] = True
        raise AssertionError("S3 publish must not run for a solution-managed app")

    monkeypatch.setattr(AppStorageService, "publish", _boom_publish)

    async def _resolve(_context, app_ref):  # noqa: ANN001
        assert app_ref == str(aid)
        return str(aid)

    async def _guarded_rest(context, method, path, *, json_body=None, params=None):  # noqa: ANN001
        assert method == "POST"
        assert path == f"/api/applications/{aid}/publish"
        assert json_body == {}
        assert params is None
        return 409, {"detail": SOLUTION_MANAGED_MESSAGE}

    monkeypatch.setattr(mcp_apps, "_resolve_app_ref", _resolve)
    monkeypatch.setattr(mcp_apps, "call_rest", _guarded_rest)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_apps.bifrost_publish_app(context, app_ref=str(aid))

    payload = result.model_dump() if hasattr(result, "model_dump") else result
    text = str(payload)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    assert published["called"] is False


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
    """The REST-backed update preserves the managed Agent and its tool binding."""
    from sqlalchemy import func, select

    from src.models.orm.agents import AgentTool
    from src.services.mcp_server.tools import agents as mcp_agents

    aid, _wf = await _managed_agent_with_tool(db_session)

    async def _fake_resolve(_context, kind, value):
        assert (kind, value) == ("agent", str(aid))
        return str(aid)

    async def _fake_assemble(_context, fields, *, is_update, scope):
        assert is_update is True
        assert scope is None
        return {"tool_ids": fields["tool_ids"]}

    async def _fake_call_rest(_context, method, path, *, json_body=None, params=None):
        assert (method, path) == ("PUT", f"/api/agents/{aid}")
        assert json_body == {"tool_ids": []}
        assert params is None
        return 409, {"detail": SOLUTION_MANAGED_MESSAGE}

    monkeypatch.setattr(mcp_agents, "_resolve_ref", _fake_resolve)
    monkeypatch.setattr(mcp_agents, "_assemble_agent_body", _fake_assemble)
    monkeypatch.setattr(mcp_agents, "call_rest", _fake_call_rest)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_agents.bifrost_update_agent(
        context,
        agent_ref=str(aid),
        tool_ids=[],
    )

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
    """The canonical Form REST boundary refuses a managed Form update."""
    from sqlalchemy import func, select

    from src.models.orm.forms import FormField
    from src.services.mcp_server.tools import forms as mcp_forms

    fid = await _managed_form_with_field(db_session)

    async def _fake_resolve(_context, kind, value):
        assert (kind, value) == ("form", str(fid))
        return str(fid)

    async def _fake_assemble(_context, fields, *, is_update, scope):
        assert fields["form_schema"] == {
            "fields": [{"name": "new", "type": "text", "label": "New"}]
        }
        assert is_update is True
        assert scope is None
        return {"form_schema": fields["form_schema"]}

    async def _fake_call_rest(_context, method, path, *, json_body=None, params=None):
        assert (method, path) == ("PATCH", f"/api/forms/{fid}")
        assert json_body == {
            "form_schema": {
                "fields": [{"name": "new", "type": "text", "label": "New"}]
            }
        }
        assert params is None
        return 409, {"detail": SOLUTION_MANAGED_MESSAGE}

    monkeypatch.setattr(mcp_forms, "_resolve_ref", _fake_resolve)
    monkeypatch.setattr(mcp_forms, "_assemble_form_body", _fake_assemble)
    monkeypatch.setattr(mcp_forms, "call_rest", _fake_call_rest)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_forms.bifrost_update_form(
        context,
        form_ref=str(fid),
        form_schema={
            "fields": [{"name": "new", "type": "text", "label": "New"}]
        },
    )

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    count = (await db_session.execute(
        select(func.count()).select_from(FormField).where(FormField.form_id == fid)
    )).scalar()
    assert count == 1


async def test_mcp_delete_form_refuses_managed(db_session, monkeypatch):
    """The canonical Form REST boundary refuses a managed Form delete."""
    from sqlalchemy import select

    from src.models.orm.forms import Form
    from src.services.mcp_server.tools import forms as mcp_forms

    fid = await _managed_form_with_field(db_session)

    async def _fake_resolve(_context, kind, value):
        assert (kind, value) == ("form", str(fid))
        return str(fid)

    async def _fake_call_rest(_context, method, path, *, json_body=None, params=None):
        assert (method, path) == ("DELETE", f"/api/forms/{fid}")
        assert json_body is None
        assert params == {"purge": False}
        return 409, {"detail": SOLUTION_MANAGED_MESSAGE}

    monkeypatch.setattr(mcp_forms, "_resolve_ref", _fake_resolve)
    monkeypatch.setattr(mcp_forms, "call_rest", _fake_call_rest)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_forms.bifrost_delete_form(context, form_ref=str(fid))

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    is_active = (
        await db_session.execute(select(Form.is_active).where(Form.id == fid))
    ).scalar_one()
    assert is_active is True


# ── audit M-MCP: legacy tools that lacked the EARLY guard ────────────────────
# These returned the locked message only via the before_flush backstop (a raised
# SolutionManagedWriteError wrapped into error_result — but a 500-shaped path
# that leaves the shared session dirty). An explicit early guard makes them
# refuse cleanly BEFORE mutating. The tests assert the locked message AND that
# the entity was not mutated.


async def test_mcp_delete_agent_refuses_managed(db_session, monkeypatch):
    from sqlalchemy import select

    from src.models.orm.agents import Agent
    from src.services.mcp_server.tools import agents as mcp_agents

    aid, _wf = await _managed_agent_with_tool(db_session)

    async def _fake_resolve(_context, kind, value):
        assert (kind, value) == ("agent", str(aid))
        return str(aid)

    async def _fake_call_rest(_context, method, path, *, json_body=None, params=None):
        assert method == "DELETE"
        assert path == f"/api/agents/{aid}"
        assert json_body is None
        assert params is None
        return 409, {"detail": SOLUTION_MANAGED_MESSAGE}

    monkeypatch.setattr(mcp_agents, "_resolve_ref", _fake_resolve)
    monkeypatch.setattr(mcp_agents, "call_rest", _fake_call_rest)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_agents.bifrost_delete_agent(context, agent_ref=str(aid))

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    # The canonical REST endpoint refused the delete, so the agent is unchanged.
    is_active = (await db_session.execute(
        select(Agent.is_active).where(Agent.id == aid)
    )).scalar_one()
    assert is_active is True


async def test_mcp_delete_table_refuses_managed(db_session, monkeypatch):
    from sqlalchemy import select

    from src.models.orm.tables import Table
    from src.services.mcp_server.tools import tables as mcp_tables

    tid = await _managed_table(db_session)

    async def _fake_resolve(_context, kind, value):
        assert (kind, value) == ("table", str(tid))
        return str(tid)

    async def _fake_call_rest(_context, method, path, *, json_body=None, params=None):
        assert (method, path) == ("DELETE", f"/api/tables/{tid}")
        assert json_body is None
        assert params is None
        return 409, {"detail": SOLUTION_MANAGED_MESSAGE}

    monkeypatch.setattr(mcp_tables, "_resolve_ref", _fake_resolve)
    monkeypatch.setattr(mcp_tables, "call_rest", _fake_call_rest)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_tables.bifrost_delete_table(context, table_ref=str(tid))

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    # The table still exists — the delete never ran.
    still = (await db_session.execute(
        select(Table.id).where(Table.id == tid)
    )).scalar_one_or_none()
    assert still == tid


async def test_mcp_update_app_refuses_managed(db_session, monkeypatch):
    from sqlalchemy import select

    from src.models.orm.applications import Application
    from src.services.mcp_server.tools import apps as mcp_apps

    aid = await _managed_app(db_session, repo_path="apps/managed-upd")

    async def _fake_resolve(_context, app_ref):
        assert app_ref == str(aid)
        return str(aid)

    async def _fake_assemble(_context, fields, *, is_update, scope):
        assert fields["name"] == "hijacked"
        assert is_update is True
        assert scope is None
        return {"name": "hijacked"}

    async def _fake_call_rest(_context, method, path, *, json_body=None, params=None):
        assert (method, path) == ("PATCH", f"/api/applications/{aid}")
        assert json_body == {"name": "hijacked"}
        assert params is None
        return 409, {"detail": SOLUTION_MANAGED_MESSAGE}

    monkeypatch.setattr(mcp_apps, "_resolve_app_ref", _fake_resolve)
    monkeypatch.setattr(mcp_apps, "_assemble_app_body", _fake_assemble)
    monkeypatch.setattr(mcp_apps, "call_rest", _fake_call_rest)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_apps.bifrost_update_app(
        context,
        app_ref=str(aid),
        name="hijacked",
    )

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    name = (await db_session.execute(
        select(Application.name).where(Application.id == aid)
    )).scalar_one()
    assert name != "hijacked"


async def test_mcp_update_app_dependencies_refuses_managed(db_session, monkeypatch):
    from sqlalchemy import select

    from src.models.orm.applications import Application
    from src.services.mcp_server.tools import apps as mcp_apps

    aid = await _managed_app(db_session, repo_path="apps/managed-deps")

    async def _fake_resolve(_context, app_ref):
        assert app_ref == str(aid)
        return str(aid)

    async def _fake_call_rest(_context, method, path, *, json_body=None, params=None):
        assert (method, path) == (
            "PUT",
            f"/api/applications/{aid}/dependencies",
        )
        assert json_body == {"left-pad": "1.0.0"}
        assert params is None
        return 409, {"detail": SOLUTION_MANAGED_MESSAGE}

    monkeypatch.setattr(mcp_apps, "_resolve_app_ref", _fake_resolve)
    monkeypatch.setattr(mcp_apps, "call_rest", _fake_call_rest)

    context = SimpleNamespace(is_platform_admin=True, org_id=None, user_id=uuid.uuid4())
    result = await mcp_apps.bifrost_update_app_dependencies(
        context,
        app_ref=str(aid),
        dependencies={"left-pad": "1.0.0"},
    )

    text = str(result.model_dump() if hasattr(result, "model_dump") else result)
    assert SOLUTION_MANAGED_MESSAGE in text, text
    deps = (await db_session.execute(
        select(Application.dependencies).where(Application.id == aid)
    )).scalar_one()
    assert deps != {"left-pad": "1.0.0"}
