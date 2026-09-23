"""E2E: supervised services against the deployed stack worker (Slice 3).

The test-stack worker runs the worker-pull claim loop, so these tests drive
desired state through the API and assert durable outcomes — no local pool,
no PIDs:

* claim → fork → run → ready() → streaming logs → stop → no restart
* rolling restart → new attempt runs
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

from bifrost import service


@service
async def {function_name}() -> None:
    """Ready, log one line, then wait for stop."""
    await service.ready()
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

_EMITTING_SOURCE = '''"""E2E fixture service that emits one event."""

from bifrost import events
from bifrost import service


@service
async def {function_name}() -> None:
    """Report ready, bridge one event, then wait for stop."""
    await service.ready()
    await events.emit("{topic}", {{"bridge": "up"}})
    await service.wait_until_stopping()
'''


def _register(e2e_client, headers, suffix: str, source: str) -> dict:
    from tests.e2e.conftest import write_and_register

    function_name = f"e2e_svc_{suffix}"
    path = f"workflows/e2e_svc_{suffix}.py"
    registered = write_and_register(
        e2e_client, headers, path, source.format(function_name=function_name),
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


@pytest.fixture
def service_def(e2e_client, platform_admin):
    """Register a healthy fixture service; park it afterwards."""
    suffix = uuid4().hex[:8]
    registered = _register(e2e_client, platform_admin.headers, suffix, _SERVICE_SOURCE)
    definition = _definition_for(e2e_client, platform_admin.headers, registered["id"])
    yield definition
    e2e_client.post(f"/api/services/{definition['id']}/stop", headers=platform_admin.headers)
    e2e_client.post(f"/api/services/{definition['id']}/disable", headers=platform_admin.headers)


@pytest.fixture
def crashing_def(e2e_client, platform_admin):
    """Register a self-crashing fixture service; park it afterwards."""
    suffix = uuid4().hex[:8]
    registered = _register(e2e_client, platform_admin.headers, suffix, _CRASHING_SOURCE)
    definition = _definition_for(e2e_client, platform_admin.headers, registered["id"])
    yield definition
    e2e_client.post(f"/api/services/{definition['id']}/stop", headers=platform_admin.headers)
    e2e_client.post(f"/api/services/{definition['id']}/disable", headers=platform_admin.headers)


@pytest.fixture
def emitting_def(e2e_client, platform_admin):
    """Register a service that bridges one event; park it afterwards.

    Pre-creates the topic source so the emit materializes an event row
    (emit without a source is a no-op by design).
    """
    from tests.e2e.conftest import write_and_register

    suffix = uuid4().hex[:8]
    function_name = f"e2e_svc_{suffix}"
    path = f"workflows/e2e_svc_{suffix}.py"
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
    registered = write_and_register(
        e2e_client,
        platform_admin.headers,
        path,
        _EMITTING_SOURCE.format(function_name=function_name, topic=topic),
        function_name,
    )
    assert registered["type"] == "service", registered
    definition = _definition_for(e2e_client, platform_admin.headers, registered["id"])
    definition["emit_topic"] = topic
    yield definition
    e2e_client.post(f"/api/services/{definition['id']}/stop", headers=platform_admin.headers)
    e2e_client.post(f"/api/services/{definition['id']}/disable", headers=platform_admin.headers)
    e2e_client.delete(f"/api/events/sources/{source_id}", headers=platform_admin.headers)


class TestServiceWorkerMode:
    async def test_claim_runs_ready_and_streams_logs(
        self, e2e_client, platform_admin, service_def
    ):
        definition_id = service_def["id"]
        observed = await _wait_for(e2e_client, platform_admin.headers, definition_id, "running")
        assert observed["active_attempt_id"]
        assert observed["restart_count"] >= 1

        deadline = time.monotonic() + 60.0
        live = None
        while time.monotonic() < deadline:
            live = _live_attempt(e2e_client, platform_admin.headers, definition_id)
            if live and live.get("ready_at"):
                break
            await asyncio.sleep(1.0)
        assert live and live.get("ready_at"), "attempt never reported ready"
        assert live.get("worker_id"), "attempt has no owning worker"

        from src.core.cache import get_redis
        from src.core.cache.keys import service_logs_stream_key

        async with get_redis() as r:
            entries = await r.xrange(service_logs_stream_key(live["id"]), min="-", max="+")
        messages = [e[1].get("message", "") for e in entries]
        assert any("e2e service live" in m for m in messages), messages
        assert any("service starting" in m for m in messages), messages

    async def test_logs_endpoint_serves_flushed_stream(
        self, e2e_client, platform_admin, service_def
    ):
        """Beat flush persists stream lines; the logs endpoint reads them.

        Polls until the claim-loop beat drains the live stream into
        Postgres, then asserts the read contract (shape, filters) and that
        rows survive the stop transition (final drain on completion).
        """
        definition_id = service_def["id"]
        await _wait_for(e2e_client, platform_admin.headers, definition_id, "running")

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

    async def test_stop_completes_without_restart(
        self, e2e_client, platform_admin, service_def
    ):
        definition_id = service_def["id"]
        await _wait_for(e2e_client, platform_admin.headers, definition_id, "running")
        before = e2e_client.get(
            f"/api/services/{definition_id}/attempts", headers=platform_admin.headers
        ).json()["total"]

        resp = e2e_client.post(f"/api/services/{definition_id}/stop", headers=platform_admin.headers)
        assert resp.status_code == 200, resp.text
        await _wait_for(e2e_client, platform_admin.headers, definition_id, "stopped")

        await asyncio.sleep(6.0)
        observed = e2e_client.get(
            f"/api/services/{definition_id}", headers=platform_admin.headers
        ).json()
        assert observed["observed_state"] == "stopped"
        after = e2e_client.get(
            f"/api/services/{definition_id}/attempts", headers=platform_admin.headers
        ).json()["total"]
        assert after == before

    async def test_rolling_restart_starts_new_attempt(
        self, e2e_client, platform_admin, service_def
    ):
        definition_id = service_def["id"]
        await _wait_for(e2e_client, platform_admin.headers, definition_id, "running")
        first = _live_attempt(e2e_client, platform_admin.headers, definition_id)
        assert first is not None

        resp = e2e_client.post(
            f"/api/services/{definition_id}/restart", headers=platform_admin.headers
        )
        assert resp.status_code == 200, resp.text

        deadline = time.monotonic() + 90.0
        second = None
        while time.monotonic() < deadline:
            candidate = _live_attempt(e2e_client, platform_admin.headers, definition_id)
            if candidate and candidate["id"] != first["id"]:
                second = candidate
                break
            await asyncio.sleep(1.0)
        assert second is not None, "restart did not start a new attempt"

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
        assert len(failed) >= 5, f"expected crash-loop failures, got {len(failed)}"
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

    async def test_service_token_is_scoped_not_superuser(        self, e2e_client, platform_admin, service_def
    ):
        definition_id = service_def["id"]
        await _wait_for(e2e_client, platform_admin.headers, definition_id, "running")
        live = _live_attempt(e2e_client, platform_admin.headers, definition_id)
        assert live is not None

        from src.core.cache import get_redis
        from src.core.cache.keys import service_token_key

        async with get_redis() as r:
            raw = await r.get(service_token_key(live["id"]))
        assert raw, "no rotation token handed to the child"
        token = json.loads(raw)["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = e2e_client.get("/api/services", headers=headers)
        assert resp.status_code == 403, resp.text

        # A service token cannot emit outside its own org (or globally).
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

    async def test_service_emits_into_own_org(
        self, e2e_client, platform_admin, emitting_def
    ):
        """The producer path: a service token can emit org-scoped events."""
        definition_id = emitting_def["id"]
        topic = emitting_def["emit_topic"]
        await _wait_for(e2e_client, platform_admin.headers, definition_id, "running")

        deadline = time.monotonic() + 60.0
        found = None
        while time.monotonic() < deadline:
            sources = e2e_client.get(
                "/api/events/sources", headers=platform_admin.headers
            ).json()["items"]
            source = next(
                (s for s in sources if s.get("event_type") == topic),
                None,
            )
            if source is not None:
                items = e2e_client.get(
                    f"/api/events/sources/{source['id']}/events",
                    headers=platform_admin.headers,
                ).json()["items"]
                found = next(
                    (e for e in items if e.get("data") == {"bridge": "up"}),
                    None,
                )
                if found is not None:
                    break
            await asyncio.sleep(2.0)
        assert found is not None, f"service never emitted {topic}"
