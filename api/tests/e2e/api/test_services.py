"""Slice 2 E2E: services REST control plane + registration wiring.

Registers a real @service file, then exercises definition auto-creation,
policy PATCH, start/stop/restart/enable/disable, attempt listing, and the
event-subscription guard. Slice 3 added the worker claim loop, so a runner
actively claims eligible services here — control-plane tests assert shapes
and transitions, not the absence of attempts.
"""

import pytest

from tests.e2e.conftest import write_and_register


SERVICE_CONTENT = '''"""E2E Services Test Bridge"""
from bifrost import service

@service(name="e2e_service_bridge")
async def e2e_service_bridge() -> None:
    """Bridge test messages into Bifrost events."""
    pass
'''

SERVICE_PATH = "e2e_service_bridge.py"
SERVICE_FUNC = "e2e_service_bridge"


@pytest.fixture(scope="module")
def service_workflow(e2e_client, platform_admin):
    """Register a @service file; definition auto-created with running desire."""
    result = write_and_register(
        e2e_client, platform_admin.headers, SERVICE_PATH, SERVICE_CONTENT, SERVICE_FUNC
    )
    assert result["type"] == "service"
    yield result

    e2e_client.delete(
        f"/api/files/editor?path={SERVICE_PATH}",
        headers=platform_admin.headers,
    )


