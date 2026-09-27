"""E2E: process-control mechanics for services (Slice 3).

These tests fork REAL service children from a test-local pool. Claim races
with the deployed stack worker are decided deterministically: the
``slot_fillers`` fixture first occupies every stack-worker service slot with
healthy dummy services, so the fixture target stays eligible-but-unclaimed
until the test-local loop (empty slots) claims it.

Covered here (all require process control the API suite cannot express):

* child SIGKILL → crash detected → new attempt restarts (failed accounting)
* owner death (loop + pool stopped) → lease expiry → second worker takes over

The API-observable behavior (claim/ready/logs/stop/restart/
crash-loop/token scopes) runs in test_service_worker_mode.py.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from uuid import uuid4

import pytest

pytestmark = pytest.mark.e2e

# Stack-worker service slots to occupy. Must match the worker's
# max_service_workers setting (default 20; the test compose sets no
# override). If it drifts, the target claim below fails loudly.
STACK_SERVICE_SLOTS = 20

_FILLER_SOURCE = '''"""Slot filler: healthy service that idles forever."""

from bifrost import service


@service
async def {function_name}() -> None:
    """Hold one stack-worker service slot."""
    await service.ready()
    await service.wait_until_stopping()
'''

_SERVICE_SOURCE = '''"""E2E fixture service (quiesced)."""

import logging

from bifrost import service


@service
async def {function_name}() -> None:
    """Ready, log one line, then wait for stop."""
    await service.ready()
    logging.getLogger(__name__).info("e2e service live")
    await service.wait_until_stopping()
'''


def _register(e2e_client, headers, suffix: str) -> dict:
    from tests.e2e.conftest import write_and_register

    function_name = f"e2e_qsvc_{suffix}"
    path = f"workflows/e2e_qsvc_{suffix}.py"
    registered = write_and_register(
        e2e_client,
        headers,
        path,
        _SERVICE_SOURCE.format(function_name=function_name),
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
    suffix = uuid4().hex[:8]
    registered = _register(e2e_client, platform_admin.headers, suffix)
    definition = _definition_for(e2e_client, platform_admin.headers, registered["id"])
    yield definition
    e2e_client.post(f"/api/services/{definition['id']}/stop", headers=platform_admin.headers)
    e2e_client.post(f"/api/services/{definition['id']}/disable", headers=platform_admin.headers)


def _make_loop(pool, **overrides):
    from src.services.service_claim import ServiceClaimLoop

    args = {
        "worker_id": f"e2e-worker-{uuid4().hex[:6]}",
        "pool": pool,
        "claim_interval_seconds": 1.0,
        "beat_interval_seconds": 2.0,
        "lease_ttl_seconds": 10,
        "token_lifetime_seconds": 120,
    }
    args.update(overrides)
    return ServiceClaimLoop(**args)


@pytest.fixture(scope="module")
def slot_fillers(e2e_client, platform_admin):
    """Occupy every stack-worker service slot with healthy dummies.

    Registers STACK_SERVICE_SLOTS idle services and waits until each has a
    live attempt (i.e. the stack worker owns them all). The mechanical-test
    target registered afterwards stays eligible-but-unclaimed until the
    test-local loop claims it. Disabled afterwards to release the slots.

    Sync (module-scoped): the HTTP client is sync and no local loop runs
    during setup, so time.sleep blocks nothing.
    """
    from tests.e2e.conftest import write_and_register

    headers = platform_admin.headers
    filler_ids = []
    for i in range(STACK_SERVICE_SLOTS):
        suffix = f"fill{i}{uuid4().hex[:6]}"
        function_name = f"e2e_qfill_{suffix}"
        registered = write_and_register(
            e2e_client,
            headers,
            f"workflows/e2e_qfill_{suffix}.py",
            _FILLER_SOURCE.format(function_name=function_name),
            function_name,
        )
        assert registered["type"] == "service", registered
        resp = e2e_client.get("/api/services", headers=headers)
        definition = next(
            item
            for item in resp.json()["items"]
            if item["workflow_id"] == registered["id"]
        )
        filler_ids.append(definition["id"])

    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        states = [
            e2e_client.get(f"/api/services/{fid}", headers=headers).json()[
                "observed_state"
            ]
            for fid in filler_ids
        ]
        if all(s == "running" for s in states):
            break
        time.sleep(2.0)
    else:
        raise AssertionError(
            f"stack slots never filled (STACK_SERVICE_SLOTS={STACK_SERVICE_SLOTS}): {states}"
        )
    yield filler_ids
    for fid in filler_ids:
        e2e_client.post(f"/api/services/{fid}/stop", headers=headers)
        e2e_client.post(f"/api/services/{fid}/disable", headers=headers)


@pytest.fixture
async def running_stack(slot_fillers):
    from src.services.execution.process_pool import ProcessPoolManager

    pool = ProcessPoolManager(
        max_workers=2, max_service_workers=2, graceful_shutdown_seconds=2
    )
    await pool.start()
    loop = _make_loop(pool)
    pool.on_service_result = loop.handle_service_result
    await loop.start()
    yield pool, loop
    await loop.stop()
    await pool.stop()


class TestServiceOwnerLoss:
    async def test_child_sigkill_restarts_with_new_attempt(
        self, e2e_client, platform_admin, service_def, running_stack
    ):
        pool, _ = running_stack
        definition_id = service_def["id"]
        await _wait_for(e2e_client, platform_admin.headers, definition_id, "running")

        deadline = time.monotonic() + 60.0
        first = None
        handle = None
        while time.monotonic() < deadline:
            first = _live_attempt(e2e_client, platform_admin.headers, definition_id)
            if first is not None:
                handle = next(
                    (
                        h
                        for h in pool.service_processes.values()
                        if h.service and h.service.attempt_id == first["id"]
                    ),
                    None,
                )
                if handle is not None and handle.pid:
                    try:
                        os.kill(handle.pid, 0)
                    except OSError:
                        handle = None
                    else:
                        break
            await asyncio.sleep(0.5)
        assert first is not None and handle is not None and handle.pid

        os.kill(handle.pid, signal.SIGKILL)

        deadline = time.monotonic() + 90.0
        second = None
        while time.monotonic() < deadline:
            candidate = _live_attempt(e2e_client, platform_admin.headers, definition_id)
            if candidate and candidate["id"] != first["id"]:
                second = candidate
                break
            await asyncio.sleep(1.0)
        assert second is not None, "crashed service was not restarted"

        resp = e2e_client.get(
            f"/api/services/{definition_id}/attempts", headers=platform_admin.headers
        )
        states = {a["id"]: a for a in resp.json()["items"]}
        assert states[first["id"]]["state"] == "failed"

    async def test_owner_death_takeover_after_lease_expiry(
        self, e2e_client, platform_admin, service_def, running_stack
    ):
        from src.services.execution.process_pool import ProcessPoolManager

        pool, first_loop = running_stack
        definition_id = service_def["id"]
        await _wait_for(e2e_client, platform_admin.headers, definition_id, "running")
        first = _live_attempt(e2e_client, platform_admin.headers, definition_id)
        assert first is not None
        handle = next(
            (
                h
                for h in pool.service_processes.values()
                if h.service and h.service.attempt_id == first["id"]
            ),
            None,
        )
        assert handle is not None and handle.pid

        # Owner death WITHOUT handover: the tick task dies (no more beats)
        # and the pool dies with it (no crash report, no terminal result).
        # The DB lease is the only backstop: it expires and the second
        # worker takes the service over.
        first_loop._task.cancel()
        try:
            await first_loop._task
        except asyncio.CancelledError:
            # Expected: killed without handover (owner-death path).
            pass
        await pool.stop()

        second_pool = ProcessPoolManager(
            max_workers=2, max_service_workers=2, graceful_shutdown_seconds=2
        )
        await second_pool.start()
        second_loop = _make_loop(second_pool)
        second_pool.on_service_result = second_loop.handle_service_result
        try:
            await second_loop.start()
            deadline = time.monotonic() + 90.0
            second = None
            while time.monotonic() < deadline:
                candidate = _live_attempt(
                    e2e_client, platform_admin.headers, definition_id
                )
                if candidate and candidate["id"] != first["id"]:
                    second = candidate
                    break
                await asyncio.sleep(1.0)
            assert second is not None, "lost worker's service was not taken over"

            resp = e2e_client.get(
                f"/api/services/{definition_id}/attempts", headers=platform_admin.headers
            )
            states = {a["id"]: a for a in resp.json()["items"]}
            assert states[first["id"]]["state"] == "failed"
            assert states[first["id"]]["exit_reason"] == "lease_expired"
        finally:
            await second_loop.stop()
            await second_pool.stop()
