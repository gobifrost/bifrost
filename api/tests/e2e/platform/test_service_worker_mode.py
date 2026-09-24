"""E2E: supervised services against the deployed stack worker (Slice 3).

The test-stack worker runs the worker-pull claim loop, so these tests drive
desired state through the API and assert durable outcomes — no local pool,
no PIDs:

* claim → fork → run → ready() → emits → streaming logs → rolling restart
  → stop → no restart
* self-crashing service → repeated failure → crash_loop → manual restart clears
* service token: valid org credential, rejected at platform-admin gates

Services are registered per test (unique suffix) and parked (stopped +
disabled) afterwards so no eligible rows leak into other tests.
"""

from __future__ import annotations

import asyncio
import json
import time
from uuid import uuid4

import pytest

pytestmark = pytest.mark.e2e

_SERVICE_SOURCE = '''"""E2E fixture service."""

import logging

from bifrost import events
from bifrost import service


@service
async def {function_name}() -> None:
    """Ready, emit once, log one line, then wait for stop."""
    await service.ready()
    await events.emit("{topic}", {{"bridge": "up"}})
    logging.getLogger(__name__).info("e2e service live")
    await service.wait_until_stopping()
'''

_CRASHING_SOURCE = '''"""E2E fixture service that always fails."""

from bifrost import service


@service
async def {function_name}() -> None:
    """Report ready, then fail fast."""
    await service.ready()
    raise RuntimeError("e2e boom")
'''

def _register(e2e_client, headers, suffix: str, source: str, **source_values: str) -> dict:
    from tests.e2e.conftest import write_and_register

    function_name = f"e2e_svc_{suffix}"
    path = f"workflows/e2e_svc_{suffix}.py"
    registered = write_and_register(
        e2e_client,
        headers,
        path,
        source.format(function_name=function_name, **source_values),
        function_name,
    )
    assert registered["type"] == "service", registered
    return registered


def _definition_for(e2e_client, headers, workflow_id: str) -> dict:
    resp = e2e_client.get("/api/services", headers=headers)
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        if item["workflow_id"] == workflow_id:
            return item
    raise AssertionError(f"no service definition for workflow {workflow_id}")


