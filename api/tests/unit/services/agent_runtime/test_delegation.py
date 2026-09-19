"""Durable delegation primitives: intents, wake-once, cascade cancel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.services.agent_runtime import run_store
from src.services.agent_runtime.resume import prepare_resume, restore_pending_wait
from src.services.agent_runtime.delegation import (
    cascade_cancel,
    child_result_text,
    collect_deferred_results,
    deferred_tool_call_ids,
    delegation_child_id,
    find_delegation_intent,
    wake_parent_for_child,
)

asyncio_mark = pytest.mark.asyncio


async def _cleanup(factory, *run_ids: UUID):
    async with factory() as session:
        for run_id in run_ids:
            await session.execute(
                delete(AgentRunJournalEntry).where(
                    AgentRunJournalEntry.run_id == run_id
                )
            )
            await session.execute(
                delete(AgentRun).where(AgentRun.id == run_id)
            )
        await session.commit()


async def _intent(factory, parent_id, tool_call_id, child_id):
    async with factory() as session:
        session.add(
            AgentRunJournalEntry(
                run_id=parent_id,
                sequence=1,
                kind="delegation",
                data={
                    "tool_call_id": tool_call_id,
                    "child_run_id": str(child_id),
                    "target_agent_name": "child",
                    "task": "go",
                },
            )
        )
        await session.commit()


class TestDelegationIds:
    def test_child_id_deterministic(self):
        parent = uuid4()
        assert delegation_child_id(parent, "call-1") == delegation_child_id(
            parent, "call-1"
        )
        assert delegation_child_id(parent, "call-1") != delegation_child_id(
            parent, "call-2"
        )


class TestWakeOnce:
    @asyncio_mark
    async def test_first_completion_wakes_second_is_noop(
        self, async_session_factory
    ):
        parent_id = uuid4()
        child_id = uuid4()
        async with async_session_factory() as session:
            session.add(
                AgentRun(
                    id=parent_id,
                    trigger_type="test",
                    status="waiting_child",
                )
            )
            session.add(
                AgentRun(
                    id=child_id,
                    trigger_type="delegation",
                    status="completed",
                    output={"text": "child says hi"},
                    parent_run_id=parent_id,
                )
            )
            await session.commit()
        await _intent(async_session_factory, parent_id, "call-1", child_id)
        try:
            with patch(
                "src.jobs.rabbitmq.publish_message", new=AsyncMock()
            ) as nudges:
                assert await wake_parent_for_child(
                    async_session_factory, child_id
                ) is True
                assert await wake_parent_for_child(
                    async_session_factory, child_id
                ) is False
            nudges.assert_awaited_once_with(
                "agent-runs", {"run_id": str(parent_id)}
            )
            async with async_session_factory() as session:
                parent = await session.get(AgentRun, parent_id)
                assert parent is not None
                assert parent.status == "running"
                results = await collect_deferred_results(session, parent_id)
                assert results == {"call-1": "child says hi"}
                ids = await deferred_tool_call_ids(session, parent_id)
                assert ids == {"call-1"}
                intent = await find_delegation_intent(
                    session, parent_id, "call-1"
                )
                assert intent is not None
        finally:
            await _cleanup(async_session_factory, parent_id, child_id)

    @asyncio_mark
    async def test_wake_ignores_non_waiting_parent(self, async_session_factory):
        parent_id = uuid4()
        child_id = uuid4()
        async with async_session_factory() as session:
            session.add(
                AgentRun(
                    id=parent_id, trigger_type="test", status="running"
                )
            )
            session.add(
                AgentRun(
                    id=child_id,
                    trigger_type="delegation",
                    status="completed",
                    output={"text": "hi"},
                    parent_run_id=parent_id,
                )
            )
            await session.commit()
        await _intent(async_session_factory, parent_id, "call-1", child_id)
        try:
            with patch(
                "src.jobs.rabbitmq.publish_message", new=AsyncMock()
            ) as nudges:
                assert await wake_parent_for_child(
                    async_session_factory, child_id
                ) is False
            nudges.assert_not_called()
        finally:
            await _cleanup(async_session_factory, parent_id, child_id)

    @asyncio_mark
    async def test_child_result_text_covers_outcomes(self):
        completed = AgentRun(
            trigger_type="delegation", status="completed", output={"text": "ok"}
        )
        assert child_result_text(completed) == "ok"
        failed = AgentRun(
            trigger_type="delegation", status="failed", error="boom"
        )
        assert child_result_text(failed) == "Error: boom"

    @asyncio_mark
    async def test_recovery_replays_child_completed_before_parent_reparks(
        self, async_session_factory
    ):
        """A completion in the collect-before-park window must not strand parent."""
        parent_id = uuid4()
        child_id = uuid4()
        async with async_session_factory() as session:
            session.add_all(
                [
                    AgentRun(id=parent_id, trigger_type="test", status="queued"),
                    AgentRun(
                        id=child_id,
                        trigger_type="delegation",
                        status="completed",
                        output={"text": "done"},
                        parent_run_id=parent_id,
                    ),
                ]
            )
            await session.commit()
        await _intent(async_session_factory, parent_id, "call-1", child_id)
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, parent_id, "worker")
                assert claimed.lease_token is not None
                restored = await restore_pending_wait(
                    async_session_factory,
                    parent_id,
                    claimed.lease_token,
                    {"call-1"},
                )
                assert restored is None
            plan, _, unrecoverable = await prepare_resume(
                async_session_factory, parent_id, claimed.lease_token
            )
            assert unrecoverable is None
            assert plan is not None
            assert plan.deferred_pending == set()
            assert plan.deferred_results == {"call-1": "done"}
            async with async_session_factory() as session:
                parent = await session.get(AgentRun, parent_id)
                assert parent is not None
                assert parent.status == "running"
        finally:
            await _cleanup(async_session_factory, parent_id, child_id)


class TestCascadeCancel:
    @asyncio_mark
    async def test_cascade_touches_only_unfinished_descendants(
        self, async_session_factory
    ):
        root_id = uuid4()
        now = datetime.now(timezone.utc)
        queued_id = uuid4()
        running_id = uuid4()
        waiting_id = uuid4()
        done_id = uuid4()
        async with async_session_factory() as session:
            session.add(
                AgentRun(
                    id=root_id,
                    trigger_type="test",
                    status="waiting_child",
                    root_run_id=root_id,
                )
            )
            session.add(
                AgentRun(
                    id=queued_id,
                    trigger_type="delegation",
                    status="queued",
                    parent_run_id=root_id,
                    root_run_id=root_id,
                )
            )
            session.add(
                AgentRun(
                    id=running_id,
                    trigger_type="delegation",
                    status="running",
                    parent_run_id=root_id,
                    root_run_id=root_id,
                    lease_owner="w",
                    lease_token="t",
                    lease_expires_at=now + timedelta(minutes=5),
                )
            )
            session.add(
                AgentRun(
                    id=waiting_id,
                    trigger_type="delegation",
                    status="waiting_child",
                    parent_run_id=root_id,
                    root_run_id=root_id,
                )
            )
            session.add(
                AgentRun(
                    id=done_id,
                    trigger_type="delegation",
                    status="completed",
                    parent_run_id=root_id,
                    root_run_id=root_id,
                )
            )
            await session.commit()
        redis_mock = AsyncMock()
        try:
            touched = await cascade_cancel(
                async_session_factory, redis_mock, root_id
            )
            assert touched == 3
            async with async_session_factory() as session:
                queued = await session.get(AgentRun, queued_id)
                running = await session.get(AgentRun, running_id)
                waiting = await session.get(AgentRun, waiting_id)
                done = await session.get(AgentRun, done_id)
                root = await session.get(AgentRun, root_id)
                assert queued is not None and queued.status == "cancelled"
                assert queued.completion_event_pending_at is not None
                assert running is not None and running.status == "cancelling"
                assert waiting is not None and waiting.status == "cancelled"
                assert waiting.completion_event_pending_at is not None
                assert done is not None and done.status == "completed"
                assert root is not None and root.status == "waiting_child"
            redis_mock.set.assert_awaited_once()
            flag = redis_mock.set.await_args.args[0]
            assert str(running_id) in flag
        finally:
            await _cleanup(
                async_session_factory,
                root_id,
                queued_id,
                running_id,
                waiting_id,
                done_id,
            )

    @asyncio_mark
    async def test_cancelling_child_only_touches_its_descendants(
        self, async_session_factory
    ):
        root_id = uuid4()
        target_child_id = uuid4()
        grandchild_id = uuid4()
        sibling_id = uuid4()
        async with async_session_factory() as session:
            session.add_all(
                [
                    AgentRun(id=root_id, trigger_type="test", status="running"),
                    AgentRun(
                        id=target_child_id,
                        trigger_type="delegation",
                        status="running",
                        parent_run_id=root_id,
                        root_run_id=root_id,
                    ),
                    AgentRun(
                        id=grandchild_id,
                        trigger_type="delegation",
                        status="queued",
                        parent_run_id=target_child_id,
                        root_run_id=root_id,
                    ),
                    AgentRun(
                        id=sibling_id,
                        trigger_type="delegation",
                        status="queued",
                        parent_run_id=root_id,
                        root_run_id=root_id,
                    ),
                ]
            )
            await session.commit()
        redis_mock = AsyncMock()
        try:
            touched = await cascade_cancel(
                async_session_factory, redis_mock, target_child_id
            )
            assert touched == 1
            async with async_session_factory() as session:
                root = await session.get(AgentRun, root_id)
                target = await session.get(AgentRun, target_child_id)
                grandchild = await session.get(AgentRun, grandchild_id)
                sibling = await session.get(AgentRun, sibling_id)
                assert root is not None and root.status == "running"
                assert target is not None and target.status == "running"
                assert grandchild is not None and grandchild.status == "cancelled"
                assert sibling is not None and sibling.status == "queued"
        finally:
            await _cleanup(
                async_session_factory,
                root_id,
                target_child_id,
                grandchild_id,
                sibling_id,
            )


class TestRecoveryRequiredPersistsEvidence:
    @asyncio_mark
    async def test_mark_recovery_required_releases_lease(
        self, async_session_factory
    ):
        run_id = uuid4()
        async with async_session_factory() as session:
            session.add(AgentRun(id=run_id, trigger_type="test", status="queued"))
            await session.commit()
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, run_id, "worker")
                token = claimed.lease_token
                assert token
                recovered = await run_store.mark_recovery_required(
                    session,
                    run_id,
                    token,
                    reason="uncertain write",
                    evidence={"tool": "send_email"},
                )
                assert recovered.status == "recovery_required"
                assert recovered.lease_token is None
        finally:
            await _cleanup(async_session_factory, run_id)
