"""
Chat E2E Tests.

Tests chat conversation and message operations.
Requires LLM configuration to be set for message sending tests.
"""

import logging
from uuid import UUID, uuid4

import pytest

from src.models.enums import MessageRole
from src.models.orm import Conversation, Message
from src.services.chat_attachments import ChatAttachmentService

logger = logging.getLogger(__name__)


# =============================================================================
# Conversation CRUD Tests
# =============================================================================


class TestConversationsCRUD:
    """Test conversation CRUD operations."""

    def test_create_conversation(
        self,
        e2e_client,
        platform_admin,
        test_chat_agent,
    ):
        """Test creating a conversation with an agent."""
        response = e2e_client.post(
            "/api/chat/conversations",
            json={
                "agent_id": test_chat_agent["id"],
                "channel": "chat",
                "title": "Test Conversation",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201, f"Create conversation failed: {response.text}"

        data = response.json()
        assert data["agent_id"] == test_chat_agent["id"]
        assert data["channel"] == "chat"
        assert data["title"] == "Test Conversation"
        assert data["is_active"] is True
        assert data["message_count"] == 0
        assert "id" in data

    def test_create_conversation_with_nonexistent_agent(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test creating a conversation with nonexistent agent returns 404."""
        import uuid
        fake_agent_id = str(uuid.uuid4())

        response = e2e_client.post(
            "/api/chat/conversations",
            json={
                "agent_id": fake_agent_id,
                "channel": "chat",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_list_conversations_empty(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test listing conversations when none exist."""
        response = e2e_client.get(
            "/api/chat/conversations",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_conversations(
        self,
        e2e_client,
        platform_admin,
        test_conversation,
    ):
        """Test listing user's conversations."""
        response = e2e_client.get(
            "/api/chat/conversations",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        # Should include the test conversation
        conv_ids = [c["id"] for c in data]
        assert test_conversation["id"] in conv_ids

    def test_get_conversation(
        self,
        e2e_client,
        platform_admin,
        test_conversation,
    ):
        """Test getting a conversation by ID."""
        response = e2e_client.get(
            f"/api/chat/conversations/{test_conversation['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == test_conversation["id"]
        assert data["agent_name"] is not None

    def test_get_conversation_not_found(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting nonexistent conversation returns 404."""
        import uuid
        fake_id = str(uuid.uuid4())

        response = e2e_client.get(
            f"/api/chat/conversations/{fake_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_delete_conversation(
        self,
        e2e_client,
        platform_admin,
        test_conversation,
    ):
        """Test soft deleting a conversation."""
        response = e2e_client.delete(
            f"/api/chat/conversations/{test_conversation['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204

        # Verify it's not listed by default (inactive)
        response = e2e_client.get(
            "/api/chat/conversations",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        conv_ids = [c["id"] for c in response.json()]
        assert test_conversation["id"] not in conv_ids


class TestChatAttachments:
    """Files can be uploaded, previewed, downloaded, and remain owner-scoped."""

    def test_attachment_upload_and_content_access(
        self,
        e2e_client,
        platform_admin,
        org1_user,
        test_conversation,
    ):
        auth_headers = {"Authorization": platform_admin.headers["Authorization"]}
        upload = e2e_client.post(
            f"/api/chat/conversations/{test_conversation['id']}/attachments",
            files=[("files", ("notes.txt", b"attachment preview", "text/plain"))],
            headers=auth_headers,
        )
        assert upload.status_code == 200, upload.text
        attachment = upload.json()["attachments"][0]
        assert attachment["filename"] == "notes.txt"
        assert attachment["content_type"] == "text/plain"

        content_url = (
            f"/api/chat/conversations/{test_conversation['id']}"
            f"/attachments/{attachment['id']}/content"
        )
        preview = e2e_client.get(content_url, headers=platform_admin.headers)
        assert preview.status_code == 200
        assert preview.content == b"attachment preview"
        assert preview.headers["content-disposition"].startswith("inline;")

        download = e2e_client.get(
            f"{content_url}?download=true", headers=platform_admin.headers
        )
        assert download.status_code == 200
        assert download.headers["content-disposition"].startswith("attachment;")

        forbidden = e2e_client.get(content_url, headers=org1_user.headers)
        assert forbidden.status_code == 404

        discard = e2e_client.delete(
            f"/api/chat/conversations/{test_conversation['id']}"
            f"/attachments/{attachment['id']}",
            headers=platform_admin.headers,
        )
        assert discard.status_code == 204
        missing = e2e_client.get(content_url, headers=platform_admin.headers)
        assert missing.status_code == 404

    def test_sdk_document_artifact_returns_readable_opaque_reference(
        self,
        e2e_client,
        platform_admin,
    ):
        response = e2e_client.post(
            "/api/sdk/artifacts/document",
            json={
                "filename": "e2e-brief",
                "format": "pdf",
                "title": "E2E brief",
                "sections": [
                    {
                        "heading": "Decision",
                        "paragraphs": ["Ship the artifact path."],
                    }
                ],
            },
            headers=platform_admin.headers,
        )

        assert response.status_code == 200, response.text
        artifact = response.json()
        assert artifact["type"] == "bifrost_artifact"
        assert artifact["id"]
        assert artifact["filename"] == "E2E Brief.pdf"
        assert artifact["content_type"] == "application/pdf"
        assert "path" not in artifact
        assert "location" not in artifact

        content = e2e_client.get(
            f"/api/sdk/artifacts/{artifact['id']}/content",
            headers=platform_admin.headers,
        )
        assert content.status_code == 200, content.text
        assert content.content.startswith(b"%PDF-")

        library = e2e_client.get("/api/chat/artifacts", headers=platform_admin.headers)
        listed = next(item for item in library.json() if item["id"] == artifact["id"])
        assert listed["conversation_id"] is None

    def test_sdk_can_store_and_read_workflow_produced_bytes(
        self,
        e2e_client,
        platform_admin,
    ):
        upload_headers = {
            key: value
            for key, value in platform_admin.headers.items()
            if key.lower() != "content-type"
        }
        workspace_id = uuid4()
        stored = e2e_client.post(
            f"/api/sdk/artifacts?workspace_id={workspace_id}",
            files={"file": ("Workflow Notes.md", b"# Ready", "text/markdown")},
            headers=upload_headers,
        )

        assert stored.status_code == 200, stored.text
        ref = stored.json()
        assert ref == {
            "type": "bifrost_artifact",
            "id": ref["id"],
            "filename": "Workflow Notes.md",
            "content_type": "text/markdown",
            "size_bytes": 7,
        }
        content = e2e_client.get(
            f"/api/sdk/artifacts/{ref['id']}/content",
            headers=platform_admin.headers,
        )
        assert content.content == b"# Ready"
        workspace = e2e_client.get(
            f"/api/sdk/artifacts?workspace_id={workspace_id}",
            headers=platform_admin.headers,
        )
        assert workspace.status_code == 200, workspace.text
        assert workspace.json() == [ref]

    @pytest.mark.asyncio
    async def test_artifact_library_lists_renames_and_deletes_owned_files(
        self,
        e2e_client,
        db_session,
        platform_admin,
        org1_user,
        test_conversation,
    ):
        conversation_id = UUID(test_conversation["id"])
        conversation = await db_session.get(Conversation, conversation_id)
        assert conversation is not None
        message = Message(
            conversation_id=conversation_id,
            role=MessageRole.TOOL_CALL,
            content=None,
            tool_name="create_text_artifact",
            tool_state="completed",
            sequence=1,
        )
        db_session.add(message)
        await db_session.flush()
        artifact = await ChatAttachmentService(db_session).store_generated(
            conversation_id=conversation_id,
            message_id=message.id,
            filename="Welcome Page.html",
            content_type="text/html",
            content=b"<!doctype html><title>Welcome</title>",
        )
        await db_session.commit()

        library = e2e_client.get("/api/chat/artifacts", headers=platform_admin.headers)
        assert library.status_code == 200, library.text
        listed = next(item for item in library.json() if item["id"] == str(artifact.id))
        assert listed["filename"] == "Welcome Page.html"
        assert listed["kind"] == "artifact"
        assert listed["conversation_id"] == test_conversation["id"]
        assert "created_at" in listed

        forbidden = e2e_client.patch(
            f"/api/chat/artifacts/{artifact.id}",
            json={"filename": "Not Yours.html"},
            headers=org1_user.headers,
        )
        assert forbidden.status_code == 404

        renamed = e2e_client.patch(
            f"/api/chat/artifacts/{artifact.id}",
            json={"filename": "Bifrost Welcome.html"},
            headers=platform_admin.headers,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["filename"] == "Bifrost Welcome.html"

        deleted = e2e_client.delete(
            f"/api/chat/artifacts/{artifact.id}", headers=platform_admin.headers
        )
        assert deleted.status_code == 204
        library_after = e2e_client.get(
            "/api/chat/artifacts", headers=platform_admin.headers
        )
        assert all(item["id"] != str(artifact.id) for item in library_after.json())


# =============================================================================
# Conversation Access Control Tests
# =============================================================================


class TestConversationsAccessControl:
    """Test conversation access control."""

    def test_user_can_only_see_own_conversations(
        self,
        e2e_client,
        platform_admin,
        org1_user,
        test_chat_agent,
    ):
        """Test that users can only see their own conversations."""
        # Create conversation as platform admin
        response = e2e_client.post(
            "/api/chat/conversations",
            json={
                "agent_id": test_chat_agent["id"],
                "channel": "chat",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201
        admin_conv_id = response.json()["id"]

        # Org user should not see admin's conversation
        response = e2e_client.get(
            "/api/chat/conversations",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        conv_ids = [c["id"] for c in response.json()]
        assert admin_conv_id not in conv_ids

        # Clean up
        e2e_client.delete(
            f"/api/chat/conversations/{admin_conv_id}",
            headers=platform_admin.headers,
        )

    def test_user_cannot_access_other_users_conversation(
        self,
        e2e_client,
        platform_admin,
        org1_user,
        test_conversation,
    ):
        """Test that users cannot access other users' conversations."""
        response = e2e_client.get(
            f"/api/chat/conversations/{test_conversation['id']}",
            headers=org1_user.headers,
        )
        assert response.status_code == 404

    def test_user_cannot_delete_other_users_conversation(
        self,
        e2e_client,
        org1_user,
        test_conversation,
    ):
        """Test that users cannot delete other users' conversations."""
        response = e2e_client.delete(
            f"/api/chat/conversations/{test_conversation['id']}",
            headers=org1_user.headers,
        )
        assert response.status_code == 404

    def test_user_cannot_create_conversation_with_cross_org_agent(
        self,
        e2e_client,
        platform_admin,
        org1,
        org2_user,
    ):
        """A user must not be able to chat with an agent in another org.

        Pins the cross-tenant rule: even if the agent is access_level=
        ``authenticated`` (which used to mean "any logged-in user can
        access"), the org boundary still applies. The agent is created in
        Org 1 by an admin. A user from Org 2 attempts to start a
        conversation with it. Must be rejected with 403.

        Before the chat ``_check_agent_access`` was rerouted through
        ``AgentRepository`` (commit f4184ef5+), this scenario succeeded
        because the helper checked access_level + roles but not
        organization_id.
        """
        # Admin creates an Org 1 agent that's "authenticated" (the most
        # permissive non-public access level).
        agent_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": "Cross-Org Leak Test Agent",
                "description": "Should not be reachable from Org 2",
                "system_prompt": "Test only.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "organization_id": org1["id"],
            },
            headers=platform_admin.headers,
        )
        assert agent_resp.status_code == 201, agent_resp.text
        agent = agent_resp.json()

        try:
            # Org 2 user attempts to create a conversation with this agent.
            resp = e2e_client.post(
                "/api/chat/conversations",
                json={
                    "agent_id": agent["id"],
                    "channel": "chat",
                    "title": "Should not work",
                },
                headers=org2_user.headers,
            )
            assert resp.status_code == 403, (
                f"CROSS-TENANT LEAK: Org 2 user was able to create a "
                f"conversation with an Org 1 'authenticated' agent. "
                f"Status: {resp.status_code}, body: {resp.text[:300]}"
            )
        finally:
            # Best-effort cleanup — never let teardown fail the test. The
            # state-reset between e2e runs will scrub anything left behind.
            try:
                e2e_client.delete(
                    f"/api/agents/{agent['id']}",
                    headers=platform_admin.headers,
                )
            except Exception as cleanup_exc:
                logger.debug(f"agent cleanup skipped: {cleanup_exc}")


# =============================================================================
# Message Tests
# =============================================================================


class TestMessages:
    """Test message operations."""

    def test_get_messages_empty(
        self,
        e2e_client,
        platform_admin,
        test_conversation,
    ):
        """Test getting messages from empty conversation."""
        response = e2e_client.get(
            f"/api/chat/conversations/{test_conversation['id']}/messages",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_get_messages_from_nonexistent_conversation(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting messages from nonexistent conversation returns 404."""
        import uuid
        fake_id = str(uuid.uuid4())

        response = e2e_client.get(
            f"/api/chat/conversations/{fake_id}/messages",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_send_message_without_llm_config(
        self,
        e2e_client,
        platform_admin,
        test_conversation,
        llm_config_cleanup,
    ):
        """Test sending message fails gracefully without LLM config."""
        response = e2e_client.post(
            f"/api/chat/conversations/{test_conversation['id']}/messages",
            json={"message": "Hello, agent!"},
            headers=platform_admin.headers,
        )
        # Should fail with 500 because LLM is not configured
        assert response.status_code == 500

    def test_send_message_to_nonexistent_conversation(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test sending message to nonexistent conversation returns 404."""
        import uuid
        fake_id = str(uuid.uuid4())

        response = e2e_client.post(
            f"/api/chat/conversations/{fake_id}/messages",
            json={"message": "Hello!"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


class TestMessagesWithLLM:
    """Test message operations that require LLM configuration."""

    def test_send_message_and_get_response(
        self,
        e2e_client,
        platform_admin,
        test_conversation,
        llm_anthropic_configured,
    ):
        """Test sending a message and receiving a response."""
        import time

        # Retry logic for transient API errors (rate limiting, overloaded, etc.)
        max_retries = 3
        for attempt in range(max_retries):
            response = e2e_client.post(
                f"/api/chat/conversations/{test_conversation['id']}/messages",
                json={"message": "Say 'Hello Test' and nothing else."},
                headers=platform_admin.headers,
                timeout=30.0,  # LLM calls can take a few seconds
            )

            if response.status_code == 200:
                break
            elif response.status_code == 500:
                error_text = response.text.lower()
                if "overloaded" in error_text or "rate" in error_text:
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
            # For other errors, fail immediately
            break

        assert response.status_code == 200, f"Send message failed: {response.text}"

        data = response.json()
        assert "content" in data
        assert "message_id" in data
        # The response should contain something
        assert len(data["content"]) > 0

    def test_messages_are_persisted(
        self,
        e2e_client,
        platform_admin,
        test_conversation,
        llm_anthropic_configured,
    ):
        """Test that messages are persisted after sending."""
        import time

        # Retry logic for transient API errors (rate limiting, overloaded, etc.)
        max_retries = 3
        for attempt in range(max_retries):
            # Send a message
            response = e2e_client.post(
                f"/api/chat/conversations/{test_conversation['id']}/messages",
                json={"message": "Reply with the word 'test' only."},
                headers=platform_admin.headers,
                timeout=30.0,
            )

            if response.status_code == 200:
                break
            elif response.status_code == 500:
                error_text = response.text.lower()
                if "overloaded" in error_text or "rate" in error_text:
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
            # For other errors, fail immediately
            break

        assert response.status_code == 200

        # Get messages - should have user message and assistant response
        response = e2e_client.get(
            f"/api/chat/conversations/{test_conversation['id']}/messages",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        messages = response.json()
        assert len(messages) >= 2  # At least user message + assistant response

        # Find user and assistant messages
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_chat_agent(e2e_client, platform_admin):
    """Create a test agent for chat tests."""
    response = e2e_client.post(
        "/api/agents",
        json={
            "name": "E2E Chat Test Agent",
            "description": "Agent for chat E2E testing",
            "system_prompt": "You are a helpful test assistant. Keep your responses brief.",
            "channels": ["chat"],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, f"Failed to create test agent: {response.text}"
    agent = response.json()

    yield agent

    # Cleanup - delete the agent
    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}",
            headers=platform_admin.headers,
        )
    except Exception as e:
        # Best-effort fixture cleanup; teardown shouldn't fail the test
        logger.debug(f"fixture cleanup error: {e}")


@pytest.fixture
def test_conversation(e2e_client, platform_admin, test_chat_agent):
    """Create a test conversation for use in tests."""
    response = e2e_client.post(
        "/api/chat/conversations",
        json={
            "agent_id": test_chat_agent["id"],
            "channel": "chat",
            "title": "E2E Test Conversation",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, f"Failed to create test conversation: {response.text}"
    conversation = response.json()

    yield conversation

    # Cleanup - delete the conversation
    try:
        e2e_client.delete(
            f"/api/chat/conversations/{conversation['id']}",
            headers=platform_admin.headers,
        )
    except Exception as e:
        # Best-effort fixture cleanup; teardown shouldn't fail the test
        logger.debug(f"fixture cleanup error: {e}")
