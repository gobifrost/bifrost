"""Slice 2: service lifecycle domain — definitions, claims, fencing, restart.

Uses the real test-stack PostgreSQL via db_session (rollback-isolated).
Covers ensure/idempotence, desired-state transitions, worker-pull claims
(including concurrent singleton), lease fencing, backoff/crash-loop math,
lease expiry, and observed-state derivation.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm.services import ServiceAttempt
from src.services import service_lifecycle
from src.services.service_lifecycle import (
    ServiceConflictError,
    ServiceValidationError,
    StaleLeaseError,
)


async def _ensure_org(db_session):
    """Insert a real Organization row (workflows FK-restrict to it)."""
    from src.models.orm.organizations import Organization

    org = Organization(
        id=uuid4(), name=f"acme_{uuid4().hex[:8]}", created_by="tester@example.com"
    )
    db_session.add(org)
    await db_session.flush()
    return org


def _workflow_row(db_session, workflow_id=None, org_id="org-scoped", type="service", _org=None):
    """Insert a real Workflow row (definitions FK-restrict to it)."""
    from src.models.orm.workflows import Workflow

    # Unique natural key per row: the concurrency test commits, so fixed
    # (path, function_name) values would collide across tests.
    suffix = uuid4().hex[:8]
    # Services are always org-scoped (claim_eligible_service skips org-less
    # definitions); the caller passes a real org id, or org_id=None to opt
    # out. Random UUIDs violate the organizations FK, so tests must use one
    # inserted above.
    row = Workflow(
        id=workflow_id or uuid4(),
        name=f"telegram_bridge_{suffix}",
        function_name=f"telegram_bridge_{suffix}",
        path=f"workflows/telegram_bridge_{suffix}.py",
        type=type,
        organization_id=(_org.id if _org is not None else org_id),
        is_active=True,
    )
    db_session.add(row)
    return row


async def _ensure(db_session, org_id="org-scoped", **overrides):
    if org_id == "org-scoped":
        overrides["_org"] = await _ensure_org(db_session)
    else:
        overrides["org_id"] = org_id
    wf = _workflow_row(db_session, **overrides)
    await db_session.flush()
    definition = await service_lifecycle.ensure_definition_for_workflow(
        db_session, wf, created_by="tester@example.com"
    )
    await db_session.flush()
    return definition, wf


# --- Definitions ---


@pytest.mark.asyncio
async def test_ensure_creates_defaults(db_session):
    """First ensure creates an enabled, running-desired, always-restart definition."""
    definition, wf = await _ensure(db_session)

    assert definition.workflow_id == wf.id
    assert definition.enabled is True
    assert definition.startup_policy == "automatic"
    assert definition.restart_policy == "always"
    assert definition.desired_state == "running"
    assert definition.blocked_reason is None
    assert definition.restart_eligible_at is None
    assert definition.created_by == "tester@example.com"


@pytest.mark.asyncio
async def test_ensure_is_idempotent_and_preserves_policy(db_session):
    """Re-ensure never resets operator policy."""
    definition, wf = await _ensure(db_session)
    await service_lifecycle.update_policy(db_session, definition, restart_policy="never")
    await service_lifecycle.stop_service(db_session, definition)

    again = await service_lifecycle.ensure_definition_for_workflow(
        db_session, wf, created_by="other@example.com"
    )
    assert again.id == definition.id
    assert again.restart_policy == "never"
    assert again.desired_state == "stopped"


@pytest.mark.asyncio
async def test_sync_parks_converted_rows(db_session):
    """A row converting away from service keeps history but cannot launch."""
    definition, wf = await _ensure(db_session)
    attempt = await service_lifecycle.claim_eligible_service(
        db_session, worker_id="w1"
    )
    assert attempt is not None

    wf.type = "workflow"
    parked = await service_lifecycle.sync_definition_for_registration(
        db_session, wf, created_by="tester@example.com"
    )
    assert parked.enabled is False
    assert parked.desired_state == "stopped"
    live = await service_lifecycle.get_live_attempt(db_session, definition.id)
    assert live is not None and live.stop_requested_at is not None

    # And a non-service row without a definition stays definition-less.
    other_org = await _ensure_org(db_session)
    other = _workflow_row(db_session, type="workflow", _org=other_org)
    await db_session.flush()
    assert (
        await service_lifecycle.sync_definition_for_registration(
            db_session, other, created_by="t"
        )
    ) is None


@pytest.mark.asyncio
async def test_update_policy_validates(db_session):
    """Unknown fields and out-of-range values are rejected."""
    definition, _ = await _ensure(db_session)

    with pytest.raises(ServiceValidationError, match="Unknown policy field"):
        await service_lifecycle.update_policy(db_session, definition, nonsense=1)
    with pytest.raises(ServiceValidationError, match="Invalid restart_policy"):
        await service_lifecycle.update_policy(db_session, definition, restart_policy="sometimes")
    with pytest.raises(ServiceValidationError, match="Invalid graceful_shutdown_seconds"):
        await service_lifecycle.update_policy(db_session, definition, graceful_shutdown_seconds=9999)

    updated = await service_lifecycle.update_policy(
        db_session,
        definition,
        restart_policy="on_failure",
        graceful_shutdown_seconds=10,
    )
    assert updated.restart_policy == "on_failure"
    assert updated.graceful_shutdown_seconds == 10


# --- Desired state ---


@pytest.mark.asyncio
async def test_stop_marks_live_attempt(db_session):
    """Stop stores desire first and asks the live attempt to stop."""
    definition, _ = await _ensure(db_session)
    attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")
    assert attempt is not None

    stopped = await service_lifecycle.stop_service(db_session, definition)
    assert stopped.desired_state == "stopped"
    live = await service_lifecycle.get_live_attempt(db_session, definition.id)
    assert live is not None and live.stop_requested_at is not None


@pytest.mark.asyncio
async def test_start_requires_enabled(db_session):
    """Starting a disabled service conflicts; enable+start recovers."""
    definition, _ = await _ensure(db_session)
    await service_lifecycle.set_enabled(db_session, definition, False)

    with pytest.raises(ServiceConflictError, match="disabled"):
        await service_lifecycle.start_service(db_session, definition)

    await service_lifecycle.set_enabled(db_session, definition, True)
    assert definition.blocked_reason is None
    started = await service_lifecycle.start_service(db_session, definition)
    assert started.desired_state == "running"


@pytest.mark.asyncio
async def test_restart_asks_live_attempt_to_stop(db_session):
    """Restart keeps running desire and flags the live attempt."""
    definition, _ = await _ensure(db_session)
    await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")

    restarted = await service_lifecycle.restart_service(db_session, definition)
    assert restarted.desired_state == "running"
    live = await service_lifecycle.get_live_attempt(db_session, definition.id)
    assert live is not None and live.stop_requested_at is not None

    with pytest.raises(ServiceConflictError, match="disabled"):
        await service_lifecycle.set_enabled(db_session, definition, False)
        await service_lifecycle.restart_service(db_session, definition)


# --- Claims ---


@pytest.mark.asyncio
async def test_claim_returns_none_when_nothing_eligible(db_session):
    """Stopped desire, disabled flag, and suppression all block claims."""
    definition, _ = await _ensure(db_session)

    await service_lifecycle.stop_service(db_session, definition)
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is None

    await service_lifecycle.start_service(db_session, definition)
    await service_lifecycle.set_enabled(db_session, definition, False)
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is None

    await service_lifecycle.set_enabled(db_session, definition, True)
    definition.blocked_reason = "policy"
    await db_session.flush()
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is None


@pytest.mark.asyncio
async def test_second_claim_blocked_while_live(db_session):
    """A live attempt suppresses further claims for the same service."""
    definition, _ = await _ensure(db_session)
    first = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")
    assert first is not None
    assert first.state == "starting"
    assert first.restart_number == 0

    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w2") is None


@pytest.mark.asyncio
async def test_concurrent_claims_yield_single_attempt(async_session_factory):
    """Two workers racing claim exactly one attempt (SKIP LOCKED + index)."""
    import asyncio

    async with async_session_factory() as setup:
        definition, wf = await _ensure(setup)
        service_id = definition.id
        definition_id = definition.id
        workflow_id = wf.id
        org_id = definition.organization_id
        await setup.commit()

    try:
        async def try_claim(worker_id):
            async with async_session_factory() as session:
                try:
                    attempt = await service_lifecycle.claim_eligible_service(
                        session, worker_id=worker_id
                    )
                    await session.commit()
                    return attempt.id if attempt else None
                except Exception:
                    await session.rollback()
                    return "conflict"

        results = await asyncio.gather(try_claim("w1"), try_claim("w2"))
        assert sorted([r is not None and r != "conflict" for r in results]) == [False, True]

        from sqlalchemy import func as sa_func

        async with async_session_factory() as check:
            total = await check.scalar(
                select(sa_func.count(ServiceAttempt.id)).where(
                    ServiceAttempt.service_id == service_id
                )
            )
            assert total == 1
    finally:
        # Committed rows survive db_session rollback: remove them so later
        # files (e.g. claim-loop completion counts) see a clean table.
        from sqlalchemy import delete as sa_delete

        from src.models.orm.organizations import Organization
        from src.models.orm.workflows import Workflow
        from src.models.orm.services import ServiceDefinition

        async with async_session_factory() as cleanup:
            await cleanup.execute(
                sa_delete(ServiceAttempt).where(
                    ServiceAttempt.service_id == service_id
                )
            )
            await cleanup.execute(
                sa_delete(ServiceDefinition).where(
                    ServiceDefinition.id == definition_id
                )
            )
            await cleanup.execute(
                sa_delete(Workflow).where(Workflow.id == workflow_id)
            )
            if org_id is not None:
                await cleanup.execute(
                    sa_delete(Organization).where(Organization.id == org_id)
                )
            await cleanup.commit()


# --- Fencing ---


@pytest.mark.asyncio
async def test_heartbeat_rejects_stale_token(db_session):
    """Wrong lease tokens fail heartbeat and completion."""
    definition, _ = await _ensure(db_session)
    attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")

    renewed = await service_lifecycle.heartbeat_attempt(
        db_session, attempt_id=attempt.id, lease_token=attempt.lease_token
    )
    assert renewed.heartbeat_at is not None

    with pytest.raises(StaleLeaseError):
        await service_lifecycle.heartbeat_attempt(
            db_session, attempt_id=attempt.id, lease_token="wrong"
        )
    with pytest.raises(StaleLeaseError):
        await service_lifecycle.complete_attempt(
            db_session, attempt_id=attempt.id, lease_token="wrong", reason="failed"
        )


@pytest.mark.asyncio
async def test_ready_transitions_starting_to_running(db_session):
    """service.ready() moves the attempt to running exactly once."""
    definition, _ = await _ensure(db_session)
    attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")

    ready = await service_lifecycle.mark_attempt_ready(
        db_session, attempt_id=attempt.id, lease_token=attempt.lease_token
    )
    assert ready.state == "running"
    assert ready.ready_at is not None


# --- Restart decisions ---


@pytest.mark.asyncio
async def test_clean_return_always_reschedules(db_session):
    """Clean exit under always restarts promptly without failure accounting."""
    definition, _ = await _ensure(db_session)
    attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")

    updated = await service_lifecycle.complete_attempt(
        db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
        reason="clean_return",
    )
    assert updated.blocked_reason is None
    assert updated.restart_eligible_at is not None
    delta = (updated.restart_eligible_at - service_lifecycle._now()).total_seconds()
    assert 0 <= delta <= definition.restart_backoff_initial_seconds


@pytest.mark.asyncio
async def test_clean_return_on_failure_blocks(db_session):
    """Clean exit under on_failure parks with a policy block."""
    definition, _ = await _ensure(db_session)
    await service_lifecycle.update_policy(db_session, definition, restart_policy="on_failure")
    attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")

    updated = await service_lifecycle.complete_attempt(
        db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
        reason="clean_return",
    )
    assert updated.blocked_reason == "policy"
    assert updated.restart_eligible_at is None
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is None


@pytest.mark.asyncio
async def test_failed_never_blocks(db_session):
    """Failures under never park with a policy block."""
    definition, _ = await _ensure(db_session)
    await service_lifecycle.update_policy(db_session, definition, restart_policy="never")
    attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")

    updated = await service_lifecycle.complete_attempt(
        db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
        reason="failed", error="boom",
    )
    assert updated.blocked_reason == "policy"


@pytest.mark.asyncio
async def test_repeated_failures_enter_crash_loop(db_session):
    """Rapid failures transition to crash-loop and stop scheduling."""
    definition, _ = await _ensure(db_session)
    await service_lifecycle.update_policy(
        db_session, definition,
        crash_loop_max_restarts=3, crash_loop_window_seconds=3600,
        restart_backoff_initial_seconds=0, restart_backoff_max_seconds=0,
    )

    for _ in range(3):
        attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")
        assert attempt is not None
        definition = await service_lifecycle.complete_attempt(
            db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
            reason="failed", error="boom",
        )

    assert definition.blocked_reason == "crash_loop"
    assert definition.restart_eligible_at is None
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is None

    # Manual restart clears the loop.
    restarted = await service_lifecycle.restart_service(db_session, definition)
    assert restarted.blocked_reason is None
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is not None


@pytest.mark.asyncio
async def test_backoff_is_bounded_by_max(db_session):
    """Restart delays never exceed the policy cap."""
    definition, _ = await _ensure(db_session)
    await service_lifecycle.update_policy(
        db_session, definition,
        restart_backoff_initial_seconds=100, restart_backoff_max_seconds=150,
        crash_loop_max_restarts=100, crash_loop_window_seconds=3600,
    )

    for i in range(4):
        attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")
        assert attempt is not None
        definition = await service_lifecycle.complete_attempt(
            db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
            reason="failed", error="boom",
        )
        if i < 3:
            # Force immediate re-eligibility to observe the next delay.
            definition.restart_eligible_at = service_lifecycle._now()
            await db_session.flush()

    assert definition.blocked_reason is None
    assert definition.restart_eligible_at is not None
    delta = (definition.restart_eligible_at - service_lifecycle._now()).total_seconds()
    assert 0 <= delta <= 150


@pytest.mark.asyncio
async def test_requested_completion_reschedules_without_accounting(db_session):
    """Rolling-restart completions come back promptly and don't crash-loop."""
    definition, _ = await _ensure(db_session)
    await service_lifecycle.update_policy(
        db_session, definition, crash_loop_max_restarts=2, crash_loop_window_seconds=3600
    )
    for _ in range(3):
        attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")
        definition = await service_lifecycle.complete_attempt(
            db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
            reason="requested",
        )
        definition.restart_eligible_at = service_lifecycle._now()
        await db_session.flush()

    assert definition.blocked_reason is None


