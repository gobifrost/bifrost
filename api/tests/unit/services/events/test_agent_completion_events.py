"""Durable completion events: topics, payloads, and the outbox scanner."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.jobs.schedulers.agent_completion_events import (
    publish_pending_agent_completions,
)
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.services.events import builtins
from src.services.events.registry import CURATED_TOPICS

asyncio_mark = pytest.mark.asyncio


def _run(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "trigger_type": "event",
        "status": "completed",
        "input": {"ticket_id": 7},
        "output": {"text": "done"},
        "iterations_used": 3,
        "tokens_used": 120,
        "duration_ms": 4500,
        "correlation": {"ticket_id": "7", "kind": "ticket"},
        "created_at": now,
        "started_at": now,
        "completed_at": now,
    }
    base.update(overrides)
    return AgentRun(**base)


class TestTopicMapping:
    def test_every_terminal_status_maps(self):
        assert builtins.agent_completion_topic("completed") == "agent.completed"
        assert builtins.agent_completion_topic("failed") == "agent.failed"
        assert builtins.agent_completion_topic("cancelled") == "agent.cancelled"
        assert builtins.agent_completion_topic("timeout") == "agent.timed_out"
        assert builtins.agent_completion_topic("contract_failed") == (
            "agent.failed"
        )
        assert builtins.agent_completion_topic("budget_exceeded") == "agent.failed"
        assert builtins.agent_completion_topic("weird") == "agent.failed"

    def test_registry_lists_completion_topics(self):
        topics = {entry["topic"] for entry in CURATED_TOPICS}
        assert {
            "agent.completed",
            "agent.failed",
            "agent.cancelled",
            "agent.timed_out",
            "agent.failed",
            "workflow.completed",
        } <= topics


class TestPayloadShape:
    def test_success_carries_metadata_without_output_or_error(self):
        topic, body = builtins.agent_completion_payload(_run())
        assert topic == "agent.completed"
        assert body["run"]["status"] == "completed"
        assert "output" not in body
        assert "error" not in body
        assert body["correlation"] == {"ticket_id": "7", "kind": "ticket"}
        assert body["counters"]["iterations_used"] == 3
        assert body["run"]["attempt"] == 0
        assert body["contract"] == {"valid": None}

    def test_failure_carries_structured_error_not_output(self):
        topic, body = builtins.agent_completion_payload(
            _run(status="budget_exceeded", error="ran out of budget")
        )
        assert topic == "agent.failed"
        assert body["run"]["status"] == "budget_exceeded"
        assert body["error"]["message"] == "Agent run budget_exceeded."
        assert body["error"]["type"] == "budget_exceeded"
        assert "output" not in body

    def test_event_excludes_output_and_validation_values(self):
        _, body = builtins.agent_completion_payload(
            _run(output={"secret": "do-not-publish"},
                 contract_errors=["do-not-publish"], error="do-not-publish")
        )
        assert "do-not-publish" not in str(body)
        assert "output" not in body

    def test_correlation_is_bounded_and_secret_keys_are_removed(self):
        _, body = builtins.agent_completion_payload(_run(correlation={
            "ticket_id": "7", "api_key": "do-not-publish",
            "authorization": "do-not-publish", "nested": {"value": "do-not-publish"},
            "oversized": "x" * 10000,
        }))
        assert body["correlation"]["ticket_id"] == "7"
        assert "do-not-publish" not in str(body)
        assert len(str(body["correlation"])) < 2000

    def test_synthetic_completion_cannot_match_production_topic(self):
        topic, body = builtins.agent_completion_payload(_run(
            trigger_type="evaluation_synthetic",
            execution_snapshot={"evaluation": {"mode": "evaluation_synthetic", "evaluation_only": True}},
        ))
        assert topic == "agent.evaluation.completed"
        assert body["run"]["trigger_type"] == "evaluation_synthetic"

    def test_payload_carries_filterable_keys(self):
        agent_id = uuid4()
        root_id = uuid4()
        _, body = builtins.agent_completion_payload(
            _run(agent_id=agent_id, root_run_id=root_id)
        )
        assert body["run"]["agent_id"] == str(agent_id)
        assert body["run"]["root_run_id"] == str(root_id)
        assert body["correlation"]["ticket_id"] == "7"

    @pytest.mark.asyncio
    async def test_workflow_completed_emitter(self):
        with patch(
            "src.services.events.builtins._emit", new=AsyncMock()
        ) as emit:
            await builtins.emit_workflow_completed_event(
                workflow_id=uuid4(),
                workflow_name="sync",
                execution_id=uuid4(),
                organization_id=None,
                user_id=None,
                user_email=None,
                user_name=None,
                status="Success",
                duration_ms=12,
            )
        assert emit.await_args.args[0] == "workflow.completed"
        assert emit.await_args.args[1]["execution"]["status"] == "Success"


class TestOutboxScanner:
    async def _seed_pending(self, async_session_factory, **overrides):
        async with async_session_factory() as session:
            run = _run(**overrides)
            run.completion_event_pending_at = (
                run.completion_event_pending_at or datetime.now(timezone.utc)
            )
            session.add(run)
            await session.commit()
            return run.id

    async def _load(self, async_session_factory, run_id):
        async with async_session_factory() as session:
            return await session.get(AgentRun, run_id)

    async def _cleanup(self, async_session_factory, run_id):
        async with async_session_factory() as session:
            await session.execute(
                delete(AgentRun).where(AgentRun.id == run_id)
            )
            await session.commit()

    @asyncio_mark
    async def test_emits_once_then_skips(self, async_session_factory):
        run_id = await self._seed_pending(async_session_factory)
        try:
            with patch(
                "src.services.events.emit_event", new=AsyncMock()
            ) as emit:
                first = await publish_pending_agent_completions()
                second = await publish_pending_agent_completions()
            assert first["claimed"] >= 1
            assert first["emitted"] >= 1
            assert emit.await_count >= 1
            topics = [call.args[0] for call in emit.await_args_list]
            assert "agent.completed" in topics
            assert second["claimed"] == 0
            reloaded = await self._load(async_session_factory, run_id)
            assert reloaded is not None
            assert reloaded.completion_event_emitted_at is not None
            assert reloaded.completion_event_attempts == 1
        finally:
            await self._cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_failure_records_attempt_and_retries(self, async_session_factory):
        run_id = await self._seed_pending(async_session_factory)
        try:
            with patch(
                "src.services.events.emit_event",
                new=AsyncMock(side_effect=RuntimeError("broker down")),
            ):
                failed = await publish_pending_agent_completions()
            assert failed["failed"] >= 1
            reloaded = await self._load(async_session_factory, run_id)
            assert reloaded is not None
            assert reloaded.completion_event_emitted_at is None
            assert reloaded.completion_event_attempts >= 1
            assert "broker down" in (reloaded.completion_event_last_error or "")

            with patch(
                "src.services.events.emit_event", new=AsyncMock()
            ) as emit:
                recovered = await publish_pending_agent_completions()
            assert recovered["emitted"] >= 1
            assert emit.await_count >= 1
        finally:
            await self._cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_failed_completion_does_not_starve_later_runs(self, async_session_factory):
        failed_id = await self._seed_pending(
            async_session_factory,
            completion_event_pending_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        later_id = await self._seed_pending(
            async_session_factory,
            completion_event_pending_at=datetime(2001, 1, 1, tzinfo=timezone.utc),
        )

        async def emit(topic, body, **kwargs):
            if body["run"]["id"] == str(failed_id):
                raise RuntimeError("This completion cannot be delivered yet")

        try:
            with patch("src.services.events.emit_event", side_effect=emit):
                await publish_pending_agent_completions(batch_size=1)
                await publish_pending_agent_completions(batch_size=1)
            later = await self._load(async_session_factory, later_id)
            assert later.completion_event_emitted_at is not None
        finally:
            await self._cleanup(async_session_factory, failed_id)
            await self._cleanup(async_session_factory, later_id)

    @asyncio_mark
    async def test_overlapping_scanner_keeps_claim_until_emit_commits(
        self, async_session_factory
    ):
        run_id = await self._seed_pending(async_session_factory)
        emitting = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def emit(topic, body, **kwargs):
            nonlocal calls
            if body["run"]["id"] == str(run_id):
                calls += 1
                if calls == 1:
                    emitting.set()
                    await release.wait()

        task = None
        try:
            with patch("src.services.events.emit_event", side_effect=emit):
                task = asyncio.create_task(publish_pending_agent_completions())
                await asyncio.wait_for(emitting.wait(), timeout=5)
                await publish_pending_agent_completions()
                assert calls == 1
                release.set()
                await task
        finally:
            release.set()
            if task is not None:
                await task
            await self._cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_scanner_recovers_parent_wake_after_child_terminal_commit(
        self, async_session_factory
    ):
        async with async_session_factory() as db:
            parent = _run(status="waiting_child")
            db.add(parent)
            await db.flush()
            child = _run(parent_run_id=parent.id)
            child.completion_event_pending_at = datetime.now(timezone.utc)
            db.add(child)
            await db.flush()
            db.add(AgentRunJournalEntry(
                run_id=parent.id, sequence=1, kind="delegation",
                data={"child_run_id": str(child.id), "tool_call_id": "child-call"},
            ))
            await db.commit()
            parent_id, child_id = parent.id, child.id
        try:
            with (
                patch("src.services.events.emit_event", new=AsyncMock()),
                patch("src.jobs.rabbitmq.publish_message", new=AsyncMock()),
            ):
                await publish_pending_agent_completions()
            parent = await self._load(async_session_factory, parent_id)
            child = await self._load(async_session_factory, child_id)
            assert parent.status == "running"
            assert parent.lease_token is None
            assert child.completion_event_emitted_at is not None
        finally:
            await self._cleanup(async_session_factory, child_id)
            await self._cleanup(async_session_factory, parent_id)

    @asyncio_mark
    async def test_terminalization_marks_pending_in_same_transaction(
        self, async_session_factory
    ):
        from src.services.agent_runtime import run_store

        async with async_session_factory() as session:
            run = _run(status="queued")
            session.add(run)
            await session.commit()
            run_id = run.id
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, run_id, "worker")
                assert claimed.lease_token is not None
                finished = await run_store.finish_run(
                    session, run_id, claimed.lease_token, "completed"
                )
                assert finished.completion_event_pending_at is not None
                assert finished.completion_event_emitted_at is None
        finally:
            await self._cleanup(async_session_factory, run_id)
