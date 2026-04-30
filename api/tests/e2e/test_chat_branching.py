"""E2E tests for chat branching: edit, retry, and per-conversation instructions.

Covers Task 10 of the Chat V2 / M3 backend plan:

1. Editing a user message creates a new sibling pair (user + assistant) while
   retaining the originals — verified by ``GET /api/chat/conversations/{id}/messages``
   and ``POST /api/chat/conversations/{id}/active-leaf`` round-trip.
2. Retrying an assistant message creates a sibling assistant under the same
   user parent — sibling_count reaches 2.
3. Per-conversation ``instructions`` round-trip via PATCH and GET.

LLM gating
----------
Tests #1 and #2 drive a real LLM turn over the WebSocket (there is no mock
seam between ``_process_chat_message`` and the LLM client). They are gated on
``ANTHROPIC_API_TEST_KEY`` / ``OPENAPI_API_TEST_KEY`` via the module-level
skipif marker, mirroring ``tests/e2e/platform/test_agent_connection_pressure.py``.

The instructions test is HTTP-only and would otherwise run without the key,
but it shares the same module-level skip for simplicity — the cost is that
a key-less run also skips one HTTP-only check. Acceptable given the test
costs ~zero LLM calls when the key IS present.
"""

import asyncio
import json
import logging
import os
import time
from uuid import UUID

import pytest
from websockets.asyncio.client import connect

logger = logging.getLogger(__name__)

LLM_KEY = os.environ.get("ANTHROPIC_API_TEST_KEY") or os.environ.get("OPENAPI_API_TEST_KEY")
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not LLM_KEY,
        reason="Requires LLM API key (ANTHROPIC_API_TEST_KEY or OPENAPI_API_TEST_KEY)",
    ),
]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def _llm_configured(e2e_client, platform_admin):
    """Configure the platform LLM AND the platform_admin org's default chat
    model for the duration of this module.

    The chat WS path resolves the model via ``shared/model_resolver.py``,
    which requires either an entry in ``Organization.allowed_chat_models``
    or ``Organization.default_chat_model`` to be set — configuring only the
    platform-level LLM is not enough.
    """
    api_key = os.environ.get("ANTHROPIC_API_TEST_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_TEST_KEY not configured")

    # The resolver hands the chosen ``model_id`` directly to the LLM client,
    # and the Anthropic provider does NOT accept a provider prefix. The
    # canonical platform_models entry for Anthropic Haiku is
    # ``claude-haiku-4-5`` (see ``api/shared/data/models.json``); the dated
    # ID ``claude-haiku-4-5-20251001`` is also a valid Anthropic API model.
    org_default_model = "claude-haiku-4-5-20251001"

    config = {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "api_key": api_key,
        "max_tokens": 1024,
    }
    response = e2e_client.post(
        "/api/admin/llm/config",
        json=config,
        headers=platform_admin.headers,
    )
    assert response.status_code == 200, f"Failed to configure LLM: {response.text}"

    # Resolve platform_admin's organization id and set the org default model
    # so the chat WS path can pick a model. Capture the previous value for
    # restoration on teardown.
    me_resp = e2e_client.get("/auth/me", headers=platform_admin.headers)
    assert me_resp.status_code == 200, f"GET /auth/me failed: {me_resp.text}"
    org_id = me_resp.json()["organization_id"]
    assert org_id, "platform_admin has no organization_id"

    org_get_resp = e2e_client.get(
        f"/api/organizations/{org_id}",
        headers=platform_admin.headers,
    )
    assert org_get_resp.status_code == 200, (
        f"GET /api/organizations/{org_id} failed: {org_get_resp.text}"
    )
    previous_default = org_get_resp.json().get("default_chat_model")

    patch_resp = e2e_client.patch(
        f"/api/organizations/{org_id}",
        json={"default_chat_model": org_default_model},
        headers=platform_admin.headers,
    )
    assert patch_resp.status_code == 200, (
        f"PATCH org default_chat_model failed: {patch_resp.text}"
    )

    yield config

    try:
        e2e_client.delete("/api/admin/llm/config", headers=platform_admin.headers)
    except Exception as e:
        # Best-effort fixture cleanup; teardown shouldn't fail the test
        logger.debug(f"LLM config fixture cleanup error: {e}")
    try:
        # PATCH semantics here treat ``None`` as "no change" (see the
        # router: ``if request.default_chat_model is not None: ...``), so
        # we can't restore an originally-null value via this endpoint. If
        # there was a previous value, restore it; otherwise leave the model
        # set — module teardown shouldn't fail tests, and the next module
        # configures its own LLM state.
        if previous_default is not None:
            e2e_client.patch(
                f"/api/organizations/{org_id}",
                json={"default_chat_model": previous_default},
                headers=platform_admin.headers,
            )
    except Exception as e:
        logger.debug(f"org default_chat_model restore error: {e}")