@pytest.mark.asyncio
async def test_expire_leases_fails_and_reschedules(db_session):
    """Expired leases become failed attempts with restart decisions applied."""
    definition, _ = await _ensure(db_session)
    attempt = await service_lifecycle.claim_eligible_service(
        db_session, worker_id="w1", lease_ttl_seconds=-1
    )
    assert attempt is not None

    expired = await service_lifecycle.expire_leases(db_session)
    assert expired == 1

    await db_session.refresh(attempt)
    assert attempt.state == "failed"
    assert attempt.exit_reason == "lease_expired"
    await db_session.refresh(definition)
    assert definition.restart_eligible_at is not None
    # Backoff gates the next claim; force eligibility to prove recovery works.
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w2") is None
    definition.restart_eligible_at = service_lifecycle._now()
    await db_session.flush()
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w2") is not None


# --- Observed state ---


@pytest.mark.asyncio
async def test_observed_state_mapping(db_session):
    """Desired + definition + live attempt derive the UI state."""
    definition, _ = await _ensure(db_session)

    assert service_lifecycle.describe_observed_state(definition, None) == "restarting"
    attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")
    assert service_lifecycle.describe_observed_state(definition, attempt) == "starting"
    await service_lifecycle.mark_attempt_ready(
        db_session, attempt_id=attempt.id, lease_token=attempt.lease_token
    )
    assert service_lifecycle.describe_observed_state(definition, attempt) == "running"
    await service_lifecycle.stop_service(db_session, definition)
    assert service_lifecycle.describe_observed_state(definition, attempt) == "stopping"

    await service_lifecycle.complete_attempt(
        db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
        reason="requested",
    )
    assert service_lifecycle.describe_observed_state(definition, None) == "stopped"


