"""E2E: ``events.emit`` through the real worker pool.

Exercises the full path — workflow code in a forked worker child calling
the fixed ``bifrost.events.emit`` — proving parity with the external
HTTP API:

- the workflow records that the engine injected the worker's private socket,
  so the migrated call rode the shared client transport rather than the
  network API (the zero-HTTP proof lives in the unit and forked-child socket
  tests, where the child's network API is dead);
- the emit round-trips inside the workflow through the real ``/api/events/emit``
  route served on that socket (event id + subscriber count);
- the committed event row is verified over external HTTP.
"""

import uuid

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def live_events_keys():
    tag = _uid()
    return {"tag": tag, "topic": f"e2e.sdk.local.{tag}"}


@pytest.fixture(scope="module")
def live_events_source(e2e_client, platform_admin, live_events_keys):
    """Pre-create the topic source so the emit materializes an event row."""
    resp = e2e_client.post(
        "/api/events/sources",
        headers=platform_admin.headers,
        json={
            "name": f"E2E events local {live_events_keys['tag']}",
            "source_type": "topic",
            "event_type": live_events_keys["topic"],
        },
    )
    assert resp.status_code == 201, resp.text
    source = resp.json()
    yield source
    e2e_client.delete(
        f"/api/events/sources/{source['id']}",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def live_events_workflow(
    e2e_client, platform_admin, org1, live_events_keys, live_events_source
):
    """Workflow exercising events.emit through the live worker.

    The workflow records that the engine injected the worker's private
    socket, so the migrated call rode the shared client transport (the
    zero-HTTP proof lives in the unit and forked-child socket tests, where
    the child's network API is dead). The test then verifies the committed
    event row over external HTTP.
    """
    name = f"e2e_sdk_local_events_{live_events_keys['tag']}"
    path = f"{name}.py"
    topic = live_events_keys["topic"]
    content = f'''"""Local events.emit E2E workflow."""
from bifrost import events, workflow
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="Local events.emit E2E")
async def {name}():
    # The engine injected its private socket; the zero-HTTP proof lives in
    # the unit and forked-child socket tests where the child's network API
    # is dead by environment.
    used_socket = get_engine_socket_path() is not None
    result = await events.emit("{topic}", {{"ping": "local"}})
    return {{
        "used_socket": used_socket,
        "event_id": result["event_id"],
        "subscribers_notified": result["subscribers_notified"],
    }}
'''
    registered = write_and_register(
        e2e_client,
        platform_admin.headers,
        path,
        content,
        name,
        organization_id=org1["id"],
    )
    resp = e2e_client.patch(
        f"/api/workflows/{registered['id']}",
        headers=platform_admin.headers,
        json={"organization_id": org1["id"], "access_level": "authenticated"},
    )
    assert resp.status_code == 200, f"workflow patch failed: {resp.text}"
    yield registered

    e2e_client.delete(
        f"/api/files/editor?path={path}",
        headers=platform_admin.headers,
    )


class TestSdkEventsLocalLiveE2E:
    def test_workflow_emit_commits_and_matches_http(
        self,
        e2e_client,
        org1_user,
        platform_admin,
        live_events_workflow,
        live_events_keys,
    ):
        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            live_events_workflow["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        out = result["result"]
        # The worker injected its private socket, so the migrated call rode
        # the shared client transport (zero-HTTP proof is in the unit and
        # forked-child tests).
        assert out["used_socket"] is True
        assert out["subscribers_notified"] == 0

        event_id = out["event_id"]
        # Committed state verified over external HTTP (parity).
        fetched = e2e_client.get(
            f"/api/events/{event_id}", headers=platform_admin.headers
        )
        assert fetched.status_code == 200, fetched.text
        body = fetched.json()
        assert body["event_type"] == live_events_keys["topic"]
        assert body["data"] == {"ping": "local"}
