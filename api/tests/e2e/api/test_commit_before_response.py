"""A 2xx response means the write is already committed and visible to others."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.models.orm.agents import Agent

pytestmark = pytest.mark.asyncio


async def test_created_agent_is_committed_when_response_arrives(
    e2e_client, platform_admin, async_session_factory
) -> None:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Commit Ordering Agent {uuid4().hex[:8]}",
            "description": "test",
            "system_prompt": "test",
            "channels": [],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent_id = UUID(resp.json()["id"])
    try:
        # A brand-new session/connection: it only sees committed rows.
        async with async_session_factory() as other_session:
            found = await other_session.scalar(select(Agent.id).where(Agent.id == agent_id))
        assert found == agent_id
    finally:
        e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)
