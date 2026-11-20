"""Service lease clocks are evaluated after acquiring the attempt row lock."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.workflows import Workflow
from src.services import service_lifecycle as lifecycle


@pytest_asyncio.fixture
async def timing_attempt(async_session_factory):
    """Commit only the rows needed for independent lock holders."""
    async with async_session_factory() as db:
        suffix = uuid4().hex
        workflow = Workflow(
            id=uuid4(), name=f"lease_clock_{suffix}",
            function_name=f"lease_clock_{suffix}",
            path=f"workflows/lease_clock_{suffix}.py", type="service",
            organization_id=None, is_active=True,
        )
        db.add(workflow)
        await db.flush()
        definition = await lifecycle.ensure_definition_for_workflow(
            db, workflow, created_by="tester@example.com",
        )
        definition.restart_policy = "never"
        await db.flush()
        # Keep the committed baseline live for the real supervisor sweeper.
        # Only this test process advances the application clock past expiry.
        start = datetime.now(timezone.utc)
        expires = start + timedelta(seconds=60)
        attempt = ServiceAttempt(
            id=uuid4(), service_id=definition.id, lease_token=uuid4().hex,
            lease_expires_at=expires, state="starting", started_at=start,
            heartbeat_at=start,
        )
        db.add(attempt)
        await db.commit()
        try:
            yield attempt.id, attempt.lease_token, start
        finally:
            await db.execute(delete(ServiceDefinition).where(ServiceDefinition.id == definition.id))
            await db.execute(delete(Workflow).where(Workflow.id == workflow.id))
            await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["mark_attempt_ready", "complete_attempt", "heartbeat_attempt"])
@pytest.mark.parametrize("clock_case", [
    "valid", "expired", "boundary", "wait_expired", "wait_valid", "wait_explicit",
])
async def test_service_owner_clock_after_attempt_lock(
    async_session_factory, timing_attempt, monkeypatch, operation, clock_case,
):
    attempt_id, token, start = timing_attempt
    expires = start + timedelta(seconds=60)
    late = expires + timedelta(seconds=1)
    clock = {"expired": late, "boundary": expires}.get(
        clock_case, start + timedelta(seconds=1),
    )
    monkeypatch.setattr(lifecycle, "_now", lambda: clock)
    kwargs = {"attempt_id": attempt_id, "lease_token": token}
    if operation == "complete_attempt":
        kwargs["reason"] = "clean_return"
    if clock_case == "wait_explicit":
        kwargs["now"] = clock
    expected_time = clock
    rejected = clock_case in ("expired", "boundary", "wait_expired")

    async with (
        async_session_factory() as blocker,
        async_session_factory() as owner,
        async_session_factory() as observer,
    ):
        owner_pid = await owner.scalar(text("SELECT pg_backend_pid()"))
        blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        if clock_case.startswith("wait_"):
            await blocker.execute(
                select(ServiceAttempt).where(ServiceAttempt.id == attempt_id).with_for_update()
            )

        async def invoke():
            try:
                await getattr(lifecycle, operation)(owner, **kwargs)
            except lifecycle.StaleLeaseError:
                return True
            return False

        task = asyncio.create_task(invoke())
        try:
            async with asyncio.timeout(10):  # Hang guard, not a lease-duration wait.
                if clock_case.startswith("wait_"):
                    # Observe the actual lock wait before advancing the application clock.
                    connection = await observer.connection(
                        execution_options={"isolation_level": "AUTOCOMMIT"}
                    )
                    while not await connection.scalar(text("""
                        SELECT EXISTS (
                            SELECT 1 FROM pg_stat_activity
                            WHERE pid = :owner AND wait_event_type = 'Lock'
                              AND :blocker = ANY(pg_blocking_pids(pid))
                        )
                    """), {"owner": owner_pid, "blocker": blocker_pid}):
                        assert not task.done(), "Owner did not wait on the held attempt lock"
                    assert not task.done()
                    clock = start + timedelta(seconds=2) if clock_case == "wait_valid" else late
                    if clock_case == "wait_valid":
                        expected_time = clock
                    await blocker.rollback()
                was_rejected = await task
                assert was_rejected == rejected

                row = await owner.get(ServiceAttempt, attempt_id)
                assert row is not None
                if rejected:
                    assert (row.state, row.heartbeat_at, row.lease_expires_at) == (
                        "starting", start, expires,
                    )
                    assert row.ready_at is None and row.stopped_at is None
                elif operation == "complete_attempt":
                    assert row.state == "stopped" and row.stopped_at == expected_time
                else:
                    assert row.heartbeat_at == expected_time
                    if operation == "mark_attempt_ready":
                        assert row.state == "running" and row.ready_at == expected_time
                    else:
                        assert row.lease_expires_at == expected_time + timedelta(seconds=60)
        finally:
            await blocker.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await owner.rollback()


@pytest.mark.asyncio
async def test_timing_fixture_survives_live_sweeper(async_session_factory, timing_attempt):
    """The real supervisor must not expire the synthetic clock's baseline."""
    attempt_id, _, _ = timing_attempt
    async with async_session_factory() as db:
        await lifecycle.expire_leases(db, now=datetime.now(timezone.utc))
        await db.commit()
        attempt = await db.get(ServiceAttempt, attempt_id)
        assert attempt is not None
        assert attempt.state == "starting"
