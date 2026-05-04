"""Websocket subscription E2E for tables under policies.

Covers the subscribe protocol added in Task 12:
- subscribe accepted when the user satisfies any read rule
- subscribe rejected otherwise (error ack)
- four-way fanout: insert is delivered when a row becomes visible
- subscription_revoked when a policy edit removes the user's read access
- per-connection user filter narrows what messages reach the client
"""

import asyncio
import json
import os
import uuid

import httpx
import pytest
from websockets.asyncio.client import connect


TEST_API_URL = os.environ.get("TEST_API_URL", "http://api:8000")
TEST_WS_URL = TEST_API_URL.replace("http://", "ws://").replace("https://", "wss://")


async def _ws_subscribe(user_token: str, channels: list):
    """Open a ws, drain the `connected` greeting, send subscribe, return (ws, ack).

    The server emits `{"type": "connected", ...}` immediately on open; the
    next inbound message is the response to our subscribe (either
    `{"type": "subscribed", ...}` on success or `{"type": "error", ...}` on
    denial).
    """
    ws = await connect(
        f"{TEST_WS_URL}/ws/connect",
        additional_headers={"Authorization": f"Bearer {user_token}"},
    )
    # Drain the connected greeting first.
    greeting = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
    assert greeting.get("type") == "connected", f"expected greeting, got {greeting}"
    await ws.send(json.dumps({"type": "subscribe", "channels": channels}))
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
    return ws, ack


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_subscribe_with_read_accepted(platform_admin, alice_user):
    """Alice subscribes; everyone-read policy permits."""
    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        r = await client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": f"sub_ok_{uuid.uuid4().hex[:8]}",
                "organization_id": None,
                "policies": {"policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    },
                    {
                        "name": "everyone_read",
                        "actions": ["read"],
                        "when": None,
                    },
                ]},
            },
        )
        assert r.status_code == 201, r.text
        table_id = r.json()["id"]

    ws, ack = await _ws_subscribe(alice_user.access_token, [f"table:{table_id}"])
    try:
        assert ack.get("type") == "subscribed", f"expected subscribed, got {ack}"
        assert ack.get("channel") == f"table:{table_id}"
    finally:
        await ws.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_subscribe_without_read_rejected(platform_admin, alice_user):
    """Alice subscribes to a seeded-only table → rejected."""
    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        r = await client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": f"sub_deny_{uuid.uuid4().hex[:8]}",
                "organization_id": None,
            },  # seeded admin_bypass only
        )
        assert r.status_code == 201, r.text
        table_id = r.json()["id"]

    ws, ack = await _ws_subscribe(alice_user.access_token, [f"table:{table_id}"])
    try:
        assert ack.get("type") == "error", f"expected error ack, got {ack}"
        assert ack.get("channel") == f"table:{table_id}"
    finally:
        await ws.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_receive_insert(platform_admin, alice_user):
    """Alice subscribes, admin inserts → Alice sees insert."""
    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        r = await client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": f"sub_insert_{uuid.uuid4().hex[:8]}",
                "organization_id": None,
                "policies": {"policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    },
                    {"name": "everyone_read", "actions": ["read"], "when": None},
                ]},
            },
        )
        assert r.status_code == 201, r.text
        table_id = r.json()["id"]

        ws, ack = await _ws_subscribe(alice_user.access_token, [f"table:{table_id}"])
        try:
            assert ack.get("type") == "subscribed", f"subscribe failed: {ack}"
            await client.post(
                f"/api/tables/{table_id}/documents",
                headers=platform_admin.headers,
                json={"data": {"x": 1}},
            )
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            assert msg["type"] == "document_change", msg
            assert msg["action"] == "insert", msg
            # `_row_from_doc` flattens JSONB data at the top level — `x` lives
            # alongside id/created_by/etc, not nested under `data`.
            assert msg["row"]["x"] == 1, msg
        finally:
            await ws.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_subscribe_by_name_resolves_to_canonical_channel(platform_admin, alice_user):
    """`useTable(name)` subscribes via `table:<name>`; server resolves to UUID.

    The publisher only emits on `table:<uuid>` channels, so a subscription
    registered against the user-supplied name would silently drop every
    message. The subscribe handler resolves name → UUID and registers under
    the canonical channel.
    """
    table_name = f"sub_byname_{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        r = await client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": table_name,
                "organization_id": None,
                "policies": {"policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    },
                    {"name": "everyone_read", "actions": ["read"], "when": None},
                ]},
            },
        )
        assert r.status_code == 201, r.text
        table_id = r.json()["id"]

        # Subscribe by NAME, not UUID — the channel string the SDK builds
        # when callers do `useTable("ticket_table")`.
        ws, ack = await _ws_subscribe(alice_user.access_token, [f"table:{table_name}"])
        try:
            assert ack.get("type") == "subscribed", f"subscribe failed: {ack}"
            # Server normalizes to canonical UUID channel in the ack so the
            # client/server agree on identity for any future messages.
            assert ack.get("channel") == f"table:{table_id}", ack

            await client.post(
                f"/api/tables/{table_id}/documents",
                headers=platform_admin.headers,
                json={"data": {"x": 7}},
            )
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            assert msg["type"] == "document_change", msg
            assert msg["action"] == "insert", msg
            assert msg["row"]["x"] == 7, msg
        finally:
            await ws.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_subscribe_by_unknown_name_rejected(platform_admin, alice_user):
    """Subscribing to a non-existent table name returns an error ack."""
    bogus = f"nope_{uuid.uuid4().hex[:8]}"
    ws, ack = await _ws_subscribe(alice_user.access_token, [f"table:{bogus}"])
    try:
        assert ack.get("type") == "error", f"expected error ack, got {ack}"
        assert ack.get("channel") == f"table:{bogus}"
    finally:
        await ws.close()


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.skip(
    reason="created_by is a column overwriting JSONB created_by in _row_from_doc; "
    "visibility-gain logic is unit-tested in tests/unit/test_subscription_visibility.py "
    "(Task 12). Expressing this via a custom user_id field is possible but adds little "
    "over the unit coverage."
)
async def test_visibility_gain_emits_insert(platform_admin, alice_user, bob_user):
    """Row originally invisible to Alice (Bob's row) gets reassigned to Alice → insert.

    Skipped: see decorator. The four-way fanout's visibility-gain branch is
    covered at the function level in `decide_visibility_change` unit tests.
    """


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_subscription_revoked_on_policy_change(platform_admin, alice_user):
    """Admin removes read access → Alice's ws gets subscription_revoked."""
    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        r = await client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": f"revoke_{uuid.uuid4().hex[:8]}",
                "organization_id": None,
                "policies": {"policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    },
                    {"name": "everyone_read", "actions": ["read"], "when": None},
                ]},
            },
        )
        assert r.status_code == 201, r.text
        table_id = r.json()["id"]

        ws, ack = await _ws_subscribe(alice_user.access_token, [f"table:{table_id}"])
        try:
            assert ack.get("type") == "subscribed", f"subscribe failed: {ack}"
            # Remove the everyone_read rule
            patch = await client.patch(
                f"/api/tables/{table_id}",
                headers=platform_admin.headers,
                json={"policies": {"policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    },
                ]}},
            )
            assert patch.status_code == 200, patch.text
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            assert msg["type"] == "subscription_revoked", msg
            assert msg["channel"] == f"table:{table_id}"
        finally:
            await ws.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_user_filter_narrows_messages(platform_admin, alice_user):
    """Alice subscribes with status=open filter; messages for status=done are dropped."""
    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        r = await client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": f"filter_{uuid.uuid4().hex[:8]}",
                "organization_id": None,
                "policies": {"policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    },
                    {"name": "everyone_read", "actions": ["read"], "when": None},
                ]},
            },
        )
        assert r.status_code == 201, r.text
        table_id = r.json()["id"]

        # `_row_from_doc` flattens JSONB at top level, so {"row": "status"}
        # resolves to the same value the API stores under data.status.
        ws, ack = await _ws_subscribe(
            alice_user.access_token,
            [{"name": f"table:{table_id}", "filter": {"eq": [{"row": "status"}, "open"]}}],
        )
        try:
            assert ack.get("type") == "subscribed", f"subscribe failed: {ack}"
            # Insert a 'done' row → filter drops it
            await client.post(
                f"/api/tables/{table_id}/documents",
                headers=platform_admin.headers,
                json={"data": {"status": "done"}},
            )
            # Insert an 'open' row → user sees it
            await client.post(
                f"/api/tables/{table_id}/documents",
                headers=platform_admin.headers,
                json={"data": {"status": "open"}},
            )
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            assert msg["type"] == "document_change", msg
            assert msg["action"] == "insert", msg
            # The 'done' row should have been dropped — the first delivered
            # message must be the 'open' one.
            assert msg["row"]["status"] == "open", msg
        finally:
            await ws.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_subscribe_to_other_org_table_rejected(platform_admin, alice_user, org2):
    """Hard org gate: alice (org1) cannot subscribe to an org2-scoped table,
    by NAME or by UUID, even if its policies look permissive.

    Pins the org gate in `_resolve_table_id` (UUID branch returns None for
    cross-org access; name branch filters by org). The subscribe protocol
    surfaces "Table not found" — same as a non-existent table — to avoid
    leaking the existence of cross-org tables.
    """
    org2_id = org2["id"]
    name = f"xorg_sub_{uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        # Org2 table with a permissive read policy (this is what makes the
        # gate the only line of defense — without it, alice could subscribe).
        r = await client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": name,
                "organization_id": org2_id,
                "policies": {"policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    },
                    {"name": "everyone_read", "actions": ["read"], "when": None},
                ]},
            },
        )
        assert r.status_code == 201, r.text
        table_id = r.json()["id"]

    # Subscribe by NAME — should be rejected.
    ws, ack = await _ws_subscribe(alice_user.access_token, [f"table:{name}"])
    try:
        assert ack.get("type") == "error", f"name leak: {ack}"
        assert "not found" in ack.get("message", "").lower()
    finally:
        await ws.close()

    # Subscribe by UUID — also rejected.
    ws, ack = await _ws_subscribe(alice_user.access_token, [f"table:{table_id}"])
    try:
        assert ack.get("type") == "error", f"UUID leak: {ack}"
        assert "not found" in ack.get("message", "").lower()
    finally:
        await ws.close()