async def _wait_for(e2e_client, headers, definition_id: str, state: str, timeout=120.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        resp = e2e_client.get(f"/api/services/{definition_id}", headers=headers)
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["observed_state"] == state:
            return last
        await asyncio.sleep(1.0)
    raise AssertionError(f"service {definition_id} never reached {state}: {last}")


def _live_attempt(e2e_client, headers, definition_id: str) -> dict | None:
    resp = e2e_client.get(f"/api/services/{definition_id}/attempts", headers=headers)
    assert resp.status_code == 200, resp.text
    for attempt in resp.json()["items"]:
        if attempt["state"] in ("starting", "running", "stopping"):
            return attempt
    return None


async def _wait_for_live_attempt(
    e2e_client,
    headers,
    definition_id: str,
    *,
    different_from: str | None = None,
    ready: bool = False,
    timeout: float = 120.0,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        candidate = _live_attempt(e2e_client, headers, definition_id)
        if candidate is not None:
            last = candidate
            if (
                candidate["id"] != different_from
                and (not ready or candidate.get("ready_at"))
            ):
                return candidate
        await asyncio.sleep(1.0)
    raise AssertionError(f"service {definition_id} never had a matching live attempt: {last}")


@pytest.fixture
def service_def(e2e_client, platform_admin):
    """Register a healthy service and its topic source; park both afterwards."""
    suffix = uuid4().hex[:8]
    topic = f"e2e.svc.{suffix}"
    source = e2e_client.post(
        "/api/events/sources",
        headers=platform_admin.headers,
        json={
            "name": f"E2E service emit {suffix}",
            "source_type": "topic",
            "event_type": topic,
        },
    )
    assert source.status_code == 201, source.text
    source_id = source.json()["id"]
    registered = _register(
        e2e_client,
        platform_admin.headers,
        suffix,
        _SERVICE_SOURCE,
        topic=topic,
    )
    definition = _definition_for(e2e_client, platform_admin.headers, registered["id"])
    definition["emit_topic"] = topic
    yield definition
    e2e_client.post(f"/api/services/{definition['id']}/stop", headers=platform_admin.headers)
    e2e_client.post(f"/api/services/{definition['id']}/disable", headers=platform_admin.headers)
    e2e_client.delete(f"/api/events/sources/{source_id}", headers=platform_admin.headers)


@pytest.fixture
def crashing_def(e2e_client, platform_admin):
    """Register one real crash; unit tests cover repeated-failure accounting."""
    suffix = uuid4().hex[:8]
    registered = _register(e2e_client, platform_admin.headers, suffix, _CRASHING_SOURCE)
    definition = _definition_for(e2e_client, platform_admin.headers, registered["id"])
    policy = e2e_client.patch(
        f"/api/services/{definition['id']}",
        headers=platform_admin.headers,
        json={"crash_loop_max_restarts": 1},
    )
    assert policy.status_code == 200, policy.text
    yield definition
    e2e_client.post(f"/api/services/{definition['id']}/stop", headers=platform_admin.headers)
    e2e_client.post(f"/api/services/{definition['id']}/disable", headers=platform_admin.headers)


class TestServiceWorkerMode:
    async def test_healthy_service_lifecycle(
        self, e2e_client, platform_admin, service_def
    ):
        """One service runs its ready, emit, log, restart, and stop lifecycle."""
        definition_id = service_def["id"]
        observed = await _wait_for(e2e_client, platform_admin.headers, definition_id, "running")
        assert observed["active_attempt_id"]
        assert observed["restart_count"] >= 1

        live = await _wait_for_live_attempt(
            e2e_client, platform_admin.headers, definition_id, ready=True
        )
        assert live.get("ready_at"), "attempt never reported ready"
        assert live.get("worker_id"), "attempt has no owning worker"

        # The producer path: this attempt may emit within its own organization.
        deadline = time.monotonic() + 60.0
        found = None
        while time.monotonic() < deadline:
            sources = e2e_client.get(
                "/api/events/sources", headers=platform_admin.headers
            ).json()["items"]
            source = next(
                (s for s in sources if s.get("event_type") == service_def["emit_topic"]),
                None,
            )
            if source is not None:
                items = e2e_client.get(
                    f"/api/events/sources/{source['id']}/events",
                    headers=platform_admin.headers,
                ).json()["items"]
                found = next(
                    (event for event in items if event.get("data") == {"bridge": "up"}),
                    None,
                )
                if found is not None:
                    break
            await asyncio.sleep(2.0)
        assert found is not None, f"service never emitted {service_def['emit_topic']}"

        from src.core.cache import get_redis
        from src.core.cache.keys import service_logs_stream_key

        async with get_redis() as r:
            entries = await r.xrange(service_logs_stream_key(live["id"]), min="-", max="+")
        messages = [e[1].get("message", "") for e in entries]
        assert any("e2e service live" in m for m in messages), messages
        assert any("service starting" in m for m in messages), messages

        # A worker beat must flush the live Redis stream into Postgres.
        deadline = time.monotonic() + 90.0
        logs: dict = {"items": [], "total": 0}
        while time.monotonic() < deadline:
            resp = e2e_client.get(
                f"/api/services/{definition_id}/logs",
                headers=platform_admin.headers,
                params={"limit": 200},
            )
            assert resp.status_code == 200, resp.text
            logs = resp.json()
            if any("e2e service live" in item["message"] for item in logs["items"]):
                break
            await asyncio.sleep(2.0)
        messages = [item["message"] for item in logs["items"]]
        assert any("e2e service live" in m for m in messages), messages
        assert logs["total"] >= len(logs["items"]) >= 1
        live = _live_attempt(e2e_client, platform_admin.headers, definition_id)
        assert live is not None
        first = logs["items"][0]
        assert set(first) >= {
            "id", "service_id", "attempt_id", "level", "message", "timestamp",
        }
        assert first["service_id"] == definition_id

        # Filters: attempt scope narrows, level allowlist narrows, and an
        # unknown level matches nothing (not a 422).
        resp = e2e_client.get(
            f"/api/services/{definition_id}/logs",
            headers=platform_admin.headers,
            params={"attempt_id": live["id"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"], "attempt-scoped read is empty"
        assert all(
            item["attempt_id"] == live["id"]
            for item in resp.json()["items"]
        )
        resp = e2e_client.get(
            f"/api/services/{definition_id}/logs",
            headers=platform_admin.headers,
            params={"levels": "INFO"},
        )
        assert resp.status_code == 200, resp.text
        assert all(
            item["level"] == "INFO" for item in resp.json()["items"]
        )
        resp = e2e_client.get(
            f"/api/services/{definition_id}/logs",
            headers=platform_admin.headers,
            params={"levels": "NOPE"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []

        # The token for the actual live attempt has org-scoped access only.
        from src.core.cache.keys import service_token_key

        live = await _wait_for_live_attempt(
            e2e_client, platform_admin.headers, definition_id, ready=True
        )
        async with get_redis() as r:
            raw = await r.get(service_token_key(live["id"]))
        assert raw, "no rotation token handed to the child"
        token = json.loads(raw)["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = e2e_client.get("/api/services", headers=headers)
        assert resp.status_code == 403, resp.text

        from uuid import uuid4 as _uuid4

        resp = e2e_client.post(
            "/api/events/emit",
            headers=headers,
            json={
                "topic": "e2e.svc.probe",
                "data": {},
                "scope": str(_uuid4()),
            },
        )
        assert resp.status_code == 403, resp.text
        resp = e2e_client.post(
            "/api/events/emit",
            headers=headers,
            json={"topic": "e2e.svc.probe", "data": {}, "scope": "GLOBAL"},
        )
        assert resp.status_code == 403, resp.text

        resp = e2e_client.post(
            "/api/files/read",
            headers=headers,
            json={"location": "workspace", "path": "workflows/nope.py"},
        )
        assert resp.status_code != 401, resp.text

        # A rolling restart must replace the live attempt before the final stop.
        resp = e2e_client.post(
            f"/api/services/{definition_id}/restart", headers=platform_admin.headers
        )
        assert resp.status_code == 200, resp.text
        second = await _wait_for_live_attempt(
            e2e_client,
            platform_admin.headers,
            definition_id,
            different_from=live["id"],
            ready=True,
            timeout=90.0,
        )
        assert second["id"] != live["id"]

        before_attempts = e2e_client.get(
            f"/api/services/{definition_id}/attempts", headers=platform_admin.headers
        ).json()["total"]

        # Rows survive the stop transition (final drain on completion).
        resp = e2e_client.post(
            f"/api/services/{definition_id}/stop",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 200, resp.text
        await _wait_for(e2e_client, platform_admin.headers, definition_id, "stopped")
        resp = e2e_client.get(
            f"/api/services/{definition_id}/logs",
            headers=platform_admin.headers,
            params={"limit": 200},
        )
        assert resp.status_code == 200, resp.text
        assert any(
            "e2e service live" in item["message"]
            for item in resp.json()["items"]
        ), "flushed rows lost across the terminal transition"

        # Newest-first paging + invalid order rejection.
        resp = e2e_client.get(
            f"/api/services/{definition_id}/logs",
            headers=platform_admin.headers,
            params={"limit": 200, "order": "newest_first"},
        )
        assert resp.status_code == 200, resp.text
        timestamps = [item["timestamp"] for item in resp.json()["items"]]
        assert timestamps == sorted(timestamps, reverse=True)
        assert "continuation_token" in resp.json()
        resp = e2e_client.get(
            f"/api/services/{definition_id}/logs",
            headers=platform_admin.headers,
            params={"order": "sideways"},
        )
        assert resp.status_code == 422, resp.text

        # Observe one worker beat after terminal completion before confirming
        # the stopped desire cannot launch a replacement attempt.
        await asyncio.sleep(6.0)
        observed = e2e_client.get(
            f"/api/services/{definition_id}", headers=platform_admin.headers
        ).json()
        assert observed["observed_state"] == "stopped"
        after = e2e_client.get(
            f"/api/services/{definition_id}/attempts", headers=platform_admin.headers
        ).json()["total"]
        assert after == before_attempts

    async def test_crashing_service_enters_crash_loop_then_recovers(
        self, e2e_client, platform_admin, crashing_def
    ):
        definition_id = crashing_def["id"]
        observed = await _wait_for(
            e2e_client, platform_admin.headers, definition_id, "crash_loop", timeout=300.0
        )
        assert observed["blocked_reason"] == "crash_loop"

        attempts = e2e_client.get(
            f"/api/services/{definition_id}/attempts", headers=platform_admin.headers
        ).json()["items"]
        failed = [a for a in attempts if a["state"] == "failed"]
        assert failed, "the child process must report a failed attempt"
        assert any("e2e boom" in (a.get("error") or "") for a in failed)

        # Manual restart clears the loop — but the code still fails, so park
        # it right after proving the clear works.
        resp = e2e_client.post(
            f"/api/services/{definition_id}/restart", headers=platform_admin.headers
        )
        assert resp.status_code == 200, resp.text
        deadline = time.monotonic() + 60.0
        cleared = False
        while time.monotonic() < deadline:
            current = e2e_client.get(
                f"/api/services/{definition_id}", headers=platform_admin.headers
            ).json()
            if current["blocked_reason"] is None:
                cleared = True
                break
            await asyncio.sleep(1.0)
        assert cleared, "restart did not clear the crash loop"