@pytest.mark.asyncio
async def test_list_attempts_paginates(db_session):
    """Attempts list newest-first with totals."""
    definition, _ = await _ensure(db_session)
    attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")
    await service_lifecycle.complete_attempt(
        db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
        reason="requested",
    )
    definition.restart_eligible_at = service_lifecycle._now()
    await db_session.flush()
    await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")

    items, total = await service_lifecycle.list_attempts(db_session, definition.id)
    assert total == 2
    assert items[0].created_at >= items[1].created_at


# --- Codex review fixes ---


@pytest.mark.asyncio
async def test_expired_lease_fences_owner_writes(db_session):
    """An owner past lease expiry cannot renew, report, or complete."""
    from datetime import timedelta

    definition, _ = await _ensure(db_session)
    attempt = await service_lifecycle.claim_eligible_service(
        db_session, worker_id="w1", lease_ttl_seconds=60
    )
    # Age the lease past expiry without sweeping.
    attempt.lease_expires_at = service_lifecycle._now() - timedelta(seconds=1)
    await db_session.flush()

    with pytest.raises(StaleLeaseError):
        await service_lifecycle.heartbeat_attempt(
            db_session, attempt_id=attempt.id, lease_token=attempt.lease_token
        )
    with pytest.raises(StaleLeaseError):
        await service_lifecycle.mark_attempt_ready(
            db_session, attempt_id=attempt.id, lease_token=attempt.lease_token
        )
    with pytest.raises(StaleLeaseError):
        await service_lifecycle.complete_attempt(
            db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
            reason="failed",
        )
    # The sweep still terminalizes it exactly once.
    assert await service_lifecycle.expire_leases(db_session) == 1


