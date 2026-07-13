"""Unit: compute_setup_status unifies config + connection declarations.

Config items get ``kind="config"`` and ``is_set`` from a matching Config value
in the install scope. Connection items get ``kind="connection"`` and ``is_set``
purely from whether a GLOBAL Integration with that name exists (integrations are
global — no org filter). ``has_oauth`` is a warn-only flag and ``connected`` is
informational; neither gates ``setup_complete``.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.config import Config
from src.models.orm.integrations import Integration
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.solutions.setup_status import compute_setup_status

pytestmark = pytest.mark.asyncio


async def test_config_item_carries_kind_config(db_session: AsyncSession):
    # Unique key: compute_setup_status matches Config rows by (key, org) only,
    # so a generic key like "api_key" can read a value another test left global.
    key = f"cfg_key_{uuid4().hex[:8]}"
    sol = Solution(id=uuid4(), slug="cfg", name="CFG", organization_id=None)
    db_session.add(sol)
    await db_session.flush()
    db_session.add(
        SolutionConfigSchema(
            solution_id=sol.id, key=key, type="string", required=True,
            position=0, description="needed",
        )
    )
    await db_session.flush()

    status = await compute_setup_status(db_session, sol)
    cfg = [i for i in status.items if i.kind == "config"]
    assert len(cfg) == 1
    assert cfg[0].key == key
    assert cfg[0].is_set is False
    assert status.setup_complete is False

    # Provide the value globally → complete.
    db_session.add(
        Config(key=key, value="xyz", organization_id=None, updated_by="test")
    )
    await db_session.flush()
    status2 = await compute_setup_status(db_session, sol)
    assert status2.items[0].is_set is True
    assert status2.setup_complete is True


async def test_connection_item_satisfied_when_integration_exists(db_session: AsyncSession):
    sol = Solution(id=uuid4(), slug="s", name="S", organization_id=None)
    db_session.add(sol)
    db_session.add(Integration(name="HaloPSA"))
    await db_session.flush()
    db_session.add(
        SolutionConnectionSchema(
            solution_id=sol.id, integration_name="HaloPSA", position=0,
            template={"name": "HaloPSA", "config_schema": [], "oauth": {"provider_name": "p"}},
        )
    )
    await db_session.flush()

    status = await compute_setup_status(db_session, sol)
    conn = [i for i in status.items if i.kind == "connection"]
    assert len(conn) == 1
    assert conn[0].is_set is True
    assert conn[0].has_oauth is True
    assert status.setup_complete is True  # no required configs, connection exists


async def test_connection_item_unmet_when_integration_absent(db_session: AsyncSession):
    sol = Solution(id=uuid4(), slug="s2", name="S2", organization_id=None)
    db_session.add(sol)
    await db_session.flush()
    db_session.add(
        SolutionConnectionSchema(
            solution_id=sol.id, integration_name="Ghost", position=0,
            template={"name": "Ghost", "config_schema": [], "oauth": None},
        )
    )
    await db_session.flush()

    status = await compute_setup_status(db_session, sol)
    conn = [i for i in status.items if i.kind == "connection"][0]
    assert conn.is_set is False
    assert conn.has_oauth is False
    assert status.setup_complete is False


async def test_non_public_endpoint_workflow_requires_active_key(db_session: AsyncSession):
    sol = Solution(id=uuid4(), slug="endpoint", name="Endpoint", organization_id=None)
    db_session.add(sol)
    wf = Workflow(
        id=uuid4(),
        solution_id=sol.id,
        name="inbound_sync",
        display_name="Inbound Sync",
        path="workflows/inbound.py",
        function_name="run",
        endpoint_enabled=True,
        public_endpoint=False,
        allowed_methods=["GET", "POST"],
        is_active=True,
    )
    db_session.add(wf)
    await db_session.flush()

    status = await compute_setup_status(db_session, sol)
    endpoint_items = [i for i in status.items if i.kind == "workflow_endpoint_key"]
    assert len(endpoint_items) == 1
    assert endpoint_items[0].key == str(wf.id)
    assert endpoint_items[0].workflow_id == str(wf.id)
    assert endpoint_items[0].workflow_name == "Inbound Sync"
    assert endpoint_items[0].type == "workflow_endpoint_key"
    assert endpoint_items[0].required is True
    assert endpoint_items[0].is_set is False
    assert status.setup_complete is False

    wf.api_key_hash = "hash"
    wf.api_key_enabled = True
    await db_session.flush()

    status2 = await compute_setup_status(db_session, sol)
    endpoint_item = [i for i in status2.items if i.kind == "workflow_endpoint_key"][0]
    assert endpoint_item.is_set is True
    assert status2.setup_complete is True


async def test_public_endpoint_workflow_does_not_require_key(db_session: AsyncSession):
    sol = Solution(id=uuid4(), slug="public-endpoint", name="Public Endpoint", organization_id=None)
    db_session.add(sol)
    db_session.add(
        Workflow(
            id=uuid4(),
            solution_id=sol.id,
            name="Public Hook",
            path="workflows/public.py",
            function_name="run",
            endpoint_enabled=True,
            public_endpoint=True,
            is_active=True,
        )
    )
    await db_session.flush()

    status = await compute_setup_status(db_session, sol)
    assert [i for i in status.items if i.kind == "workflow_endpoint_key"] == []
    assert status.setup_complete is True
