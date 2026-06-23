"""Policy-aware websocket subscriptions for SDK file browsers."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from urllib.parse import quote

import httpx
import pytest
from websockets.asyncio.client import connect


TEST_API_URL = os.environ.get("TEST_API_URL", "http://api:8000")
TEST_WS_URL = TEST_API_URL.replace("http://", "ws://").replace("https://", "wss://")


async def _ws_subscribe(user_token: str, channel: str, *, scope: str | None = None):
    ws = await connect(
        f"{TEST_WS_URL}/ws/connect",
        additional_headers={"Authorization": f"Bearer {user_token}"},
    )
    greeting = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
    assert greeting.get("type") == "connected", f"expected greeting, got {greeting}"
    item: dict[str, str] = {"name": channel}
    if scope is not None:
        item["scope"] = scope
    await ws.send(json.dumps({"type": "subscribe", "channels": [item]}))
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
    return ws, ack


async def _put_allow_policy(client: httpx.AsyncClient, headers: dict, *, location: str, scope: str, prefix: str) -> None:
    encoded = quote(prefix, safe="")
    resp = await client.put(
        f"/api/files/policies/{encoded}",
        headers=headers,
        params={"location": location, "scope": scope},
        json={
            "policies": {
                "policies": [
                    {
                        "name": "everyone_full_access",
                        "actions": ["read", "write", "delete", "list"],
                        "when": None,
                    }
                ]
            }
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_file_subscribe_without_policy_is_rejected(org1_user, org1):
    location = f"wssub-{uuid.uuid4().hex[:8]}"
    channel = f"files:{location}:gallery"

    ws, ack = await _ws_subscribe(org1_user.access_token, channel, scope=org1["id"])
    try:
        assert ack.get("type") == "error", ack
        assert ack.get("channel") == channel
        assert ack.get("message") == "Access denied"
    finally:
        await ws.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_file_subscriber_receives_policy_allowed_write(platform_admin, org1_user, org1):
    location = f"wssub-{uuid.uuid4().hex[:8]}"
    prefix = "gallery"
    channel = f"files:{location}:{prefix}"
    path = f"{prefix}/a.txt"

    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        await _put_allow_policy(
            client,
            platform_admin.headers,
            location=location,
            scope=org1["id"],
            prefix=prefix,
        )

        ws, ack = await _ws_subscribe(org1_user.access_token, channel, scope=org1["id"])
        try:
            assert ack.get("type") == "subscribed", ack
            assert ack.get("channel") == channel

            write = await client.post(
                "/api/files/write",
                headers=org1_user.headers,
                json={
                    "path": path,
                    "content": "hello",
                    "location": location,
                    "scope": org1["id"],
                    "mode": "cloud",
                },
            )
            assert write.status_code == 204, write.text

            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            assert msg["type"] == "file_change", msg
            assert msg["action"] == "write", msg
            assert msg["path"] == path, msg
            assert msg["channel"] == channel, msg
        finally:
            await ws.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_file_subscription_revoked_after_policy_delete(platform_admin, org1_user, org1):
    location = f"wssub-{uuid.uuid4().hex[:8]}"
    prefix = "gallery"
    channel = f"files:{location}:{prefix}"

    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        await _put_allow_policy(
            client,
            platform_admin.headers,
            location=location,
            scope=org1["id"],
            prefix=prefix,
        )

        ws, ack = await _ws_subscribe(org1_user.access_token, channel, scope=org1["id"])
        try:
            assert ack.get("type") == "subscribed", ack

            delete = await client.delete(
                f"/api/files/policies/{quote(prefix, safe='')}",
                headers=platform_admin.headers,
                params={"location": location, "scope": org1["id"]},
            )
            assert delete.status_code == 204, delete.text

            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            assert msg == {
                "type": "subscription_revoked",
                "channel": channel,
            }
        finally:
            await ws.close()