@pytest.mark.asyncio
async def test_claim_requires_active_service_source(db_session):
    """Deactivated or converted sources are never claimable."""
    definition, wf = await _ensure(db_session)

    wf.is_active = False
    await db_session.flush()
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is None

    wf.is_active = True
    wf.type = "workflow"
    await db_session.flush()
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is None

    wf.type = "service"
    await db_session.flush()
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is not None


@pytest.mark.asyncio
async def test_claim_skips_org_less_definitions(db_session):
    """Services are always org-scoped: org-less definitions never launch."""
    definition, _ = await _ensure(db_session, org_id=None)
    assert definition.organization_id is None
    assert await service_lifecycle.claim_eligible_service(db_session, worker_id="w1") is None


@pytest.mark.asyncio
async def test_window_counts_terminal_time(db_session):
    """A long-running service failing now counts now (not at creation)."""
    from datetime import timedelta

    from sqlalchemy import update as sa_update

    definition, _ = await _ensure(db_session)
    await service_lifecycle.update_policy(
        db_session, definition, crash_loop_max_restarts=1, crash_loop_window_seconds=3600
    )
    attempt = await service_lifecycle.claim_eligible_service(db_session, worker_id="w1")
    # The run started two hours ago; it fails now.
    await db_session.execute(
        sa_update(ServiceAttempt)
        .where(ServiceAttempt.id == attempt.id)
        .values(created_at=service_lifecycle._now() - timedelta(hours=2))
    )
    await db_session.flush()

    updated = await service_lifecycle.complete_attempt(
        db_session, attempt_id=attempt.id, lease_token=attempt.lease_token,
        reason="failed", error="late failure",
    )
    # Counted by terminal time: one recent failure meets max_restarts=1.
    assert updated.blocked_reason == "crash_loop"


