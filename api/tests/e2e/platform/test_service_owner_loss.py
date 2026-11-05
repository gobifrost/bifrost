"""E2E: process-control mechanics for services (Slice 3).

These tests fork REAL service children from a test-local pool. Claim races
with the deployed stack executors are decided deterministically by memory:
the fixture target requests more memory than any stack executor's budget
holds, and only the test-local loop (an unlimited budget) can claim it.

Covered here (all require process control the API suite cannot express):

* child SIGKILL → crash detected → new attempt restarts (failed accounting)
* owner death (loop + pool stopped) → lease expiry → second worker takes over
* a burst of services on the stack executors receives a concurrent stop
  burst and every one reaches a terminal state

The claim-loop unit regression covers the complementary transaction boundary:
a child completion callback can finish while its stop is awaited after the
claim tick has released its database session.

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

# Larger than any stack executor's budget: only the test-local loop, whose
# budget is unlimited, can claim the fixture target. The loop is registered
# as an executor that large, so the target is not work no executor can hold
# (which the control plane blocks as too_large).
TARGET_REQUEST_BYTES = 2**40
STOP_BURST_SERVICES = 5

_FILLER_SOURCE = '''"""Slot filler: healthy service that idles forever."""

from bifrost import service


@service
async def {function_name}() -> None:
    """Idle on a stack executor until stopped."""
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
async def test_loop_executor(async_session_factory):
    """The test-local loop as a live executor whose budget holds the target."""
    from sqlalchemy import delete, func, update

    from src.models.orm.execution_workers import ExecutionWorker

    worker_id = uuid4()
    async with async_session_factory() as db:
        db.add(ExecutionWorker(
            id=worker_id, form="static", state="ready", resource_class="standard", slots=1,
            hostname="e2e-test-loop", memory_budget_bytes=2 * TARGET_REQUEST_BYTES,
            heartbeat_at=func.now(),
        ))
        await db.commit()

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(5)
            async with async_session_factory() as db:
                await db.execute(
                    update(ExecutionWorker)
                    .where(ExecutionWorker.id == worker_id)
                    .values(heartbeat_at=func.now())
                )
                await db.commit()

    beating = asyncio.create_task(heartbeat())
    yield worker_id
    beating.cancel()
    async with async_session_factory() as db:
        await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
        await db.commit()


@pytest.fixture
async def service_def(e2e_client, platform_admin, db_session, test_loop_executor):
    import sqlalchemy as sa

    headers = platform_admin.headers
    suffix = uuid4().hex[:8]
    registered = _register(e2e_client, headers, suffix)
    definition = _definition_for(e2e_client, headers, registered["id"])
    await db_session.execute(
        sa.text("UPDATE workflows SET learned_memory_bytes = :size WHERE id = CAST(:id AS uuid)"),
        {"size": TARGET_REQUEST_BYTES, "id": registered["id"]},
    )
    await db_session.commit()
    # A stack executor may have claimed it before its request was raised.
    assert e2e_client.post(f"/api/services/{definition['id']}/stop", headers=headers).status_code == 200
    await _wait_for(e2e_client, headers, definition["id"], "stopped")
    assert e2e_client.post(f"/api/services/{definition['id']}/start", headers=headers).status_code == 200
    yield definition
    e2e_client.post(f"/api/services/{definition['id']}/stop", headers=platform_admin.headers)
    e2e_client.post(f"/api/services/{definition['id']}/disable", headers=platform_admin.headers)


def _offering(free_bytes: int):  # type: ignore[no-untyped-def]
    """An executor gate that always offers ``free_bytes``."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def claiming():  # type: ignore[no-untyped-def]
        yield free_bytes

    return claiming


def _make_loop(pool, **overrides):
    from src.services.service_claim import ServiceClaimLoop

    args = {
        "worker_id": f"e2e-worker-{uuid4().hex[:6]}",
        "pool": pool,
        "claim_interval_seconds": 1.0,
        "beat_interval_seconds": 2.0,
        "lease_ttl_seconds": 10,
        "token_lifetime_seconds": 120,
        "claiming": _offering(2 * TARGET_REQUEST_BYTES),
        "hold_memory": lambda attempt_id, request: None,
    }
    args.update(overrides)
    return ServiceClaimLoop(**args)


@pytest.fixture
async def running_stack():
    from src.services.execution.process_pool import ProcessPoolManager

    pool = ProcessPoolManager(graceful_shutdown_seconds=2)
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

        second_pool = ProcessPoolManager(graceful_shutdown_seconds=2)
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


def test_a_stop_burst_on_stack_executors_reaches_terminal_state(e2e_client, platform_admin):
    """Several services the stack executors run, stopped at once, all end."""
    from tests.e2e.conftest import write_and_register

    headers = platform_admin.headers
    ids = []
    for _ in range(STOP_BURST_SERVICES):
        suffix = f"burst{uuid4().hex[:8]}"
        function_name = f"e2e_qfill_{suffix}"
        registered = write_and_register(
            e2e_client, headers, f"workflows/e2e_qfill_{suffix}.py",
            _FILLER_SOURCE.format(function_name=function_name), function_name,
        )
        ids.append(_definition_for(e2e_client, headers, registered["id"])["id"])

    def states() -> dict[str, str]:
        return {
            fid: e2e_client.get(f"/api/services/{fid}", headers=headers).json()["observed_state"]
            for fid in ids
        }

    deadline = time.monotonic() + 180.0
    while any(state != "running" for state in states().values()):
        assert time.monotonic() < deadline, states()
        time.sleep(2.0)

    for fid in ids:
        assert e2e_client.post(f"/api/services/{fid}/stop", headers=headers).status_code == 200
    for fid in ids:
        assert e2e_client.post(f"/api/services/{fid}/disable", headers=headers).status_code == 200

    deadline = time.monotonic() + 120.0
    while any(state != "stopped" for state in states().values()):
        assert time.monotonic() < deadline, states()
        time.sleep(0.5)
