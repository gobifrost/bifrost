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
from src.services.solutions.setup_status import compute_setup_status

pytestmark = pytest.mark.asyncio


async def test_config_item_carries_kind_config(db_session: AsyncSession):
    sol = Solution(id=uuid4(), slug="cfg", name="CFG", organization_id=None)
    db_session.add(sol)
    await db_session.flush()
    db_session.add(
        SolutionConfigSchema(
            solution_id=sol.id, key="api_key", type="string", required=True,
            position=0, description="needed",
        )
    )
    await db_session.flush()

    status = await compute_setup_status(db_session, sol)
    cfg = [i for i in status.items if i.kind == "config"]
    assert len(cfg) == 1
    assert cfg[0].key == "api_key"
    assert cfg[0].is_set is False
    assert status.setup_complete is False

    # Provide the value globally → complete.
    db_session.add(
        Config(key="api_key", value="xyz", organization_id=None, updated_by="test")
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
