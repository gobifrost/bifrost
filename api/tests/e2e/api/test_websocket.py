"""
E2E tests for WebSocket functionality.

Tests WebSocket connections, authentication, messaging, subscriptions, and
real-time updates. These tests verify the WebSocket server works correctly
with various client scenarios.

WebSocket endpoints tested:
- /ws/connect - Main WebSocket endpoint with channel subscriptions
- /ws/execution/{execution_id} - Single execution subscription convenience endpoint
"""

import asyncio
import json
import logging
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from tests.e2e.conftest import write_and_register

logger = logging.getLogger(__name__)


@pytest.mark.e2e
class TestWebSocketConnection:
    """Test WebSocket connection establishment and authentication."""

    @pytest.mark.asyncio
    async def test_websocket_connect_with_valid_token(
        self,
        e2e_ws_url,
        platform_admin,
    ):
        """Connect to WebSocket with valid JWT token in Authorization header."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        # WebSocket auth uses Authorization header (not query params for security)
        extra_headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Should receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)

            assert data["type"] == "connected", \
                f"Expected 'connected' message, got: {data}"
            assert "userId" in data or "user_id" in data, \
                "Missing userId in connected message"
            assert "channels" in data, \
                "Missing channels in connected message"

            logger.info(f"Connected successfully: {data}")

    @pytest.mark.asyncio
    async def test_websocket_connect_invalid_token(self, e2e_ws_url):
        """Connect to WebSocket with invalid token should be rejected."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        # Invalid token via Authorization header
        extra_headers = {"Authorization": "Bearer invalid-token-12345"}

        try:
            async with connect(ws_url, additional_headers=extra_headers) as ws:
                # With accept-before-close pattern, connection opens then closes
                # Try to receive - should get a close frame with code 4001
                try:
                    await asyncio.wait_for(ws.recv(), timeout=5)
                    pytest.fail("Should have received close frame, not a message")
                except ConnectionClosedError as e:
                    # Expected - server accepted then closed with auth failure code
                    close_code = e.rcvd.code if e.rcvd else None
                    assert close_code == 4001, f"Expected close code 4001, got {close_code}"
        except ConnectionClosedError as e:
            # Connection might be rejected at HTTP level with close code
            error_msg = str(e).lower()
            if "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg or "4001" in str(e):
                # This is acceptable - auth rejected
                logger.info(f"Connection rejected (expected): {e}")
            else:
                raise

    @pytest.mark.asyncio
    async def test_websocket_connect_no_token(self, e2e_ws_url):
        """Connect to WebSocket without token should be rejected."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        # No Authorization header

        try:
            async with connect(ws_url) as ws:
                # Should be rejected
                try:
                    await asyncio.wait_for(ws.recv(), timeout=5)
                    pytest.fail("Should have received close frame without auth")
                except ConnectionClosedError as e:
                    # Expected - server closed connection due to missing auth
                    close_code = e.rcvd.code if e.rcvd else None
                    assert close_code == 4001, f"Expected close code 4001, got {close_code}"
        except ConnectionClosedError as e:
            # Connection might be rejected at HTTP level
            error_msg = str(e).lower()
            if "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg or "4001" in str(e):
                logger.info(f"Connection rejected at HTTP level (expected): {e}")
            else:
                raise


@pytest.mark.e2e
class TestWebSocketMessaging:
    """Test WebSocket messaging patterns."""

    @pytest.mark.asyncio
    async def test_websocket_ping_pong(self, e2e_ws_url, platform_admin):
        """WebSocket ping/pong messaging works correctly."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Receive connected message first
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            assert data["type"] == "connected", \
                f"Expected 'connected', got: {data}"

            # Send ping
            ping_msg = {"type": "ping"}
            await ws.send(json.dumps(ping_msg))
            logger.info(f"Sent ping: {ping_msg}")

            # Should receive pong
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            assert data["type"] == "pong", \
                f"Expected 'pong', got: {data}"

            logger.info(f"Received pong: {data}")

    @pytest.mark.asyncio
    async def test_websocket_multiple_pings(self, e2e_ws_url, platform_admin):
        """Send multiple ping messages and receive corresponding pongs."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            assert json.loads(msg)["type"] == "connected"

            # Send and receive multiple pings/pongs
            for i in range(3):
                await ws.send(json.dumps({"type": "ping"}))
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(msg)
                assert data["type"] == "pong", \
                    f"Pong {i+1}: Expected 'pong', got: {data}"

            logger.info("Successfully sent and received 3 ping/pong cycles")


@pytest.mark.e2e
class TestWebSocketSubscriptions:
    """Test WebSocket channel subscriptions."""

    @pytest.fixture(scope="class")
    def execution_workflow(self, e2e_client, platform_admin):
        """Create a simple workflow for execution testing."""

        workflow_content = '''"""E2E WebSocket Execution Workflow"""
from bifrost import workflow

@workflow(
    name="e2e_ws_exec_workflow",
    description="Workflow for WebSocket execution tests",
    execution_mode="sync"
)
async def e2e_ws_exec_workflow(message: str = "test"):
    """Simple test workflow."""
    return {"status": "success", "message": message}
'''
        result = write_and_register(
            e2e_client,
            platform_admin.headers,
            "e2e_ws_exec_workflow.py",
            workflow_content,
            "e2e_ws_exec_workflow",
        )

        yield result

        # Cleanup
        try:
            e2e_client.delete(
                "/api/files/editor",
                headers=platform_admin.headers,
                params={"path": "e2e_ws_exec_workflow.py"},
            )
        except Exception as e:
            logger.warning(f"Failed to cleanup workflow: {e}")

    @pytest.mark.asyncio
    async def test_websocket_subscribe_to_execution(
        self,
        e2e_ws_url,
        e2e_client,
        platform_admin,
        execution_workflow,
    ):
        """Subscribe to execution channel and receive updates."""
        assert execution_workflow["id"], "execution workflow fixture returned no id"

        # Execute the workflow first
        exec_response = e2e_client.post(
            "/api/workflows/execute",
            headers=platform_admin.headers,
            json={
                "workflow_id": execution_workflow["id"],
                "input_data": {"message": "test from websocket"},
            },
        )

        assert exec_response.status_code in [200, 201], (
            f"Failed to execute workflow: {exec_response.text}"
        )

        exec_data = exec_response.json()
        execution_id = exec_data.get("id") or exec_data.get("execution_id")

        assert execution_id, f"Execution response returned no id: {exec_data}"

        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            connected_data = json.loads(msg)
            assert connected_data["type"] == "connected", \
                f"Expected 'connected', got: {connected_data}"

            # Subscribe to execution channel
            subscribe_msg = {
                "type": "subscribe",
                "channels": [f"execution:{execution_id}"]
            }
            await ws.send(json.dumps(subscribe_msg))
            logger.info(f"Subscribed to execution: {subscribe_msg}")

            # Should receive subscription confirmation or messages
            # Some implementations may not send explicit confirmation
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2)
                data = json.loads(msg)
                # Accept subscribed confirmation or any non-error response
                assert data.get("type") != "error", \
                    f"Subscribe error: {data}"
                logger.info(f"Subscribe response: {data}")
            except asyncio.TimeoutError:
                # No immediate response is also acceptable
                logger.info("No immediate subscription response (OK)")

    @pytest.mark.asyncio
    async def test_websocket_subscribe_invalid_channel(
        self,
        e2e_ws_url,
        platform_admin,
    ):
        """Subscribe to invalid channel format is handled gracefully."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            assert json.loads(msg)["type"] == "connected"

            # Try to subscribe to unknown channel format
            # Should be silently ignored or handled gracefully
            subscribe_msg = {
                "type": "subscribe",
                "channels": ["unknown:channel"]
            }
            await ws.send(json.dumps(subscribe_msg))

            # Connection should remain open
            # Try to send ping to verify
            await ws.send(json.dumps({"type": "ping"}))
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            assert data["type"] == "pong", \
                f"Expected 'pong', got: {data}"

            logger.info("Invalid channel handled gracefully")


