"""
E2E tests for the Event System.

Tests the full event lifecycle:
- Event Source CRUD (webhook type)
- Event Subscriptions
- Webhook receiver (public endpoint, no auth)
- Event delivery tracking
- Delivery retry
"""

import hashlib
import hmac
import uuid

import pytest

from tests.e2e.conftest import poll_until, write_and_register


# Simple test workflow content for subscriptions
TEST_WORKFLOW_CONTENT = '''"""E2E Events Test Workflow"""
from bifrost import workflow

@workflow(
    name="e2e_events_test_workflow",
    description="Test workflow for event subscription E2E tests",
)
async def e2e_events_test_workflow(event: dict) -> dict:
    """Receives event data and returns it."""
    return {
        "status": "received",
        "event_type": event.get("event_type"),
        "data": event.get("data"),
    }
'''


@pytest.fixture(scope="module")
def test_workflow(e2e_client, platform_admin):
    """
    Create a test workflow file for event subscriptions.

    Writes the workflow file, registers the function, and cleans up after tests.
    """
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_events_test_workflow.py",
        TEST_WORKFLOW_CONTENT,
        "e2e_events_test_workflow",
    )

    yield result

    # Cleanup: delete the workflow file
    e2e_client.delete(
        "/api/files/editor?path=e2e_events_test_workflow.py",
        headers=platform_admin.headers,
    )


@pytest.fixture
def event_source(e2e_client, platform_admin):
    """Create a test event source with webhook config."""
    source_name = f"E2E Test Webhook {uuid.uuid4().hex[:8]}"

    response = e2e_client.post(
        "/api/events/sources",
        headers=platform_admin.headers,
        json={
            "name": source_name,
            "source_type": "webhook",
            "webhook": {
                "adapter_name": "generic",
                "config": {},
            },
        },
    )
    assert response.status_code == 201, f"Failed to create event source: {response.text}"
    source = response.json()

    yield source

    # Cleanup: delete the event source
    e2e_client.delete(
        f"/api/events/sources/{source['id']}",
        headers=platform_admin.headers,
    )


@pytest.fixture
def org_event_source(e2e_client, platform_admin, org1):
    """Create an event source scoped to org1."""
    source_name = f"E2E Org Webhook {uuid.uuid4().hex[:8]}"

    response = e2e_client.post(
        "/api/events/sources",
        headers=platform_admin.headers,
        json={
            "name": source_name,
            "source_type": "webhook",
            "organization_id": org1["id"],
            "webhook": {
                "adapter_name": "generic",
                "config": {},
            },
        },
    )
    assert response.status_code == 201, f"Failed to create org event source: {response.text}"
    source = response.json()

    yield source

    # Cleanup
    e2e_client.delete(
        f"/api/events/sources/{source['id']}",
        headers=platform_admin.headers,
    )


@pytest.fixture
def subscription(e2e_client, platform_admin, event_source, test_workflow):
    """Create a subscription linking event source to workflow."""
    response = e2e_client.post(
        f"/api/events/sources/{event_source['id']}/subscriptions",
        headers=platform_admin.headers,
        json={
            "workflow_id": test_workflow["id"],
            "event_type": None,  # Match all events
        },
    )
    assert response.status_code == 201, f"Failed to create subscription: {response.text}"
    sub = response.json()

    yield sub

    # Cleanup handled by event_source deletion (cascade)


# =============================================================================
# TestEventSourceCRUD - Event Source Management
# =============================================================================


