"""E2E tests for manual lossless compaction (Chat V2 M5, §4.3).

Two layers:

1. **HTTP-only contract** (no LLM key): the ``POST .../compact`` endpoint 404s
   for an unknown conversation and returns a well-formed no-op result for a
   conversation with too few turns to compact.

2. **LLM-gated losslessness** (requires ``ANTHROPIC_API_TEST_KEY``): drive a few
   real turns over the WebSocket, then call ``/compact`` and assert the message
   list is byte-for-byte unchanged (the DB is the source of truth and is never
   modified — §4.1) while the response reports the compaction outcome.
"""

import asyncio
import json
import logging
import os
import time

import pytest
from websockets.asyncio.client import connect

logger = logging.getLogger(__name__)

LLM_KEY = os.environ.get("ANTHROPIC_API_TEST_KEY") or os.environ.get("OPENAPI_API_TEST_KEY")

pytestmark = [pytest.mark.e2e]


# =============================================================================
# HTTP-only contract (runs without an LLM key)
# =============================================================================


class TestCompactEndpointContract:
    def test_compact_unknown_conversation_404(self, e2e_client, platform_admin):
        resp = e2e_client.post(
            "/api/chat/conversations/00000000-0000-0000-0000-000000000000/compact",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 404, resp.text

    def test_compact_empty_conversation_is_noop(self, e2e_client, platform_admin):
        """A brand-new conversation has nothing to compact — well-formed no-op."""
        create = e2e_client.post(
            "/api/chat/conversations",
            json={"channel": "chat", "title": "E2E Compact Empty"},
            headers=platform_admin.headers,
        )
        assert create.status_code == 201, create.text
        conv_id = create.json()["id"]
        try:
            resp = e2e_client.post(
                f"/api/chat/conversations/{conv_id}/compact",
                headers=platform_admin.headers,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["compacted"] is False
            assert body["turns_compacted"] == 0
            assert isinstance(body["message"], str) and body["message"]
        finally:
            e2e_client.delete(
                f"/api/chat/conversations/{conv_id}",
                headers=platform_admin.headers,
            )


# =============================================================================
# LLM-gated losslessness
# =============================================================================


@pytest.fixture(scope="module")
def _compaction_llm(e2e_client, platform_admin):
    """Configure the platform LLM + org default chat model for this module."""
    api_key = os.environ.get("ANTHROPIC_API_TEST_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_TEST_KEY not configured")

    e2e_client.post(
        "/api/admin/llm/config",
        json={
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "api_key": api_key,
            "max_tokens": 1024,
        },
        headers=platform_admin.headers,
    )
    me = e2e_client.get("/auth/me", headers=platform_admin.headers).json()
    org_id = me["organization_id"]
    e2e_client.patch(
        f"/api/organizations/{org_id}",
        json={"default_chat_model": "claude-haiku-4-5-20251001"},
        headers=platform_admin.headers,
    )
    yield
    try:
        e2e_client.delete("/api/admin/llm/config", headers=platform_admin.headers)
    except Exception as e:  # best-effort teardown
        logger.debug("LLM config cleanup error: %s", e)


@pytest.fixture
def _compaction_conversation(e2e_client, platform_admin, _compaction_llm):
    resp = e2e_client.post(
        "/api/chat/conversations",
        json={"channel": "chat", "title": f"E2E Compact {int(time.time() * 1000)}"},
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    conv = resp.json()
    yield conv
    try:
        e2e_client.delete(
            f"/api/chat/conversations/{conv['id']}",
            headers=platform_admin.headers,
        )
    except Exception as e:
        logger.debug("conversation cleanup error: %s", e)


async def _drain_connected(ws, timeout: float = 10.0) -> None:
    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
    assert json.loads(msg).get("type") == "connected"


async def _drain_until_done(ws, timeout: float = 90.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError("Timed out waiting for 'done'")
        data = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        if data.get("type") == "done":
            return
        if data.get("type") == "error":
            raise RuntimeError(f"WS error: {data.get('error')}")


@pytest.mark.skipif(not LLM_KEY, reason="Requires ANTHROPIC_API_TEST_KEY")
@pytest.mark.asyncio
async def test_manual_compact_is_lossless(
    e2e_ws_url, e2e_client, platform_admin, _compaction_conversation
):
    """Manual compaction never modifies the stored messages (§4.1)."""
    conv_id = _compaction_conversation["id"]
    ws_url = f"{e2e_ws_url}/ws/connect"
    headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

    # Drive a few short turns so there is something to (potentially) fold.
    async with connect(ws_url, additional_headers=headers) as ws:
        await _drain_connected(ws)
        for prompt in ("say one", "say two", "say three", "say four"):
            await ws.send(
                json.dumps(
                    {"type": "chat", "conversation_id": conv_id, "message": prompt}
                )
            )
            await _drain_until_done(ws)

    before = e2e_client.get(
        f"/api/chat/conversations/{conv_id}/messages",
        headers=platform_admin.headers,
    ).json()
    assert len(before) >= 8

    # Manual compaction.
    resp = e2e_client.post(
        f"/api/chat/conversations/{conv_id}/compact",
        headers=platform_admin.headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {"compacted", "turns_compacted", "message"}

    # Lossless: the stored message list is byte-for-byte unchanged.
    after = e2e_client.get(
        f"/api/chat/conversations/{conv_id}/messages",
        headers=platform_admin.headers,
    ).json()
    assert [m["id"] for m in after] == [m["id"] for m in before]
    assert [m["content"] for m in after] == [m["content"] for m in before]
