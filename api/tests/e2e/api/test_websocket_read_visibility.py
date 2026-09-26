"""E2E tests for WebSocket channel visibility (own-scope enforcement).

Covers the channels tightened to match the read-visibility law: a regular
user may only subscribe to an agent run they started, the shared
agent-run list channel is bypass-only, event-source channels are
bypass-only, and CLI session channels are owner-only.
"""
import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from websockets.asyncio.client import connect

from src.models.orm.agent_runs import AgentRun
from src.models.orm.cli import CLISession

pytestmark = pytest.mark.asyncio


async def _subscribe(ws, channel):
    await ws.send(json.dumps({"type": "subscribe", "channels": [channel]}))
    msg = await asyncio.wait_for(ws.recv(), timeout=5)
    return json.loads(msg)


@pytest.mark.e2e
class TestAgentRunChannelVisibility:
    async def test_owner_can_subscribe_others_denied(
        self,
        e2e_ws_url,
        e2e_client,
        platform_admin,
        org1_user,
        org2_user,
        db_session: AsyncSession,
    ):
        run_id = uuid4()
        run = AgentRun(
            id=run_id,
            trigger_type="api",
            status="completed",
            org_id=org1_user.organization_id,
            caller_user_id=str(org1_user.user_id),
        )
        db_session.add(run)
        await db_session.commit()

        ws_url = f"{e2e_ws_url}/ws/connect"
        try:
            # Owner can subscribe.
            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {org1_user.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)  # connected
                resp = await _subscribe(ws, f"agent-run:{run_id}")
                assert resp["type"] == "subscribed", resp

            # A different regular user (not the caller) is denied.
            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {org2_user.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                resp = await _subscribe(ws, f"agent-run:{run_id}")
                assert resp["type"] == "error", resp

            # Bypass principal (platform admin) can subscribe to any run.
            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {platform_admin.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                resp = await _subscribe(ws, f"agent-run:{run_id}")
                assert resp["type"] == "subscribed", resp
        finally:
            await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await db_session.commit()

    async def test_shared_agent_runs_channel_is_bypass_only(
        self,
        e2e_ws_url,
        platform_admin,
        org1_user,
    ):
        ws_url = f"{e2e_ws_url}/ws/connect"

        async with connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {org1_user.access_token}"},
        ) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)
            resp = await _subscribe(ws, "agent-runs")
            assert resp["type"] == "error", resp

        async with connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {platform_admin.access_token}"},
        ) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)
            resp = await _subscribe(ws, "agent-runs")
            assert resp["type"] == "subscribed", resp


@pytest.mark.e2e
class TestEventSourceChannelVisibility:
    async def test_regular_user_denied_platform_admin_allowed(
        self,
        e2e_ws_url,
        platform_admin,
        org1_user,
    ):
        ws_url = f"{e2e_ws_url}/ws/connect"
        source_id = uuid4()

        async with connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {org1_user.access_token}"},
        ) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)
            resp = await _subscribe(ws, f"event-source:{source_id}")
            assert resp["type"] == "error", resp

        async with connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {platform_admin.access_token}"},
        ) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)
            resp = await _subscribe(ws, f"event-source:{source_id}")
            assert resp["type"] == "subscribed", resp


@pytest.mark.e2e
class TestCliSessionChannelVisibility:
    async def test_owner_can_subscribe_others_denied(
        self,
        e2e_ws_url,
        platform_admin,
        org1_user,
        org2_user,
        db_session: AsyncSession,
    ):
        session_id = uuid4()
        cli_session = CLISession(
            id=session_id,
            user_id=org1_user.user_id,
            file_path="workflows/example.py",
            workflows={},
        )
        db_session.add(cli_session)
        await db_session.commit()

        ws_url = f"{e2e_ws_url}/ws/connect"
        try:
            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {org1_user.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                resp = await _subscribe(ws, f"cli-session:{session_id}")
                assert resp["type"] == "subscribed", resp

            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {org2_user.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                resp = await _subscribe(ws, f"cli-session:{session_id}")
                assert resp["type"] == "error", resp

            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {platform_admin.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                resp = await _subscribe(ws, f"cli-session:{session_id}")
                assert resp["type"] == "subscribed", resp
        finally:
            await db_session.execute(delete(CLISession).where(CLISession.id == session_id))
            await db_session.commit()


@pytest.mark.e2e
class TestAppChannelVisibility:
    async def test_draft_channel_is_bypass_only(
        self,
        e2e_ws_url,
        e2e_client,
        platform_admin,
        org1_user,
    ):
        slug = f"ws-draft-{uuid4().hex[:8]}"
        create = e2e_client.post(
            "/api/applications",
            headers=org1_user.headers,
            json={"name": slug, "slug": slug, "app_model": "inline_v1"},
        )
        assert create.status_code == 201, create.text
        app_id = create.json()["id"]

        try:
            ws_url = f"{e2e_ws_url}/ws/connect"
            # Even the app's own creator does not get the draft channel —
            # it's bypass-only (authoring surface).
            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {org1_user.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                resp = await _subscribe(ws, f"app:draft:{app_id}")
                assert resp["type"] == "error", resp

            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {platform_admin.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                resp = await _subscribe(ws, f"app:draft:{app_id}")
                assert resp["type"] == "subscribed", resp
        finally:
            e2e_client.delete(f"/api/applications/{app_id}", headers=org1_user.headers)

    async def test_live_channel_uses_app_read_access_check(
        self,
        e2e_ws_url,
        e2e_client,
        platform_admin,
        org1_user,
        org2_user,
    ):
        slug = f"ws-live-{uuid4().hex[:8]}"
        create = e2e_client.post(
            "/api/applications",
            headers=org1_user.headers,
            json={"name": slug, "slug": slug, "app_model": "inline_v1"},
        )
        assert create.status_code == 201, create.text
        app_id = create.json()["id"]

        try:
            ws_url = f"{e2e_ws_url}/ws/connect"
            # Same-org creator can access the live channel.
            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {org1_user.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                resp = await _subscribe(ws, f"app:live:{app_id}")
                assert resp["type"] == "subscribed", resp

            # A user in a different org cannot.
            async with connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {org2_user.access_token}"},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                resp = await _subscribe(ws, f"app:live:{app_id}")
                assert resp["type"] == "error", resp
        finally:
            e2e_client.delete(f"/api/applications/{app_id}", headers=org1_user.headers)