def _service_id(e2e_client, headers, workflow_id):
    response = e2e_client.get("/api/services", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    matches = [s for s in items if s["workflow_id"] == workflow_id]
    assert matches, "service definition auto-created on registration"
    return matches[0]["id"]


def test_definition_auto_created_running(e2e_client, platform_admin, service_workflow):
    """Registration ensures an enabled, running-desired definition."""
    service_id = _service_id(e2e_client, platform_admin.headers, service_workflow["id"])

    response = e2e_client.get(f"/api/services/{service_id}", headers=platform_admin.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_name"] == "e2e_service_bridge"
    assert body["workflow_path"] == SERVICE_PATH
    assert body["enabled"] is True
    assert body["desired_state"] == "running"
    assert body["restart_policy"] == "always"
    # A Slice 3 runner may already have claimed an attempt for this
    # running-desired service; either pre-claim or live state is valid.
    assert body["observed_state"] in (
        "restarting",
        "starting",
        "running",
        "stopping",
        "stopped",
        "crash_loop",
    )


def test_policy_patch_valid_and_invalid(e2e_client, platform_admin, service_workflow):
    """Policy PATCH accepts valid values and rejects bad ones."""
    service_id = _service_id(e2e_client, platform_admin.headers, service_workflow["id"])

    response = e2e_client.patch(
        f"/api/services/{service_id}",
        json={"restart_policy": "on_failure", "graceful_shutdown_seconds": 10},
        headers=platform_admin.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["restart_policy"] == "on_failure"
    assert body["graceful_shutdown_seconds"] == 10

    response = e2e_client.patch(
        f"/api/services/{service_id}",
        json={"restart_policy": "sometimes"},
        headers=platform_admin.headers,
    )
    assert response.status_code == 422

    # Restore for the lifecycle tests below (module-scoped fixture shares state).
    response = e2e_client.patch(
        f"/api/services/{service_id}",
        json={"restart_policy": "always"},
        headers=platform_admin.headers,
    )
    assert response.status_code == 200


def test_stop_start_restart_cycle(e2e_client, platform_admin, service_workflow):
    """Stop stores desire; start clears it; restart keeps running."""
    service_id = _service_id(e2e_client, platform_admin.headers, service_workflow["id"])

    response = e2e_client.post(f"/api/services/{service_id}/stop", headers=platform_admin.headers)
    assert response.status_code == 200
    assert response.json()["desired_state"] == "stopped"
    assert response.json()["observed_state"] == "stopped"

    response = e2e_client.post(f"/api/services/{service_id}/start", headers=platform_admin.headers)
    assert response.status_code == 200
    assert response.json()["desired_state"] == "running"

    response = e2e_client.post(f"/api/services/{service_id}/restart", headers=platform_admin.headers)
    assert response.status_code == 200
    assert response.json()["desired_state"] == "running"


def test_disable_blocks_start(e2e_client, platform_admin, service_workflow):
    """Disabled services refuse start/restart with 409 until enabled."""
    service_id = _service_id(e2e_client, platform_admin.headers, service_workflow["id"])

    response = e2e_client.post(f"/api/services/{service_id}/disable", headers=platform_admin.headers)
    assert response.status_code == 200
    assert response.json()["enabled"] is False

    response = e2e_client.post(f"/api/services/{service_id}/start", headers=platform_admin.headers)
    assert response.status_code == 409

    response = e2e_client.post(f"/api/services/{service_id}/enable", headers=platform_admin.headers)
    assert response.status_code == 200
    assert response.json()["enabled"] is True

    response = e2e_client.post(f"/api/services/{service_id}/start", headers=platform_admin.headers)
    assert response.status_code == 200


def test_attempts_list_endpoint_shape(e2e_client, platform_admin, service_workflow):
    """Attempt history endpoint returns the paginated shape.

    A Slice 3 runner claims eligible services, so attempts may exist here;
    the endpoint contract (not emptiness) is under test.
    """
    service_id = _service_id(e2e_client, platform_admin.headers, service_workflow["id"])

    response = e2e_client.get(
        f"/api/services/{service_id}/attempts", headers=platform_admin.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total"}
    assert body["total"] == len(body["items"])
    for attempt in body["items"]:
        assert attempt["service_id"] == service_id
        assert attempt["state"] in (
            "starting",
            "running",
            "stopping",
            "stopped",
            "failed",
        )


def test_list_embeds_attempt_summary_exit_and_memory(
    e2e_client, platform_admin, service_workflow
):
    """List + detail embed the 2.2 live fields (no attempts fan-out needed).

    active_attempt mirrors the live row (or null), last_exit_reason is a
    string-or-null, and memory_mb is a float-or-null (null here: the e2e
    runner may not have published this stack's pool hash yet, but the
    contract shape holds either way).
    """
    service_id = _service_id(e2e_client, platform_admin.headers, service_workflow["id"])

    for path in ("/api/services", f"/api/services/{service_id}"):
        response = e2e_client.get(path, headers=platform_admin.headers)
        assert response.status_code == 200
        body = (
            response.json()
            if path.endswith(service_id)
            else next(
                s
                for s in response.json()["items"]
                if s["id"] == service_id
            )
        )
        assert "active_attempt" in body
        assert "last_exit_reason" in body
        assert "memory_mb" in body
        live = body["active_attempt"]
        assert body["active_attempt_id"] == (live["id"] if live else None)
        if live is not None:
            assert live["service_id"] == service_id
            assert live["state"] in ("starting", "running", "stopping")
        assert body["last_exit_reason"] is None or isinstance(
            body["last_exit_reason"], str
        )
        assert body["memory_mb"] is None or isinstance(
            body["memory_mb"], (int, float)
        )


def test_unknown_service_404s(e2e_client, platform_admin):
    """Unknown IDs 404 across the control surface."""
    import uuid

    missing = str(uuid.uuid4())
    for method, path in [
        ("get", f"/api/services/{missing}"),
        ("post", f"/api/services/{missing}/start"),
        ("post", f"/api/services/{missing}/stop"),
        ("get", f"/api/services/{missing}/attempts"),
    ]:
        response = getattr(e2e_client, method)(path, headers=platform_admin.headers)
        assert response.status_code == 404, (method, path)


def test_logs_reject_malformed_continuation_token(
    e2e_client, platform_admin, service_workflow
):
    """Garbage continuation tokens 422 instead of restarting at page one.

    Matches the executions logs endpoint: a token that decodes to nothing
    is corruption, and silently serving page one would duplicate lines
    into the reader.
    """
    service_id = _service_id(e2e_client, platform_admin.headers, service_workflow["id"])

    response = e2e_client.get(
        f"/api/services/{service_id}/logs?continuation_token=not-a-cursor",
        headers=platform_admin.headers,
    )
    assert response.status_code == 422
    assert "continuation_token" in response.json()["detail"]


def test_subscription_rejects_service_target(e2e_client, platform_admin, service_workflow):
    """Event subscriptions cannot target service workflows."""
    import uuid as uuid_module

    response = e2e_client.post(
        "/api/events/sources",
        json={
            "name": f"e2e service guard {uuid_module.uuid4().hex[:8]}",
            "source_type": "topic",
            "event_type": "e2e.service.guard",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201
    source_id = response.json()["id"]

    try:
        response = e2e_client.post(
            f"/api/events/sources/{source_id}/subscriptions",
            json={"target_type": "workflow", "workflow_id": service_workflow["id"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 400
        assert "service" in response.json()["detail"]
    finally:
        e2e_client.delete(
            f"/api/events/sources/{source_id}", headers=platform_admin.headers
        )