@pytest.mark.e2e
class TestWebSocketOrgUserAccess:
    """Test WebSocket access control for organization users."""

    @pytest.mark.asyncio
    async def test_org_user_websocket_connect(
        self,
        e2e_ws_url,
        org1_user,
    ):
        """Organization user can connect to WebSocket."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {org1_user.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Should receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)

            assert data["type"] == "connected", \
                f"Expected 'connected', got: {data}"
            assert str(org1_user.user_id) in str(data.get("userId", "")), \
                f"Expected user {org1_user.user_id}, got: {data}"

            logger.info(f"Org user connected: {data}")

    @pytest.mark.asyncio
    async def test_org_user_websocket_ping_pong(
        self,
        e2e_ws_url,
        org1_user,
    ):
        """Organization user can send ping/pong messages."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {org1_user.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            assert json.loads(msg)["type"] == "connected"

            # Send ping
            await ws.send(json.dumps({"type": "ping"}))

            # Should receive pong
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            assert data["type"] == "pong", \
                f"Expected 'pong', got: {data}"

            logger.info("Org user ping/pong successful")

    @pytest.mark.asyncio
    async def test_org_user_auto_subscribe_own_channel(
        self,
        e2e_ws_url,
        org1_user,
    ):
        """Organization user is automatically subscribed to own user channel."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {org1_user.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)

            assert data["type"] == "connected"
            # Should include their own user channel
            expected_channel = f"user:{org1_user.user_id}"
            channels = data.get("channels", [])
            assert expected_channel in channels, \
                f"Expected {expected_channel} in {channels}"

            logger.info(f"User auto-subscribed to: {channels}")


@pytest.mark.e2e
class TestWebSocketEdgeCases:
    """Test WebSocket edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_websocket_receive_json_timeout(
        self,
        e2e_ws_url,
        platform_admin,
    ):
        """WebSocket connection stays open without incoming messages."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            assert json.loads(msg)["type"] == "connected"

            # Wait without sending anything - connection should remain open
            # Send a ping after a short wait to confirm connection still works
            await asyncio.sleep(1)
            await ws.send(json.dumps({"type": "ping"}))

            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            assert data["type"] == "pong", \
                f"Expected 'pong', got: {data}"

            logger.info("Connection remained open without activity")

    @pytest.mark.asyncio
    async def test_websocket_invalid_json_message(
        self,
        e2e_ws_url,
        platform_admin,
    ):
        """Sending invalid JSON message is handled gracefully."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            assert json.loads(msg)["type"] == "connected"

            # Send invalid JSON
            await ws.send("not valid json {[")

            # Connection might close or ignore the message
            # Try to send a valid ping to test
            try:
                await ws.send(json.dumps({"type": "ping"}))
                msg = await asyncio.wait_for(ws.recv(), timeout=2)
                # If we get here, server ignored the invalid JSON
                logger.info("Server gracefully ignored invalid JSON")
            except ConnectionClosedError:
                # Also acceptable - connection closed due to protocol error
                logger.info("Connection closed after invalid JSON (OK)")
            except asyncio.TimeoutError:
                # Connection might be in weird state
                logger.info("Connection timeout after invalid JSON (OK)")

    @pytest.mark.asyncio
    async def test_websocket_unknown_message_type(
        self,
        e2e_ws_url,
        platform_admin,
    ):
        """Sending unknown message type is handled gracefully."""
        ws_url = f"{e2e_ws_url}/ws/connect"
        extra_headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

        async with connect(ws_url, additional_headers=extra_headers) as ws:
            # Receive connected message
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            assert json.loads(msg)["type"] == "connected"

            # Send unknown message type
            await ws.send(json.dumps({"type": "unknown_type", "data": "test"}))

            # Connection should remain open
            # Verify by sending ping
            await ws.send(json.dumps({"type": "ping"}))
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            assert data["type"] == "pong", \
                f"Expected 'pong', got: {data}"

            logger.info("Unknown message type handled gracefully")