@pytest.mark.asyncio
async def test_last_terminal_attempt_returns_newest_terminal(db_session):
    """Last-exit display: newest terminal attempt; live attempts ignored."""
    from datetime import timedelta
    from uuid import uuid4

    definition, _ = await _ensure(db_session)
    base = service_lifecycle._now()
    rows = [
        # Older terminal failure.
        ServiceAttempt(
            id=uuid4(),
            service_id=definition.id,
            lease_token="tok-old",
            lease_expires_at=base,
            state="failed",
            exit_reason="crashed: broker refused",
            created_at=base - timedelta(hours=2),
        ),
        # Newer terminal stop.
        ServiceAttempt(
            id=uuid4(),
            service_id=definition.id,
            lease_token="tok-new",
            lease_expires_at=base,
            state="stopped",
            exit_reason="stop_requested",
            created_at=base - timedelta(hours=1),
        ),
        # Live attempt (must not shadow the terminal rows).
        ServiceAttempt(
            id=uuid4(),
            service_id=definition.id,
            lease_token="tok-live",
            lease_expires_at=base + timedelta(seconds=60),
            state="running",
            created_at=base,
        ),
    ]
    db_session.add_all(rows)
    await db_session.flush()

    last = await service_lifecycle.get_last_terminal_attempt(
        db_session, definition.id
    )
    assert last is not None
    assert last.exit_reason == "stop_requested"


