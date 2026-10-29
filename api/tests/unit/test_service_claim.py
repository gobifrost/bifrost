"""Slice 3: worker-pull claim loop — expire, beat, claim, complete.

Uses the real test-stack PostgreSQL (rollback-isolated db_session) with a
stub pool and faked Redis. Covers claiming, supervision beats (heartbeat,
ready drain, stop mirror, token rotation), completion mapping, fencing,
capacity, the org gate, and shutdown handover.
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.cache.keys import (
    service_ready_key,
    service_stop_key,
    service_token_key,
)
from src.services import service_lifecycle
from src.services.service_claim import OwnedAttempt, ServiceClaimLoop


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    async def set(self, key, value, ex=None):
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
        return len(keys)


# The test stack's worker container runs a real claim loop against this same
# database. Services created here become eligible a day ahead of wall-clock
# time, and this process's lifecycle clock runs two days ahead, so only the
# loop under test can ever claim them.
_ELIGIBLE_AHEAD = timedelta(days=1)
_CLOCK_AHEAD = timedelta(days=2)


@pytest.fixture(autouse=True)
def _clock_ahead_of_stack_worker():
    real_now = service_lifecycle._now
    with patch.object(
        service_lifecycle, "_now", lambda: real_now() + _CLOCK_AHEAD
    ):
        yield


@asynccontextmanager
async def _fake_redis_holder(holder):
    yield holder["redis"]


@pytest.fixture
def redis_holder():
    return {"redis": FakeRedis()}


@pytest.fixture(autouse=True)
def _patch_redis(redis_holder):
    with patch(
        "src.core.cache.get_redis",
        side_effect=lambda: _fake_redis_holder(redis_holder),
    ):
        yield


# Rows committed through the loop's own sessions (not db_session) persist
# past the test: claim_eligible_service claims ANY eligible service, so
# leftovers would leak into other tests' claims. Track and remove them.
_created_definition_ids: list = []
_created_workflow_ids: list = []
_created_org_ids: list = []


@pytest.fixture(autouse=True)
async def _cleanup_services(async_session_factory):
    _created_definition_ids.clear()
    _created_workflow_ids.clear()
    _created_org_ids.clear()
    yield
    from sqlalchemy import delete as sa_delete

    from src.models.orm.services import ServiceAttempt, ServiceDefinition
    from src.models.orm.workflows import Workflow
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User

    async with async_session_factory() as session:
        if _created_definition_ids:
            await session.execute(
                sa_delete(ServiceAttempt).where(
                    ServiceAttempt.service_id.in_(_created_definition_ids)
                )
            )
            await session.execute(
                sa_delete(ServiceDefinition).where(
                    ServiceDefinition.id.in_(_created_definition_ids)
                )
            )
        if _created_workflow_ids:
            await session.execute(
                sa_delete(Workflow).where(
                    Workflow.id.in_(_created_workflow_ids)
                )
            )
        if _created_org_ids:
            await session.execute(
                sa_delete(User).where(
                    User.organization_id.in_(_created_org_ids),
                    User.identity_kind.is_not(None),
                )
            )
            await session.execute(
                sa_delete(Organization).where(
                    Organization.id.in_(_created_org_ids)
                )
            )
        await session.commit()


class StubPool:
    def __init__(self):
        self.service_processes = {}
        self.route_service = AsyncMock()
        self.stop_service_child = AsyncMock(return_value=True)


async def _ensure_org(db_session):
    from shared.identities import ensure_default_identity
    from src.models.orm.organizations import Organization

    org = Organization(
        id=uuid4(), name=f"Contoso {uuid4().hex[:8]}", created_by="tester"
    )
    db_session.add(org)
    await db_session.flush()
    await ensure_default_identity(db_session, org)
    return org


async def _ensure_service(db_session, org=None, **overrides):
    from src.models.orm.workflows import Workflow

    org = org or await _ensure_org(db_session)
    suffix = uuid4().hex[:8]
    wf = Workflow(
        id=uuid4(),
        name=f"svc_{suffix}",
        function_name=f"svc_{suffix}",
        path=f"workflows/svc_{suffix}.py",
        type="service",
        organization_id=org.id,
        is_active=True,
    )
    db_session.add(wf)
    await db_session.flush()
    definition = await service_lifecycle.ensure_definition_for_workflow(
        db_session, wf, created_by="tester"
    )
    definition.restart_eligible_at = datetime.now(timezone.utc) + _ELIGIBLE_AHEAD
    for key, value in overrides.items():
        setattr(definition, key, value)
    await db_session.flush()
    _created_definition_ids.append(definition.id)
    _created_workflow_ids.append(wf.id)
    _created_org_ids.append(org.id)
    return definition, wf, org


def _fits(free_bytes):  # type: ignore[no-untyped-def]
    """An executor gate that always offers ``free_bytes``."""

    @asynccontextmanager
    async def claiming():  # type: ignore[no-untyped-def]
        yield free_bytes

    return claiming


def _loop(pool, **overrides):
    args = {
        "worker_id": "worker-1",
        "pool": pool,
        "claim_interval_seconds": 0.01,
        "beat_interval_seconds": 0,
        "lease_ttl_seconds": 60,
        "claiming": _fits(2**40),
        "hold_memory": lambda attempt_id, request: None,
    }
    args.update(overrides)
    return ServiceClaimLoop(**args)


@pytest.mark.parametrize("platform_role", [False, True])
async def test_tick_claims_and_routes_with_service_context(
    db_session, redis_holder, platform_role
):
    definition, wf, org = await _ensure_service(db_session)
    if platform_role:
        from sqlalchemy import select
        from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
        from src.models.orm.users import User, UserRole, UserRoleBoundary

        identity_id = await db_session.scalar(select(User.id).where(
            User.organization_id == org.id, User.identity_kind == "org_default",
        ))
        db_session.add_all([
            UserRole(user_id=identity_id, role_id=PLATFORM_ADMIN_ROLE_ID, assigned_by="test"),
            UserRoleBoundary(user_id=identity_id, role_id=PLATFORM_ADMIN_ROLE_ID, kind="platform"),
        ])
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)

    await loop.tick()

    pool.route_service.assert_awaited_once()
    kwargs = pool.route_service.await_args.kwargs
    assert kwargs["service_id"] == str(definition.id)
    context = kwargs["context"]
    assert context["function_name"] == wf.function_name
    assert context["file_path"] == wf.path
    from src.models.orm.services import ServiceAttempt

    attempt = await db_session.get(ServiceAttempt, kwargs["attempt_id"])
    assert attempt.run_user_id is not None
    assert context["caller"]["user_id"] == str(attempt.run_user_id)
    assert context["is_platform_admin"] is platform_role
    assert context["organization"]["id"] == str(org.id)
    assert context["service"]["lease_token"]
    assert context["service"]["token"]
    # Token handoff written for the child to rotate from.
    attempt_id = kwargs["attempt_id"]
    assert attempt_id in loop._owned
    assert redis_holder["redis"].values.get(
        service_token_key(attempt_id)
    ) is not None
    # No double claim on the next tick: the live attempt suppresses it.
    pool.route_service.reset_mock()
    await loop.tick()
    pool.route_service.assert_not_awaited()


async def test_tick_skips_a_service_larger_than_the_free_budget(db_session):
    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool, claiming=_fits(1))

    await loop.tick()

    pool.route_service.assert_not_awaited()
    live = await service_lifecycle.get_live_attempt(db_session, definition.id)
    assert live is None


async def test_a_supervised_service_holds_its_request_until_it_ends(db_session):
    from src.services.memory_requests import DEFAULT_WORKFLOW_REQUEST_BYTES

    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    holds: dict = {}

    def hold(attempt_id, request):  # type: ignore[no-untyped-def]
        if request is None:
            holds.pop(attempt_id, None)
        else:
            holds[attempt_id] = request

    pool = StubPool()
    held_at_fork: list[dict] = []
    pool.route_service.side_effect = lambda **kwargs: held_at_fork.append(dict(holds))
    loop = _loop(pool, hold_memory=hold)
    await loop.tick()

    live = await service_lifecycle.get_live_attempt(db_session, definition.id)
    assert live is not None
    assert held_at_fork == [{str(live.id): DEFAULT_WORKFLOW_REQUEST_BYTES}], (
        "held before the child forks, so no other claim spends the same bytes"
    )
    assert holds == {str(live.id): DEFAULT_WORKFLOW_REQUEST_BYTES}
    await loop._release(loop._owned.pop(str(live.id)))
    assert holds == {}


async def test_a_service_that_fails_to_launch_gives_its_memory_back(db_session):
    await _ensure_service(db_session)
    await db_session.commit()
    holds: dict = {}

    def hold(attempt_id, request):  # type: ignore[no-untyped-def]
        if request is None:
            holds.pop(attempt_id, None)
        else:
            holds[attempt_id] = request

    pool = StubPool()
    pool.route_service.side_effect = RuntimeError("fork failed")
    loop = _loop(pool, hold_memory=hold)
    await loop.tick()

    pool.route_service.assert_awaited_once()
    assert holds == {}


async def test_beat_renews_lease_and_rotates_token(db_session, redis_holder):
    from src.models.orm.services import ServiceAttempt

    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)
    await loop.tick()
    attempt_id = pool.route_service.await_args.kwargs["attempt_id"]
    before = (
        await db_session.get(ServiceAttempt, attempt_id)
    ).lease_expires_at
    assert service_token_key(attempt_id) in redis_holder["redis"].values

    loop._last_beat = 0
    await loop.tick()

    after = (
        await db_session.get(ServiceAttempt, attempt_id)
    ).lease_expires_at
    assert after > before


async def test_service_rotation_keeps_only_durable_credential_identifiers(redis_holder):
    from src.core.security import decode_token

    service_id, attempt_id = uuid4(), uuid4()
    definition = SimpleNamespace(organization_id=uuid4(), solution_id=uuid4())
    db = AsyncMock()
    owned = OwnedAttempt(attempt_id=attempt_id, service_id=service_id, lease_token="lease")
    loop = _loop(StubPool())

    await loop._rotate_token(db, owned, definition)
    token = json.loads(redis_holder["redis"].values[service_token_key(str(attempt_id))])["token"]
    claims = decode_token(token, expected_type="access")

    assert claims["sub"] == str(attempt_id)
    assert claims["credential_kind"] == "service_attempt"
    assert claims["service_id"] == str(service_id)
    assert not {"engine_solution_id", "engine_global_repo_access", "org_id", "is_superuser"} & claims.keys()
    db.get.assert_not_awaited()


async def test_ready_drain_marks_attempt_running(db_session, redis_holder):
    await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)
    await loop.tick()
    attempt_id = pool.route_service.await_args.kwargs["attempt_id"]
    redis_holder["redis"].values[service_ready_key(attempt_id)] = "1"

    loop._last_beat = 0
    await loop.tick()

    from src.models.orm.services import ServiceAttempt

    attempt = await db_session.get(ServiceAttempt, attempt_id)
    assert attempt.state == "running"
    assert attempt.ready_at is not None


async def test_stop_mirror_notifies_child_once(db_session, redis_holder):
    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)
    await loop.tick()
    attempt_id = pool.route_service.await_args.kwargs["attempt_id"]

    await service_lifecycle.stop_service(db_session, definition)
    await db_session.commit()
    loop._last_beat = 0
    await loop.tick()
    loop._last_beat = 0
    await loop.tick()

    assert redis_holder["redis"].values.get(service_stop_key(attempt_id)) == "1"
    pool.stop_service_child.assert_awaited_once_with(attempt_id)


async def test_stop_mirror_closes_tick_database_context_before_child_termination(
    db_session, redis_holder, monkeypatch
):
    """A child result may acquire the DB while its stop is being awaited."""
    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)
    await loop.tick()
    attempt_id = pool.route_service.await_args.kwargs["attempt_id"]

    await service_lifecycle.stop_service(db_session, definition)
    await db_session.commit()

    from src.core import database

    real_get_db_context = database.get_db_context
    state = {"open_contexts": 0, "callback_completed": False}

    @asynccontextmanager
    async def tracked_db_context():
        state["open_contexts"] += 1
        try:
            async with real_get_db_context() as db:
                yield db
        finally:
            state["open_contexts"] -= 1

    async def stop_child(_attempt_id):
        assert state["open_contexts"] == 0
        await loop.handle_service_result({
            "success": True,
            "status": "Success",
            "service": {
                "service_id": str(definition.id),
                "attempt_id": attempt_id,
                "lease_token": loop._owned[attempt_id].lease_token,
            },
        })
        state["callback_completed"] = True
        return True

    monkeypatch.setattr(database, "get_db_context", tracked_db_context)
    pool.stop_service_child.side_effect = stop_child
    loop._last_beat = 0

    await loop.tick()

    assert state["callback_completed"] is True
    pool.stop_service_child.assert_awaited_once_with(attempt_id)
    from src.models.orm.services import ServiceAttempt

    attempt = await db_session.get(ServiceAttempt, attempt_id)
    await db_session.refresh(attempt)
    assert attempt.state == "stopped"


async def test_success_completes_clean_return(db_session):
    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)
    await loop.tick()
    kwargs = pool.route_service.await_args.kwargs

    await loop.handle_service_result({
        "success": True,
        "status": "Success",
        "service": {
            "service_id": kwargs["service_id"],
            "attempt_id": kwargs["attempt_id"],
            "lease_token": kwargs["lease_token"],
        },
    })

    from src.models.orm.services import ServiceAttempt

    attempt = await db_session.get(ServiceAttempt, kwargs["attempt_id"])
    assert attempt.state == "stopped"
    # Completion ran in the loop's own session: refresh this snapshot.
    await db_session.refresh(definition)
    # restart_policy=always: prompt relaunch, no suppression.
    assert definition.blocked_reason is None
    assert definition.restart_eligible_at is not None


async def test_stop_requested_completes_without_failure_accounting(db_session):
    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)
    await loop.tick()
    kwargs = pool.route_service.await_args.kwargs

    await service_lifecycle.stop_service(db_session, definition)
    await db_session.commit()
    await loop.handle_service_result({
        "success": False,
        "status": "Failed",
        "error": "cancelled mid-shutdown",
        "service": {
            "service_id": kwargs["service_id"],
            "attempt_id": kwargs["attempt_id"],
            "lease_token": kwargs["lease_token"],
        },
    })

    from src.models.orm.services import ServiceAttempt

    attempt = await db_session.get(ServiceAttempt, kwargs["attempt_id"])
    assert attempt.state == "stopped"


async def test_stale_child_result_is_dropped(db_session):
    await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)
    await loop.tick()
    kwargs = pool.route_service.await_args.kwargs

    await loop.handle_service_result({
        "success": False,
        "error": "late duplicate",
        "service": {
            "service_id": kwargs["service_id"],
            "attempt_id": kwargs["attempt_id"],
            "lease_token": "wrong-token",
        },
    })

    from src.models.orm.services import ServiceAttempt

    attempt = await db_session.get(ServiceAttempt, kwargs["attempt_id"])
    # Still live: the fenced write was rejected, no terminal transition.
    assert attempt.state == "starting"


async def test_recycled_completion_restarts_without_failure(db_session):
    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)
    await loop.tick()
    kwargs = pool.route_service.await_args.kwargs

    await loop.handle_service_result({
        "success": False,
        "recycled": True,
        "recycle_reason": "template_recycle",
        "service": {
            "service_id": kwargs["service_id"],
            "attempt_id": kwargs["attempt_id"],
            "lease_token": kwargs["lease_token"],
        },
    })

    definition = await service_lifecycle.get_definition(
        db_session, definition.id
    )
    await db_session.refresh(definition)
    assert definition.blocked_reason is None


async def test_shutdown_handover_stops_owned_children(redis_holder):
    pool = StubPool()
    loop = _loop(pool, handover_wait_seconds=0)
    attempt_id = uuid4()
    loop._owned[str(attempt_id)] = OwnedAttempt(
        attempt_id=attempt_id,
        service_id=uuid4(),
        lease_token="lease",
    )

    await loop.stop()

    assert redis_holder["redis"].values.get(service_stop_key(str(attempt_id))) == "1"
    pool.stop_service_child.assert_awaited_once_with(str(attempt_id))
    assert loop._owned == {}


async def test_instant_child_outcome_completes_claimed_attempt(db_session):
    """A fast result must observe the committed claim (commit-before-fork)."""
    from src.models.orm.services import ServiceAttempt

    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)

    async def _instant_route(**kwargs):
        await loop.handle_service_result({
            "success": False,
            "status": "Failed",
            "error": "boom",
            "error_type": "RuntimeError",
            "service": {
                "service_id": kwargs["service_id"],
                "attempt_id": kwargs["attempt_id"],
                "lease_token": kwargs["lease_token"],
            },
        })

    pool.route_service.side_effect = _instant_route
    await loop.tick()

    # Find the claimed attempt through a fresh read, scoped to this test's
    # own service: the shared test DB can carry attempt history from other
    # services (e.g. real e2e service runs), which is legitimate data, not
    # state this test owns.
    from sqlalchemy import select as sa_select

    rows = (
        await db_session.execute(
            sa_select(ServiceAttempt).where(ServiceAttempt.service_id == definition.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].state == "failed"
    assert rows[0].error == "boom"


async def test_route_failure_completes_without_accounting(db_session):
    """A committed claim whose child never launches restarts promptly."""
    from src.models.orm.services import ServiceAttempt

    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    pool.route_service.side_effect = RuntimeError("no fork today")
    loop = _loop(pool)
    await loop.tick()

    # Scoped to this test's own service — see test_instant_child_outcome_*
    # for why a whole-table select isn't safe in a shared test DB.
    from sqlalchemy import select as sa_select

    rows = (
        await db_session.execute(
            sa_select(ServiceAttempt).where(ServiceAttempt.service_id == definition.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].state == "stopped"
    assert rows[0].exit_reason == "route_failed"
    await db_session.refresh(definition)
    assert definition.blocked_reason is None


async def test_startup_grace_breach_fails_unready_attempt(db_session):
    """An attempt that never reports ready fails once its grace elapses."""
    from src.models.orm.services import ServiceAttempt

    definition, _, _ = await _ensure_service(db_session)
    definition.startup_grace_seconds = 60
    await db_session.commit()
    pool = StubPool()
    loop = _loop(pool)
    await loop.tick()
    attempt_id = pool.route_service.await_args.kwargs["attempt_id"]

    # Age the attempt past its grace without a ready report.
    attempt = await db_session.get(ServiceAttempt, attempt_id)
    attempt.started_at = datetime.now(timezone.utc) - timedelta(seconds=600)
    await db_session.commit()

    loop._last_beat = 0
    await loop.tick()

    await db_session.refresh(attempt)
    assert attempt.state == "failed"
    assert attempt.exit_reason == "startup_grace_exceeded"
    assert attempt_id not in loop._owned


async def test_a_service_no_executor_can_hold_is_blocked_until_one_can(db_session):
    from src.services.memory_requests import DEFAULT_WORKFLOW_REQUEST_BYTES, MIB
    from src.services.worker_pool.unfit import TOO_LARGE, block_services_that_never_fit

    definition, _, _ = await _ensure_service(db_session)
    await db_session.commit()

    await block_services_that_never_fit(db_session, DEFAULT_WORKFLOW_REQUEST_BYTES - MIB)
    await db_session.commit()
    await db_session.refresh(definition)
    assert definition.blocked_reason == TOO_LARGE

    await block_services_that_never_fit(db_session, DEFAULT_WORKFLOW_REQUEST_BYTES)
    await db_session.commit()
    await db_session.refresh(definition)
    assert definition.blocked_reason is None


def _record_ticks(loop, then=None):  # type: ignore[no-untyped-def]
    ticks: list[float] = []

    async def tick() -> None:
        ticks.append(time.monotonic())
        if then is not None:
            then()

    loop.tick = tick
    return ticks


async def _until(condition, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition never held"
        await asyncio.sleep(0.005)


async def test_an_idle_loop_ticks_on_a_wake_up_not_every_claim_interval():
    wake = asyncio.Event()
    loop = _loop(StubPool(), wake=wake, sweep_seconds=30)
    ticks = _record_ticks(loop)
    await loop.start()
    try:
        await _until(lambda: len(ticks) == 1)
        await asyncio.sleep(0.1)  # ten claim intervals
        assert len(ticks) == 1

        wake.set()
        await _until(lambda: len(ticks) == 2)
    finally:
        await loop.stop()


async def test_a_supervising_loop_ticks_every_claim_interval():
    """Owned attempts need their leases renewed whether or not anything wakes."""
    loop = _loop(StubPool(), wake=asyncio.Event(), sweep_seconds=30)
    loop._owned["attempt"] = OwnedAttempt(
        attempt_id=uuid4(), service_id=uuid4(), lease_token="lease"
    )
    ticks = _record_ticks(loop)
    await loop.start()
    try:
        await _until(lambda: len(ticks) >= 3)
    finally:
        loop._owned.clear()
        await loop.stop()


async def test_an_idle_loop_ticks_when_the_next_restart_comes_due():
    loop = _loop(StubPool(), wake=asyncio.Event(), sweep_seconds=30)

    def restart_soon() -> None:
        loop._next_restart = datetime.now(timezone.utc) + timedelta(seconds=0.05)

    ticks = _record_ticks(loop, then=restart_soon)
    await loop.start()
    try:
        await _until(lambda: len(ticks) >= 2)
        assert ticks[1] - ticks[0] >= 0.04
    finally:
        await loop.stop()


async def test_an_ended_attempt_wakes_its_loop(db_session):
    """The loop reads the attempt's restart time (or claims its slot) at once."""
    await _ensure_service(db_session)
    await db_session.commit()
    pool = StubPool()
    wake = asyncio.Event()
    loop = _loop(pool, wake=wake)
    await loop.tick()
    kwargs = pool.route_service.await_args.kwargs
    assert not wake.is_set()

    await loop.handle_service_result({
        "success": True,
        "status": "Success",
        "service": {
            "service_id": kwargs["service_id"],
            "attempt_id": kwargs["attempt_id"],
            "lease_token": kwargs["lease_token"],
        },
    })

    assert wake.is_set()