@pytest.mark.e2e
class TestEventSourceCRUD:
    """Test event source CRUD operations."""

    def test_list_adapters(self, e2e_client, platform_admin):
        """List available webhook adapters."""
        response = e2e_client.get(
            "/api/events/adapters",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List adapters failed: {response.text}"
        data = response.json()
        assert "adapters" in data
        # Should have at least the generic adapter
        adapter_names = [a["name"] for a in data["adapters"]]
        assert "generic" in adapter_names

    def test_create_webhook_source(self, e2e_client, platform_admin):
        """Platform admin creates webhook event source."""
        source_name = f"E2E Create Test {uuid.uuid4().hex[:8]}"

        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": source_name,
                "source_type": "webhook",
                "webhook": {
                    "adapter_name": "generic",
                    "config": {},
                },
            },
        )
        assert response.status_code == 201, f"Create source failed: {response.text}"

        source = response.json()
        assert source["name"] == source_name
        assert source["source_type"] == "webhook"
        assert source["is_active"] is True
        assert source["webhook"] is not None

        # Cleanup
        e2e_client.delete(
            f"/api/events/sources/{source['id']}",
            headers=platform_admin.headers,
        )

    def test_create_webhook_source_returns_callback_url(self, e2e_client, platform_admin):
        """Verify callback URL uses the configured public URL."""
        source_name = f"E2E Callback URL Test {uuid.uuid4().hex[:8]}"

        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": source_name,
                "source_type": "webhook",
                "webhook": {
                    "adapter_name": "generic",
                    "config": {},
                },
            },
        )
        assert response.status_code == 201

        source = response.json()
        callback_url = source["webhook"]["callback_url"]

        assert callback_url == f"http://localhost:8000/api/hooks/{source['id']}"

        # Cleanup
        e2e_client.delete(
            f"/api/events/sources/{source['id']}",
            headers=platform_admin.headers,
        )

    def test_list_event_sources(self, e2e_client, platform_admin, event_source):
        """List event sources (platform admin sees all)."""
        response = e2e_client.get(
            "/api/events/sources",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List sources failed: {response.text}"

        data = response.json()
        assert "items" in data
        assert "total" in data

        # Should find our test source
        source_ids = [s["id"] for s in data["items"]]
        assert event_source["id"] in source_ids

    def test_get_event_source(self, e2e_client, platform_admin, event_source):
        """Get event source by ID."""
        response = e2e_client.get(
            f"/api/events/sources/{event_source['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get source failed: {response.text}"

        source = response.json()
        assert source["id"] == event_source["id"]
        assert source["name"] == event_source["name"]

    def test_update_event_source(self, e2e_client, platform_admin, event_source):
        """Update event source name/status."""
        new_name = f"Updated {uuid.uuid4().hex[:8]}"

        response = e2e_client.patch(
            f"/api/events/sources/{event_source['id']}",
            headers=platform_admin.headers,
            json={
                "name": new_name,
            },
        )
        assert response.status_code == 200, f"Update source failed: {response.text}"

        source = response.json()
        assert source["name"] == new_name

    def test_delete_event_source(self, e2e_client, platform_admin):
        """Hard delete event source (commit 12d4f807 switched from soft to hard delete)."""
        # Create a source to delete
        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": f"To Delete {uuid.uuid4().hex[:8]}",
                "source_type": "webhook",
                "webhook": {"adapter_name": "generic", "config": {}},
            },
        )
        source = response.json()

        # Delete it
        response = e2e_client.delete(
            f"/api/events/sources/{source['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204, f"Delete failed: {response.text}"

        # Verify it's gone (hard delete — row removed, not just deactivated)
        response = e2e_client.get(
            f"/api/events/sources/{source['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_update_event_source_organization_id(self, e2e_client, platform_admin, org_event_source, org1):
        """Update org-scoped source: clear org (set to null), then set it back."""
        source_id = org_event_source["id"]

        # Verify it starts with an org
        assert org_event_source["organization_id"] == org1["id"]

        # Clear org → global
        response = e2e_client.patch(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
            json={"organization_id": None},
        )
        assert response.status_code == 200, f"Clear org failed: {response.text}"
        assert response.json()["organization_id"] is None

        # Set org back
        response = e2e_client.patch(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
            json={"organization_id": org1["id"]},
        )
        assert response.status_code == 200, f"Set org failed: {response.text}"
        assert response.json()["organization_id"] == org1["id"]

    def test_update_event_source_set_organization_id(self, e2e_client, platform_admin, event_source, org1):
        """Assign org to a global event source."""
        source_id = event_source["id"]

        # Starts as global (no org)
        assert event_source["organization_id"] is None

        # Assign org
        response = e2e_client.patch(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
            json={"organization_id": org1["id"]},
        )
        assert response.status_code == 200, f"Set org failed: {response.text}"
        assert response.json()["organization_id"] == org1["id"]

        # Clean up: reset to global so fixture cleanup works
        e2e_client.patch(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
            json={"organization_id": None},
        )

    def test_org_user_cannot_create_source(self, e2e_client, org1_user):
        """Only platform admin can create sources."""
        response = e2e_client.post(
            "/api/events/sources",
            headers=org1_user.headers,
            json={
                "name": "Should Fail",
                "source_type": "webhook",
                "webhook": {"adapter_name": "generic", "config": {}},
            },
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"

    def test_org_user_cannot_list_adapters(self, e2e_client, org1_user):
        """Org users cannot list webhook adapters (platform admin only)."""
        response = e2e_client.get(
            "/api/events/adapters",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"

    def test_org_user_cannot_list_sources(self, e2e_client, org1_user):
        """Org users cannot list event sources (platform admin only)."""
        response = e2e_client.get(
            "/api/events/sources",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"

    def test_org_user_cannot_get_source(self, e2e_client, platform_admin, org1_user):
        """Org users cannot get event source details (platform admin only)."""
        # Create a source as admin first
        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": "Test Source for Auth Check",
                "source_type": "webhook",
                "webhook": {"adapter_name": "generic", "config": {}},
            },
        )
        source = response.json()

        try:
            # Org user should not be able to get it
            response = e2e_client.get(
                f"/api/events/sources/{source['id']}",
                headers=org1_user.headers,
            )
            assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        finally:
            # Cleanup
            e2e_client.delete(
                f"/api/events/sources/{source['id']}",
                headers=platform_admin.headers,
            )


# =============================================================================
# TestEventSubscriptions - Subscription Management
# =============================================================================


@pytest.mark.e2e
class TestEventSubscriptions:
    """Test event subscription operations."""

    def test_create_subscription(
        self, e2e_client, platform_admin, event_source, test_workflow
    ):
        """Create workflow subscription to event source."""
        response = e2e_client.post(
            f"/api/events/sources/{event_source['id']}/subscriptions",
            headers=platform_admin.headers,
            json={
                "workflow_id": test_workflow["id"],
                "event_type": "test.event",
            },
        )
        assert response.status_code == 201, f"Create subscription failed: {response.text}"

        sub = response.json()
        assert sub["workflow_id"] == test_workflow["id"]
        assert sub["event_type"] == "test.event"
        assert sub["is_active"] is True

    def test_list_subscriptions(
        self, e2e_client, platform_admin, event_source, subscription
    ):
        """List subscriptions for an event source."""
        response = e2e_client.get(
            f"/api/events/sources/{event_source['id']}/subscriptions",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List subscriptions failed: {response.text}"

        data = response.json()
        assert "items" in data
        sub_ids = [s["id"] for s in data["items"]]
        assert subscription["id"] in sub_ids

    def test_update_subscription(
        self, e2e_client, platform_admin, event_source, subscription
    ):
        """Update event_type filter, is_active."""
        response = e2e_client.patch(
            f"/api/events/sources/{event_source['id']}/subscriptions/{subscription['id']}",
            headers=platform_admin.headers,
            json={
                "event_type": "updated.event",
                "is_active": False,
            },
        )
        assert response.status_code == 200, f"Update subscription failed: {response.text}"

        sub = response.json()
        assert sub["event_type"] == "updated.event"
        assert sub["is_active"] is False

    def test_delete_subscription(
        self, e2e_client, platform_admin, event_source, test_workflow
    ):
        """Permanently delete subscription."""
        # Create a subscription to delete
        response = e2e_client.post(
            f"/api/events/sources/{event_source['id']}/subscriptions",
            headers=platform_admin.headers,
            json={"workflow_id": test_workflow["id"]},
        )
        sub = response.json()

        # Delete it
        response = e2e_client.delete(
            f"/api/events/sources/{event_source['id']}/subscriptions/{sub['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204, f"Delete subscription failed: {response.text}"


# =============================================================================
# TestWebhookReceiver - Webhook Delivery (PUBLIC ENDPOINT)
# =============================================================================


@pytest.mark.e2e
class TestWebhookReceiver:
    """Test webhook receiver endpoint (public, no auth required)."""

    def test_webhook_returns_202(self, e2e_client, event_source):
        """POST to /api/hooks/{source_id} returns 202."""
        source_id = event_source["id"]

        # No auth headers - public endpoint
        response = e2e_client.post(
            f"/api/hooks/{source_id}",
            json={"test": "data"},
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"
        assert response.text == "Accepted"

    def test_webhook_creates_event(self, e2e_client, platform_admin, event_source):
        """Webhook POST creates Event record."""
        source_id = event_source["id"]

        # Send webhook
        e2e_client.post(
            f"/api/hooks/{source_id}",
            json={"action": "test.created", "data": {"key": "value"}},
        )

        # Poll until event is created
        def find_event():
            response = e2e_client.get(
                f"/api/events/sources/{source_id}/events",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                events = response.json()["items"]
                matching = [
                    e for e in events
                    if e.get("data", {}).get("action") == "test.created"
                ]
                if matching:
                    return matching
            return None

        matching_events = poll_until(find_event, max_wait=5.0)
        assert matching_events is not None, "Event not created within timeout"
        assert len(matching_events) >= 1

    def test_webhook_with_invalid_source_returns_404(self, e2e_client):
        """Invalid UUID returns 404."""
        fake_uuid = str(uuid.uuid4())

        response = e2e_client.post(
            f"/api/hooks/{fake_uuid}",
            json={"test": "data"},
        )

        assert response.status_code == 404

    def test_webhook_with_inactive_source_returns_404(
        self, e2e_client, platform_admin
    ):
        """Inactive source returns 404."""
        # Create and immediately deactivate a source
        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": f"Inactive Source {uuid.uuid4().hex[:8]}",
                "source_type": "webhook",
                "webhook": {"adapter_name": "generic", "config": {}},
            },
        )
        source = response.json()

        # Deactivate it
        e2e_client.delete(
            f"/api/events/sources/{source['id']}",
            headers=platform_admin.headers,
        )

        # Try to send webhook
        response = e2e_client.post(
            f"/api/hooks/{source['id']}",
            json={"test": "data"},
        )

        assert response.status_code == 404

    def test_webhook_supports_get_method(self, e2e_client, event_source):
        """GET method for validation challenges."""
        source_id = event_source["id"]

        response = e2e_client.get(f"/api/hooks/{source_id}")

        # GET should be accepted (for validation callbacks like Graph API)
        assert response.status_code == 202

    def test_webhook_supports_json_body(self, e2e_client, platform_admin, event_source):
        """JSON body is parsed and stored."""
        source_id = event_source["id"]

        payload = {
            "event_type": "user.created",
            "user": {
                "id": 123,
                "email": "test@example.com",
            },
        }

        e2e_client.post(
            f"/api/hooks/{source_id}",
            json=payload,
        )

        # Poll until event is stored
        def find_event():
            response = e2e_client.get(
                f"/api/events/sources/{source_id}/events",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                events = response.json()["items"]
                matching = [
                    e for e in events
                    if e.get("data", {}).get("event_type") == "user.created"
                ]
                if matching:
                    return matching
            return None

        matching = poll_until(find_event, max_wait=5.0)
        assert matching is not None, "Event not stored within timeout"
        assert len(matching) >= 1
        assert matching[0]["data"]["user"]["email"] == "test@example.com"

    def test_webhook_stores_headers(self, e2e_client, platform_admin, event_source):
        """Request headers are stored in event."""
        source_id = event_source["id"]

        e2e_client.post(
            f"/api/hooks/{source_id}",
            json={"header_test": True},
            headers={
                "X-Custom-Header": "test-value",
                "Content-Type": "application/json",
            },
        )

        # Poll until event with headers is stored
        def find_event():
            response = e2e_client.get(
                f"/api/events/sources/{source_id}/events",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                events = response.json()["items"]
                matching = [
                    e for e in events
                    if e.get("data", {}).get("header_test") is True
                ]
                if matching:
                    return matching
            return None

        matching = poll_until(find_event, max_wait=5.0)
        assert matching is not None, "Event not stored within timeout"
        assert len(matching) >= 1
        # Headers should be stored (lowercase)
        assert "x-custom-header" in matching[0].get("headers", {})

    def test_health_endpoint(self, e2e_client):
        """/api/hooks/health returns 200 OK."""
        response = e2e_client.get("/api/hooks/health")

        assert response.status_code == 200
        assert response.text == "OK"


# =============================================================================
# TestEventDelivery - Event to Workflow Execution
# =============================================================================


@pytest.mark.e2e
class TestEventDelivery:
    """Test event delivery to workflows."""

    def test_event_creates_delivery_for_subscription(
        self, e2e_client, platform_admin, event_source, subscription
    ):
        """Event with matching subscription creates EventDelivery."""
        source_id = event_source["id"]

        # Send webhook
        e2e_client.post(
            f"/api/hooks/{source_id}",
            json={"delivery_test": True},
        )

        # Poll until event is created and has deliveries
        def find_event_with_deliveries():
            response = e2e_client.get(
                f"/api/events/sources/{source_id}/events",
                headers=platform_admin.headers,
            )
            if response.status_code != 200:
                return None
            events = response.json()["items"]
            matching = [
                e for e in events
                if e.get("data", {}).get("delivery_test") is True
            ]
            if not matching:
                return None
            event = matching[0]

            # Check if deliveries exist
            del_response = e2e_client.get(
                f"/api/events/{event['id']}/deliveries",
                headers=platform_admin.headers,
            )
            if del_response.status_code == 200:
                deliveries = del_response.json()["items"]
                if deliveries:
                    return {"event": event, "deliveries": deliveries}
            return None

        result = poll_until(find_event_with_deliveries, max_wait=5.0)
        assert result is not None, "Event with deliveries not found within timeout"

        deliveries = result["deliveries"]
        assert len(deliveries) >= 1
        assert deliveries[0]["workflow_id"] == subscription["workflow_id"]

    def test_event_type_filter_matches(
        self, e2e_client, platform_admin, event_source, test_workflow
    ):
        """Subscription with event_type filter only receives matching events."""
        source_id = event_source["id"]

        # Create subscription with specific event_type filter
        response = e2e_client.post(
            f"/api/events/sources/{source_id}/subscriptions",
            headers=platform_admin.headers,
            json={
                "workflow_id": test_workflow["id"],
                "event_type": "specific.event",
            },
        )
        filtered_sub = response.json()

        # Send non-matching event
        e2e_client.post(
            f"/api/hooks/{source_id}",
            json={"filter_test": "non_matching"},
        )

        # Poll until event is created
        def find_event():
            response = e2e_client.get(
                f"/api/events/sources/{source_id}/events",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                events = response.json()["items"]
                matching = [
                    e for e in events
                    if e.get("data", {}).get("filter_test") == "non_matching"
                ]
                if matching:
                    return matching[0]
            return None

        event = poll_until(find_event, max_wait=5.0)
        assert event is not None, "Event not created within timeout"

        # Should have no deliveries for filtered subscription
        # (since event_type doesn't match "specific.event")
        response = e2e_client.get(
            f"/api/events/{event['id']}/deliveries",
            headers=platform_admin.headers,
        )
        deliveries = response.json()["items"]

        # None of the deliveries should be for filtered_sub
        filtered_deliveries = [
            d for d in deliveries
            if d["event_subscription_id"] == filtered_sub["id"]
        ]
        assert len(filtered_deliveries) == 0

    def test_list_events(self, e2e_client, platform_admin, event_source):
        """List events for an event source."""
        source_id = event_source["id"]

        # Send a few webhooks
        for i in range(3):
            e2e_client.post(
                f"/api/hooks/{source_id}",
                json={"list_test": True, "index": i},
            )

        # Poll until all 3 events are created
        def find_all_events():
            response = e2e_client.get(
                f"/api/events/sources/{source_id}/events",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                data = response.json()
                list_events = [
                    e for e in data["items"]
                    if e.get("data", {}).get("list_test") is True
                ]
                if len(list_events) >= 3:
                    return data
            return None

        data = poll_until(find_all_events, max_wait=5.0)
        assert data is not None, "Not all events created within timeout"

        assert "items" in data
        assert "total" in data

        list_events = [
            e for e in data["items"]
            if e.get("data", {}).get("list_test") is True
        ]
        assert len(list_events) >= 3

    def test_get_event(self, e2e_client, platform_admin, event_source):
        """Get event by ID with delivery counts."""
        source_id = event_source["id"]

        # Send webhook
        e2e_client.post(
            f"/api/hooks/{source_id}",
            json={"get_event_test": True},
        )

        # Poll until event is created
        def find_event():
            response = e2e_client.get(
                f"/api/events/sources/{source_id}/events",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                events = response.json()["items"]
                matching = [
                    e for e in events
                    if e.get("data", {}).get("get_event_test") is True
                ]
                if matching:
                    return matching[0]["id"]
            return None

        event_id = poll_until(find_event, max_wait=5.0)
        assert event_id is not None, "Event not created within timeout"

        # Get single event
        response = e2e_client.get(
            f"/api/events/{event_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        event = response.json()
        assert event["id"] == event_id
        assert "delivery_count" in event
        assert "success_count" in event
        assert "failed_count" in event

    @pytest.mark.usefixtures("subscription")
    def test_list_deliveries(
        self, e2e_client, platform_admin, event_source
    ):
        """List deliveries for an event."""
        source_id = event_source["id"]

        # Send webhook
        e2e_client.post(
            f"/api/hooks/{source_id}",
            json={"deliveries_list_test": True},
        )

        # Poll until event is created with deliveries
        def find_event_with_deliveries():
            response = e2e_client.get(
                f"/api/events/sources/{source_id}/events",
                headers=platform_admin.headers,
            )
            if response.status_code != 200:
                return None
            events = response.json()["items"]
            matching = [
                e for e in events
                if e.get("data", {}).get("deliveries_list_test") is True
            ]
            if not matching:
                return None
            event_id = matching[0]["id"]

            # Check if deliveries exist
            del_response = e2e_client.get(
                f"/api/events/{event_id}/deliveries",
                headers=platform_admin.headers,
            )
            if del_response.status_code == 200:
                data = del_response.json()
                if data.get("items"):
                    return {"event_id": event_id, "data": data}
            return None

        result = poll_until(find_event_with_deliveries, max_wait=5.0)
        assert result is not None, "Event with deliveries not found within timeout"

        data = result["data"]
        assert "items" in data
        assert len(data["items"]) >= 1

        delivery = data["items"][0]
        assert "id" in delivery
        assert "status" in delivery
        assert "workflow_id" in delivery


# =============================================================================
# TestDeliveryRetry - Retry Failed Deliveries
# =============================================================================


@pytest.mark.e2e
class TestDeliveryRetry:
    """Test delivery retry functionality."""

    @pytest.mark.usefixtures("subscription")
    def test_cannot_retry_pending_delivery(
        self, e2e_client, platform_admin, event_source
    ):
        """Cannot retry a delivery that's not failed."""
        source_id = event_source["id"]

        # Send webhook
        e2e_client.post(
            f"/api/hooks/{source_id}",
            json={"retry_test": True},
        )

        # Poll until event is created with deliveries
        def find_event_with_deliveries():
            response = e2e_client.get(
                f"/api/events/sources/{source_id}/events",
                headers=platform_admin.headers,
            )
            if response.status_code != 200:
                return None
            events = response.json()["items"]
            matching = [
                e for e in events
                if e.get("data", {}).get("retry_test") is True
            ]
            if not matching:
                return None
            event = matching[0]

            # Check if deliveries exist
            del_response = e2e_client.get(
                f"/api/events/{event['id']}/deliveries",
                headers=platform_admin.headers,
            )
            if del_response.status_code == 200:
                deliveries = del_response.json()["items"]
                if deliveries:
                    return {"event": event, "deliveries": deliveries}
            return None

        result = poll_until(find_event_with_deliveries, max_wait=15.0)

        assert result is not None, "No deliveries created within timeout - event delivery may be broken"

        deliveries = result["deliveries"]
        delivery = deliveries[0]

        # If delivery already raced to failed/skipped, the retry test is moot
        if delivery["status"] in ["failed", "skipped"]:
            pytest.skip(
                f"Delivery already reached '{delivery['status']}' before retry test could run (race condition)"
            )

        response = e2e_client.post(
            f"/api/events/deliveries/{delivery['id']}/retry",
            headers=platform_admin.headers,
        )
        assert response.status_code == 400, f"Expected 400 for non-failed delivery: {response.text}"


# =============================================================================
# TestScheduleSourceCRUD - Schedule Event Source Management
# =============================================================================


@pytest.fixture
def schedule_source(e2e_client, platform_admin):
    """Create a schedule event source for testing."""
    source_name = f"E2E Schedule {uuid.uuid4().hex[:8]}"

    response = e2e_client.post(
        "/api/events/sources",
        headers=platform_admin.headers,
        json={
            "name": source_name,
            "source_type": "schedule",
            "schedule": {
                "cron_expression": "0 9 * * *",
                "timezone": "UTC",
                "enabled": True,
            },
        },
    )
    assert response.status_code == 201, f"Failed to create schedule source: {response.text}"
    source = response.json()

    yield source

    # Cleanup
    e2e_client.delete(
        f"/api/events/sources/{source['id']}",
        headers=platform_admin.headers,
    )


@pytest.mark.e2e
class TestScheduleSourceCRUD:
    """Test schedule event source CRUD operations."""

    def test_create_schedule_source(self, e2e_client, platform_admin):
        """Create a schedule event source with cron expression."""
        source_name = f"E2E Schedule Create {uuid.uuid4().hex[:8]}"

        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": source_name,
                "source_type": "schedule",
                "schedule": {
                    "cron_expression": "*/5 * * * *",
                    "timezone": "America/New_York",
                    "enabled": True,
                },
            },
        )
        assert response.status_code == 201, f"Failed: {response.text}"
        data = response.json()
        assert data["name"] == source_name
        assert data["source_type"] == "schedule"
        assert data["schedule"] is not None
        assert data["schedule"]["cron_expression"] == "*/5 * * * *"
        assert data["schedule"]["timezone"] == "America/New_York"
        assert data["schedule"]["enabled"] is True

        # Cleanup
        e2e_client.delete(
            f"/api/events/sources/{data['id']}",
            headers=platform_admin.headers,
        )

    def test_get_schedule_source(self, e2e_client, platform_admin, schedule_source):
        """Get a schedule event source with schedule details."""
        response = e2e_client.get(
            f"/api/events/sources/{schedule_source['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["source_type"] == "schedule"
        assert data["schedule"] is not None
        assert data["schedule"]["cron_expression"] == "0 9 * * *"
        assert data["schedule"]["timezone"] == "UTC"
        assert data["schedule"]["enabled"] is True

    def test_update_schedule_source(self, e2e_client, platform_admin, schedule_source):
        """Update a schedule event source's cron expression and timezone."""
        response = e2e_client.patch(
            f"/api/events/sources/{schedule_source['id']}",
            headers=platform_admin.headers,
            json={
                "schedule": {
                    "cron_expression": "0 */6 * * *",
                    "timezone": "Europe/London",
                },
            },
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["schedule"]["cron_expression"] == "0 */6 * * *"
        assert data["schedule"]["timezone"] == "Europe/London"

    def test_disable_schedule_source(self, e2e_client, platform_admin, schedule_source):
        """Disable a schedule source via the schedule config."""
        response = e2e_client.patch(
            f"/api/events/sources/{schedule_source['id']}",
            headers=platform_admin.headers,
            json={
                "schedule": {
                    "cron_expression": schedule_source["schedule"]["cron_expression"],
                    "enabled": False,
                },
            },
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["schedule"]["enabled"] is False

    def test_list_schedule_sources(self, e2e_client, platform_admin, schedule_source):
        """List event sources filtered by schedule type."""
        response = e2e_client.get(
            "/api/events/sources",
            headers=platform_admin.headers,
            params={"source_type": "schedule"},
        )
        assert response.status_code == 200
        data = response.json()
        sources = data["items"]
        assert len(sources) >= 1
        # All returned sources should be schedule type
        assert all(s["source_type"] == "schedule" for s in sources)
        # Our source should be in the list
        source_ids = [s["id"] for s in sources]
        assert schedule_source["id"] in source_ids

    def test_delete_schedule_source(self, e2e_client, platform_admin):
        """Delete a schedule event source."""
        # Create a source to delete
        source_name = f"E2E Schedule Delete {uuid.uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": source_name,
                "source_type": "schedule",
                "schedule": {
                    "cron_expression": "0 12 * * *",
                },
            },
        )
        assert response.status_code == 201
        source_id = response.json()["id"]

        # Delete it
        response = e2e_client.delete(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204

    def test_schedule_source_with_subscription(
        self, e2e_client, platform_admin, schedule_source, test_workflow
    ):
        """Create a subscription with input mapping on a schedule source."""
        response = e2e_client.post(
            f"/api/events/sources/{schedule_source['id']}/subscriptions",
            headers=platform_admin.headers,
            json={
                "workflow_id": test_workflow["id"],
                "input_mapping": {
                    "report_type": "daily",
                    "as_of_date": "{{ scheduled_time }}",
                },
            },
        )
        assert response.status_code == 201, f"Failed: {response.text}"
        sub = response.json()
        assert sub["workflow_id"] == test_workflow["id"]
        assert sub["input_mapping"] is not None
        assert sub["input_mapping"]["report_type"] == "daily"
        assert sub["input_mapping"]["as_of_date"] == "{{ scheduled_time }}"


# =============================================================================
# TestWebhookAuthentication - Webhook HMAC Auth
# =============================================================================


def _hmac_sign(body: bytes, secret: str, prefix: str = "sha256=") -> str:
    """Compute HMAC-SHA256 signature."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{prefix}{sig}"


@pytest.mark.e2e
class TestWebhookAuthentication:
    """Tests for webhook HMAC signature verification."""

    @pytest.fixture
    def secret_source(self, e2e_client, platform_admin):
        """Create a webhook source with a secret configured."""
        source_name = f"E2E HMAC Webhook {uuid.uuid4().hex[:8]}"
        secret = "test-webhook-secret-e2e"

        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": source_name,
                "source_type": "webhook",
                "webhook": {
                    "adapter_name": "generic",
                    "config": {"secret": secret},
                },
            },
        )
        assert response.status_code == 201, f"Failed to create source: {response.text}"
        source = response.json()
        source["_secret"] = secret

        yield source

        e2e_client.delete(
            f"/api/events/sources/{source['id']}",
            headers=platform_admin.headers,
        )

    @pytest.fixture
    def no_secret_source(self, e2e_client, platform_admin):
        """Create a webhook source without a secret."""
        source_name = f"E2E NoAuth Webhook {uuid.uuid4().hex[:8]}"

        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": source_name,
                "source_type": "webhook",
                "webhook": {
                    "adapter_name": "generic",
                    "config": {},
                },
            },
        )
        assert response.status_code == 201, f"Failed to create source: {response.text}"
        source = response.json()

        yield source

        e2e_client.delete(
            f"/api/events/sources/{source['id']}",
            headers=platform_admin.headers,
        )

    def test_webhook_with_valid_hmac_accepted(self, e2e_client, secret_source):
        """Valid HMAC signature → 202 Accepted."""
        callback_url = secret_source["webhook"]["callback_url"]
        # Extract path from callback URL
        from urllib.parse import urlparse

        path = urlparse(callback_url).path

        body = b'{"event": "test", "data": "hello"}'
        secret = secret_source["_secret"]
        signature = _hmac_sign(body, secret)

        response = e2e_client.post(
            path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-256": signature,
            },
        )
        assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"

    def test_webhook_with_invalid_hmac_rejected(self, e2e_client, secret_source):
        """Invalid HMAC signature → 401."""
        callback_url = secret_source["webhook"]["callback_url"]
        from urllib.parse import urlparse

        path = urlparse(callback_url).path

        body = b'{"event": "test"}'

        response = e2e_client.post(
            path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-256": "sha256=invalid_signature_value",
            },
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"

    def test_webhook_without_signature_rejected(self, e2e_client, secret_source):
        """No signature header when secret is set → 401."""
        callback_url = secret_source["webhook"]["callback_url"]
        from urllib.parse import urlparse

        path = urlparse(callback_url).path

        response = e2e_client.post(
            path,
            content=b'{"event": "test"}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"

    def test_webhook_no_secret_accepts_any(self, e2e_client, no_secret_source):
        """No secret configured → 202 regardless of headers."""
        callback_url = no_secret_source["webhook"]["callback_url"]
        from urllib.parse import urlparse

        path = urlparse(callback_url).path

        response = e2e_client.post(
            path,
            content=b'{"event": "test"}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"