@pytest.mark.asyncio
async def test_last_terminal_attempt_none_without_terminals(db_session):
    """No terminal history yet → None (UI renders no last-exit badge)."""
    definition, _ = await _ensure(db_session)
    assert (
        await service_lifecycle.get_last_terminal_attempt(
            db_session, definition.id
        )
        is None
    )


@pytest.mark.asyncio
async def test_batch_list_embedding_matches_single_lookups(db_session):
    """Batch observed-state maps equal the per-row helpers, all shapes.

    Seeds a live+history service, a terminal-only service, and an
    attempt-less service (plus an unknown id): the batch path must agree
    with get_live_attempt / get_last_terminal_attempt / count for every
    row, so the list endpoint's 3-query page renders the identical
    contract as the per-row path.
    """
    from datetime import timedelta
    from uuid import uuid4

    from sqlalchemy import func as sa_func

    def _row(definition, state, age_hours, exit_reason=None):
        base = service_lifecycle._now()
        return ServiceAttempt(
            id=uuid4(),
            service_id=definition.id,
            lease_token=f"tok-{uuid4().hex[:8]}",
            lease_expires_at=base + timedelta(seconds=60),
            state=state,
            exit_reason=exit_reason,
            created_at=base - timedelta(hours=age_hours),
        )

    live_def, _ = await _ensure(db_session)
    term_def, _ = await _ensure(db_session)
    empty_def, _ = await _ensure(db_session)
    db_session.add_all([
        _row(live_def, "failed", 3, exit_reason="old crash"),
        _row(live_def, "stopped", 2, exit_reason="old stop"),
        _row(live_def, "running", 0),
        _row(term_def, "failed", 1, exit_reason="only exit"),
    ])
    await db_session.flush()

    ids = [live_def.id, term_def.id, empty_def.id, uuid4()]
    live_map, terminal_map, count_map = await service_lifecycle.batch_list_embedding(
        db_session, ids
    )

    for definition, want_live_state, want_exit, want_count in (
        (live_def, "running", "old stop", 3),
        (term_def, None, "only exit", 1),
        (empty_def, None, None, 0),
    ):
        live = await service_lifecycle.get_live_attempt(
            db_session, definition.id
        )
        assert (live_map.get(definition.id) is None) == (live is None)
        if live is not None:
            assert live_map[definition.id].id == live.id
            assert live_map[definition.id].state == want_live_state

        last = await service_lifecycle.get_last_terminal_attempt(
            db_session, definition.id
        )
        assert (terminal_map.get(definition.id) is None) == (last is None)
        if last is not None:
            assert terminal_map[definition.id].id == last.id
            assert terminal_map[definition.id].exit_reason == want_exit

        count = await db_session.scalar(
            select(sa_func.count(ServiceAttempt.id)).where(
                ServiceAttempt.service_id == definition.id
            )
        )
        assert count_map.get(definition.id, 0) == (count or 0) == want_count

    assert await service_lifecycle.batch_list_embedding(db_session, []) == (
        {},
        {},
        {},
    )