@pytest.fixture
def branching_agent(e2e_client, platform_admin, _llm_configured):
    """Create a chat-channel agent with no tools, asking for terse replies."""
    response = e2e_client.post(
        "/api/agents",
        json={
            "name": f"E2E Branching Test Agent {int(time.time() * 1000)}",
            "description": "Minimal chat agent for branching e2e tests",
            "system_prompt": (
                "You are a test assistant. Reply with one short sentence. "
                "Do not use any tools."
            ),
            "channels": ["chat"],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, f"Failed to create agent: {response.text}"
    agent = response.json()
    yield agent

    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}",
            headers=platform_admin.headers,
        )
    except Exception as e:
        logger.debug(f"agent fixture cleanup error: {e}")


@pytest.fixture
def branching_conversation(e2e_client, platform_admin, branching_agent):
    """Create an isolated conversation for each test."""
    response = e2e_client.post(
        "/api/chat/conversations",
        json={
            "agent_id": branching_agent["id"],
            "channel": "chat",
            "title": "E2E Branching Conversation",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, f"Failed to create conversation: {response.text}"
    conversation = response.json()
    yield conversation

    try:
        e2e_client.delete(
            f"/api/chat/conversations/{conversation['id']}",
            headers=platform_admin.headers,
        )
    except Exception as e:
        logger.debug(f"conversation fixture cleanup error: {e}")


# =============================================================================
# Helpers
# =============================================================================


async def _drain_connected(ws, timeout: float = 10.0) -> None:
    """Drain the initial ``connected`` frame after a WS handshake."""
    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
    data = json.loads(msg)
    assert data.get("type") == "connected", f"Expected 'connected', got {data}"


async def _drain_until_done(ws, timeout: float = 90.0) -> str | None:
    """Drain frames until the terminal ``done`` frame.

    Returns the assistant ``message_id`` reported on the ``done`` frame
    (may be ``None`` if the LLM produced no content). Raises on ``error``
    frames or timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError("Timed out waiting for 'done' frame")
        msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
        data = json.loads(msg)
        frame_type = data.get("type")
        if frame_type == "done":
            return data.get("message_id")
        if frame_type == "error":
            raise RuntimeError(f"WS error: {data.get('error')}")
        # Other frames (delta, message_start, assistant_message_end,
        # title_update, etc.) are streamed mid-turn — ignore.


def _get_messages(e2e_client, platform_admin, conversation_id: str) -> list[dict]:
    """Fetch all messages in a conversation."""
    resp = e2e_client.get(
        f"/api/chat/conversations/{conversation_id}/messages",
        headers=platform_admin.headers,
    )
    assert resp.status_code == 200, f"GET messages failed: {resp.text}"
    return resp.json()


# =============================================================================
# Tests
# =============================================================================


class TestChatBranching:
    """Edit / retry / instructions branching contracts over WS + REST."""

    @pytest.mark.asyncio
    async def test_edit_creates_branch_and_retains_old_messages(
        self,
        e2e_ws_url,
        e2e_client,
        platform_admin,
        branching_conversation,
    ):
        """Editing a user message creates a sibling user+asst pair under the
        same parent, leaving the original pair intact and reachable via
        active-leaf navigation.
        """
        conv_id = branching_conversation["id"]
        ws_url = f"{e2e_ws_url}/ws/connect"
        headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        # 1. Drive an initial chat turn to populate (user1, asst1).
        async with connect(ws_url, additional_headers=headers) as ws:
            await _drain_connected(ws)
            await ws.send(json.dumps({
                "type": "chat",
                "conversation_id": conv_id,
                "message": "say hello",
            }))
            await _drain_until_done(ws)

        messages_v1 = _get_messages(e2e_client, platform_admin, conv_id)
        assert len(messages_v1) == 2, f"Expected 2 messages after first turn, got {messages_v1}"
        user1 = next(m for m in messages_v1 if m["role"] == "user")
        asst1 = next(m for m in messages_v1 if m["role"] == "assistant")
        assert user1["parent_message_id"] is None
        assert asst1["parent_message_id"] == user1["id"]

        # 2. Edit user1 — should create user2 (sibling under same parent: None)
        # and a fresh asst2 child of user2.
        async with connect(ws_url, additional_headers=headers) as ws:
            await _drain_connected(ws)
            await ws.send(json.dumps({
                "type": "edit_message",
                "conversation_id": conv_id,
                "target_message_id": user1["id"],
                "content": "say goodbye",
            }))
            await _drain_until_done(ws)

        messages_v2 = _get_messages(e2e_client, platform_admin, conv_id)
        assert len(messages_v2) == 4, (
            f"Expected 4 messages after edit (old pair + new pair), got "
            f"{[(m['role'], m['content']) for m in messages_v2]}"
        )

        users = [m for m in messages_v2 if m["role"] == "user"]
        assistants = [m for m in messages_v2 if m["role"] == "assistant"]
        assert len(users) == 2
        assert len(assistants) == 2

        # Both user messages share the same (None) parent and have sibling_count 2.
        for u in users:
            assert u["parent_message_id"] is None, (
                f"Edited user message should share None parent, got {u}"
            )
            assert u["sibling_count"] == 2, (
                f"Each user sibling should report sibling_count=2, got {u}"
            )

        user2 = next(u for u in users if u["id"] != user1["id"])
        asst2 = next(a for a in assistants if a["id"] != asst1["id"])
        assert asst2["parent_message_id"] == user2["id"], (
            f"New assistant should be child of new user, got asst2={asst2}"
        )

        # sibling_index reflects sequence order: original (older) is 0, edit is 1.
        original_user = next(u for u in users if u["id"] == user1["id"])
        assert original_user["sibling_index"] == 0, (
            f"Original user should be sibling_index=0, got {original_user}"
        )
        assert user2["sibling_index"] == 1, (
            f"Edited user sibling should be sibling_index=1, got {user2}"
        )

        # 3. Switch active leaf back to the original assistant.
        resp = e2e_client.post(
            f"/api/chat/conversations/{conv_id}/active-leaf",
            json={"message_id": asst1["id"]},
            headers=platform_admin.headers,
        )
        assert resp.status_code == 200, f"active-leaf switch failed: {resp.text}"
        body = resp.json()
        assert body["active_leaf_message_id"] == asst1["id"], (
            f"Expected active leaf {asst1['id']}, got {body['active_leaf_message_id']}"
        )

    @pytest.mark.asyncio
    async def test_retry_creates_assistant_branch(
        self,
        e2e_ws_url,
        e2e_client,
        platform_admin,
        branching_conversation,
    ):
        """Retrying an assistant message creates a sibling assistant under
        the same user parent, with sibling_count == 2.
        """
        conv_id = branching_conversation["id"]
        ws_url = f"{e2e_ws_url}/ws/connect"
        headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        # 1. Drive an initial chat turn.
        async with connect(ws_url, additional_headers=headers) as ws:
            await _drain_connected(ws)
            await ws.send(json.dumps({
                "type": "chat",
                "conversation_id": conv_id,
                "message": "say hi",
            }))
            await _drain_until_done(ws)

        messages_v1 = _get_messages(e2e_client, platform_admin, conv_id)
        assert len(messages_v1) == 2, messages_v1
        user1 = next(m for m in messages_v1 if m["role"] == "user")
        asst1 = next(m for m in messages_v1 if m["role"] == "assistant")

        # 2. Retry asst1.
        async with connect(ws_url, additional_headers=headers) as ws:
            await _drain_connected(ws)
            await ws.send(json.dumps({
                "type": "retry_message",
                "conversation_id": conv_id,
                "target_message_id": asst1["id"],
            }))
            await _drain_until_done(ws)

        messages_v2 = _get_messages(e2e_client, platform_admin, conv_id)
        assistants = [m for m in messages_v2 if m["role"] == "assistant"]
        users = [m for m in messages_v2 if m["role"] == "user"]
        assert len(users) == 1, (
            f"Retry should not create a new user message, got users={users}"
        )
        assert len(assistants) == 2, (
            f"Retry should produce two assistants, got {assistants}"
        )

        # Both assistants share user1 as parent.
        for a in assistants:
            assert a["parent_message_id"] == user1["id"], (
                f"Both assistants must share the original user as parent, got {a}"
            )
            assert a["sibling_count"] == 2, (
                f"Each assistant sibling should report sibling_count=2, got {a}"
            )

        # IDs are distinct and look like UUIDs.
        ids = {a["id"] for a in assistants}
        assert len(ids) == 2
        for a_id in ids:
            UUID(a_id)  # raises if malformed

        # sibling_index reflects sequence order: original (older) assistant is 0,
        # the retry is 1.
        original_asst = next(a for a in assistants if a["id"] == asst1["id"])
        retry_asst = next(a for a in assistants if a["id"] != asst1["id"])
        assert original_asst["sibling_index"] == 0, (
            f"Original assistant should be sibling_index=0, got {original_asst}"
        )
        assert retry_asst["sibling_index"] == 1, (
            f"Retry assistant should be sibling_index=1, got {retry_asst}"
        )

    def test_per_conversation_instructions_persist_and_apply(
        self,
        e2e_client,
        platform_admin,
        branching_conversation,
    ):
        """PATCH instructions, then GET — value round-trips."""
        conv_id = branching_conversation["id"]
        instructions = "Always answer in haiku form."

        patch_resp = e2e_client.patch(
            f"/api/chat/conversations/{conv_id}",
            json={"instructions": instructions},
            headers=platform_admin.headers,
        )
        assert patch_resp.status_code == 200, f"PATCH failed: {patch_resp.text}"
        patch_body = patch_resp.json()
        assert patch_body["instructions"] == instructions, (
            f"PATCH response missing instructions, got {patch_body}"
        )

        get_resp = e2e_client.get(
            f"/api/chat/conversations/{conv_id}",
            headers=platform_admin.headers,
        )
        assert get_resp.status_code == 200, f"GET failed: {get_resp.text}"
        get_body = get_resp.json()
        assert get_body["instructions"] == instructions, (
            f"GET did not return persisted instructions, got {get_body}"
        )
