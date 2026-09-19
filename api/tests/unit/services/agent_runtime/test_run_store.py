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
