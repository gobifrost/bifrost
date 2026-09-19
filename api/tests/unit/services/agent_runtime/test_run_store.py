"""Fenced run-store transactions: claims, leases, checkpoints, lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import delete, select

from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunJournalEntry,
    AgentRunStep,
)
from src.services.agent_runtime import run_store
from src.services.agent_runtime import types as rt
from src.services.agent_runtime.resume import active_seconds_used

asyncio_mark = pytest.mark.asyncio


async def _create_queued_run(session, *, status="queued", **overrides):
    run = AgentRun(trigger_type="test", status=status, **overrides)
    session.add(run)
    await session.commit()
    return run


async def _cleanup_run(async_session_factory, run_id: UUID):
    async with async_session_factory() as session:
        for model in (AgentRunJournalEntry, AgentRunCheckpoint, AgentRunStep):
            await session.execute(
                delete(model).where(model.run_id == run_id)
            )
        await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await session.commit()


class TestTransitionMap:
    def test_terminal_states_have_no_outgoing_transitions(self):
        for status in rt.TERMINAL_STATUSES:
            assert rt.TRANSITIONS.get(status, frozenset()) == frozenset()

    def test_waiting_states_wake_only_to_running_or_terminal(self):
        for status in (rt.WAITING_CHILD, rt.WAITING_CHILDREN, rt.SLEEPING):
            allowed = rt.TRANSITIONS[status]
            assert rt.RUNNING in allowed
            assert rt.WAITING_CHILD not in allowed
            assert rt.SLEEPING not in allowed


class TestClaimRun:
    @asyncio_mark
    async def test_only_one_worker_claims_queued_run(self, async_session_factory):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id

        async def attempt(owner: str):
            async with async_session_factory() as session:
                try:
                    claimed = await run_store.claim_run(
                        session, run_id, owner
                    )
                    return ("claimed", owner, claimed.lease_token)
                except rt.RunNotClaimableError:
                    return ("rejected", owner, None)

        try:
            first, second = await asyncio.gather(
                attempt("worker-a"), attempt("worker-b")
            )
            outcomes = sorted([first[0], second[0]])
            assert outcomes == ["claimed", "rejected"]
            winner = first if first[0] == "claimed" else second
            async with async_session_factory() as session:
                stored = await session.get(AgentRun, run_id)
                assert stored is not None
                assert stored.status == "running"
                assert stored.lease_owner == winner[1]
                assert stored.lease_token == winner[2]
                assert stored.attempt == 1
        finally:
            await _cleanup_run(async_session_factory, run_id)

    @asyncio_mark
    async def test_expired_lease_reclaims_same_run(self, async_session_factory):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                first = await run_store.claim_run(session, run_id, "worker-a")
                assert first.attempt == 1
            # Simulate a dead worker: expire its lease directly.
            async with async_session_factory() as session:
                stored = await session.get(AgentRun, run_id)
                assert stored is not None
                stored.lease_expires_at = datetime.now(timezone.utc) - timedelta(
                    seconds=1
                )
                await session.commit()
            async with async_session_factory() as session:
                second = await run_store.claim_run(session, run_id, "worker-b")
                assert second.id == run_id
                assert second.attempt == 2
                assert second.lease_owner == "worker-b"
                assert second.lease_token != first.lease_token
        finally:
            await _cleanup_run(async_session_factory, run_id)


class TestLeaseFencing:
    @asyncio_mark
    async def test_stale_token_cannot_renew_journal_or_finish(
        self, async_session_factory
    ):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, run_id, "worker-a")
                good_token = claimed.lease_token
                assert good_token
            async with async_session_factory() as session:
                with pytest.raises(rt.LeaseMismatchError):
                    await run_store.renew_lease(session, run_id, "stale-token")
                with pytest.raises(rt.LeaseMismatchError):
                    await run_store.append_journal(
                        session, run_id, "stale-token", rt.JOURNAL_TOOL_CALL, {}
                    )
                with pytest.raises(rt.LeaseMismatchError):
                    await run_store.commit_checkpoint(
                        session, run_id, "stale-token", {"messages": []}
                    )
                with pytest.raises(rt.LeaseMismatchError):
                    await run_store.finish_run(
                        session, run_id, "stale-token", rt.COMPLETED
                    )
            async with async_session_factory() as session:
                renewed = await run_store.renew_lease(
                    session, run_id, good_token
                )
                assert renewed.lease_token == good_token
                assert renewed.lease_expires_at is not None
        finally:
            await _cleanup_run(async_session_factory, run_id)


class TestCancellationFencing:
    @asyncio_mark
    async def test_stale_cancellation_cannot_resurrect_completed_run(
        self, async_session_factory
    ):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as worker:
                claimed = await run_store.claim_run(worker, run_id, "worker-a")
                token = claimed.lease_token
                assert token

            # Simulate the router's old authorization/visibility read. The
            # completion wins before cancellation takes its authoritative lock.
            async with async_session_factory() as stale_session:
                stale = await stale_session.get(AgentRun, run_id)
                assert stale is not None and stale.status == rt.RUNNING
                async with async_session_factory() as worker:
                    await run_store.finish_run(
                        worker, run_id, token, rt.COMPLETED, output={"text": "done"}
                    )
                with pytest.raises(rt.InvalidTransitionError):
                    await run_store.request_cancellation(stale_session, run_id)

            async with async_session_factory() as session:
                stored = await session.get(AgentRun, run_id)
                assert stored is not None
                assert stored.status == rt.COMPLETED
                assert stored.output == {"text": "done"}
        finally:
            await _cleanup_run(async_session_factory, run_id)

    @asyncio_mark
    async def test_expired_token_cannot_write_before_another_worker_claims(
        self, async_session_factory
    ):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, run_id, "worker-a")
                token = claimed.lease_token
                assert token
            async with async_session_factory() as session:
                stored = await session.get(AgentRun, run_id)
                assert stored is not None
                stored.lease_expires_at = datetime.now(timezone.utc) - timedelta(
                    seconds=1
                )
                await session.commit()
            async with async_session_factory() as session:
                with pytest.raises(rt.LeaseMismatchError):
                    await run_store.commit_checkpoint(
                        session, run_id, token, {"format_version": 1, "messages": []}
                    )
                with pytest.raises(rt.LeaseMismatchError):
                    await run_store.finish_run(
                        session, run_id, token, rt.COMPLETED
                    )
        finally:
            await _cleanup_run(async_session_factory, run_id)


class TestCheckpointAndJournal:
    @asyncio_mark
    async def test_commit_checkpoint_advances_run_and_projects_steps(
        self, async_session_factory
    ):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, run_id, "worker-a")
                token = claimed.lease_token
                assert token
                checkpoint = await run_store.commit_checkpoint(
                    session,
                    run_id,
                    token,
                    {"format_version": 1, "messages": [{"role": "user"}]},
                    format_version=1,
                    journal_kind=rt.JOURNAL_MODEL_RESPONSE,
                    journal_data={"model": "fake"},
                    steps=[
                        {
                            "step_number": 1,
                            "type": "llm_response",
                            "content": {"text": "hi"},
                            "tokens_used": 7,
                            "duration_ms": 3,
                        }
                    ],
                    iterations_used=1,
                    tokens_used=7,
                )
                assert checkpoint.sequence == 1
            async with async_session_factory() as session:
                stored = await session.get(AgentRun, run_id)
                assert stored is not None
                assert stored.checkpoint_sequence == 1
                assert stored.iterations_used == 1
                assert stored.tokens_used == 7
                steps = (
                    await session.execute(
                        select(AgentRunStep).where(AgentRunStep.run_id == run_id)
                    )
                ).scalars().all()
                assert len(steps) == 1
                assert steps[0].type == "llm_response"
                journals = (
                    await session.execute(
                        select(AgentRunJournalEntry)
                        .where(AgentRunJournalEntry.run_id == run_id)
                        .order_by(AgentRunJournalEntry.sequence)
                    )
                ).scalars().all()
                kinds = [j.kind for j in journals]
                assert rt.JOURNAL_MODEL_RESPONSE in kinds
                boundary = next(
                    j
                    for j in journals
                    if j.kind == rt.JOURNAL_MODEL_RESPONSE
                )
                assert boundary.checkpoint_sequence == 1
        finally:
            await _cleanup_run(async_session_factory, run_id)

    @asyncio_mark
    async def test_checkpoint_counters_survive_nested_lock_with_autoflush_off(
        self, async_session_factory
    ):
        """A lock refresh cannot discard this transaction's unflushed state."""
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                session.autoflush = False
                claimed = await run_store.claim_run(session, run_id, "worker-a")
                assert claimed.lease_token is not None
                await run_store.commit_checkpoint(
                    session,
                    run_id,
                    claimed.lease_token,
                    {"format_version": 1, "messages": []},
                    iterations_used=1,
                    tokens_used=10,
                )
                await run_store.commit_checkpoint(
                    session,
                    run_id,
                    claimed.lease_token,
                    {"format_version": 1, "messages": []},
                    iterations_used=2,
                    tokens_used=20,
                )
            async with async_session_factory() as session:
                stored = await session.get(AgentRun, run_id)
                assert stored is not None
                assert stored.checkpoint_sequence == 2
                assert stored.iterations_used == 2
                assert stored.tokens_used == 20
                checkpoints = (
                    await session.execute(
                        select(AgentRunCheckpoint)
                        .where(AgentRunCheckpoint.run_id == run_id)
                        .order_by(AgentRunCheckpoint.sequence)
                    )
                ).scalars().all()
                assert [checkpoint.sequence for checkpoint in checkpoints] == [1, 2]
        finally:
            await _cleanup_run(async_session_factory, run_id)

    @asyncio_mark
    async def test_journal_redacts_secrets(self, async_session_factory):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, run_id, "worker-a")
                entry = await run_store.append_journal(
                    session,
                    run_id,
                    claimed.lease_token,
                    rt.JOURNAL_TOOL_CALL,
                    {"arguments": {"api_key": "super-secret-value"}},
                    secrets={"super-secret-value"},
                )
                assert "super-secret-value" not in str(entry.data)
        finally:
            await _cleanup_run(async_session_factory, run_id)


class TestWaitingAndFinish:
    @asyncio_mark
    async def test_wait_wake_round_trip(self, async_session_factory):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, run_id, "worker-a")
                waiting = await run_store.transition_waiting(
                    session,
                    run_id,
                    claimed.lease_token,
                    rt.WAITING_CHILD,
                    journal_data={"child_run_id": "child-1"},
                )
                assert waiting.status == rt.WAITING_CHILD
                assert waiting.lease_token is None
            # A woken run is claimable again without a replacement run.
            async with async_session_factory() as session:
                woken = await run_store.wake_run(
                    session, run_id, reason="child completed"
                )
                assert woken.status == rt.RUNNING
            async with async_session_factory() as session:
                reclaimed = await run_store.claim_run(session, run_id, "worker-b")
                assert reclaimed.id == run_id
        finally:
            await _cleanup_run(async_session_factory, run_id)


    @asyncio_mark
    async def test_wake_from_running_is_rejected(self, async_session_factory):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                await run_store.claim_run(session, run_id, "worker-a")
            async with async_session_factory() as session:
                with pytest.raises(rt.InvalidTransitionError):
                    await run_store.wake_run(session, run_id, reason="bogus")
        finally:
            await _cleanup_run(async_session_factory, run_id)


class TestActiveRuntimeAccounting:
    @asyncio_mark
    async def test_waiting_period_is_excluded_between_attempts(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            run = await _create_queued_run(session)
            run_id = run.id
            start = datetime.now(timezone.utc)
            session.add_all(
                [
                    AgentRunJournalEntry(
                        run_id=run_id,
                        sequence=1,
                        kind=rt.JOURNAL_RESUME,
                        data={"attempt": 1},
                        created_at=start,
                    ),
                    AgentRunJournalEntry(
                        run_id=run_id,
                        sequence=2,
                        kind=rt.JOURNAL_WAIT,
                        data={"target": rt.SLEEPING},
                        created_at=start + timedelta(seconds=3),
                    ),
                    AgentRunJournalEntry(
                        run_id=run_id,
                        sequence=3,
                        kind=rt.JOURNAL_RESUME,
                        data={"attempt": 2},
                        created_at=start + timedelta(hours=2),
                    ),
                ]
            )
            await session.commit()
        try:
            async with async_session_factory() as session:
                assert await active_seconds_used(session, run_id, 2) == pytest.approx(3)
        finally:
            await _cleanup_run(async_session_factory, run_id)

    @asyncio_mark
    async def test_crash_downtime_is_capped_at_prior_lease_expiration(
        self, async_session_factory
    ):
        async with async_session_factory() as session:
            run = await _create_queued_run(session)
            run_id = run.id
            start = datetime.now(timezone.utc)
            lease_end = start + timedelta(seconds=9)
            session.add_all(
                [
                    AgentRunJournalEntry(
                        run_id=run_id,
                        sequence=1,
                        kind=rt.JOURNAL_RESUME,
                        data={"attempt": 1},
                        created_at=start,
                    ),
                    # The cluster was down for a day before attempt two
                    # reclaimed this run. Only the original lease window was
                    # active work; the rest is not chargeable timeout.
                    AgentRunJournalEntry(
                        run_id=run_id,
                        sequence=2,
                        kind=rt.JOURNAL_LEASE_RECOVERY,
                        data={
                            "attempt": 2,
                            "prior_lease_expires_at": lease_end.isoformat(),
                        },
                        created_at=start + timedelta(days=1),
                    ),
                ]
            )
            await session.commit()
        try:
            async with async_session_factory() as session:
                assert await active_seconds_used(session, run_id, 2) == pytest.approx(9)
        finally:
            await _cleanup_run(async_session_factory, run_id)

    @asyncio_mark
    async def test_finish_marks_completion_pending_and_terminalizes(
        self, async_session_factory
    ):
        async with async_session_factory() as setup:
            run = await _create_queued_run(setup)
            run_id = run.id
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, run_id, "worker-a")
                finished = await run_store.finish_run(
                    session,
                    run_id,
                    claimed.lease_token,
                    rt.COMPLETED,
                    output={"text": "done"},
                    iterations_used=2,
                    tokens_used=11,
                )
                assert finished.status == rt.COMPLETED
                assert finished.completion_event_pending_at is not None
                assert finished.completed_at is not None
            async with async_session_factory() as session:
                with pytest.raises(rt.InvalidTransitionError):
                    await run_store.wake_run(session, run_id, reason="too late")
                latest = await run_store.latest_checkpoint(session, run_id)
                assert latest is None
        finally:
            await _cleanup_run(async_session_factory, run_id)
