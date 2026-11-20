"""Durable evidence checkpoints for API-orchestrated autonomous compute."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.services.agent_runtime.compute_execution import (
    agent_compute_execution,
    is_agent_compute_execution,
)
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor


def _executor() -> AutonomousAgentExecutor:
    return AutonomousAgentExecutor(MagicMock(), redis_client=MagicMock())


def _buffer_usage(executor: AutonomousAgentExecutor, run_id: UUID) -> None:
    executor._buffer_ai_usage(
        SimpleNamespace(organization_id=uuid4()),
        str(run_id),
        "test-provider",
        "test-model",
        input_tokens=3,
        output_tokens=2,
    )


def test_compute_execution_context_does_not_leak_after_checkpoint_scope():
    assert not is_agent_compute_execution()

    with agent_compute_execution(lambda *_args, **_kwargs: MagicMock()):
        assert is_agent_compute_execution()

    assert not is_agent_compute_execution()


@pytest.mark.asyncio
async def test_local_record_step_remains_buffered_without_checkpoint_write(monkeypatch):
    executor = _executor()
    persist = AsyncMock()
    monkeypatch.setattr(executor, "_persist_compute_checkpoint", persist)
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.publish_agent_run_step",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.append_agent_run_step",
        AsyncMock(),
    )

    await executor._record_step(str(uuid4()), 1, "llm_request", {"model": "test"})

    persist.assert_not_awaited()
    assert len(executor._pending_steps) == 1


@pytest.mark.asyncio
async def test_remote_record_step_persists_step_and_usage_before_returning(monkeypatch):
    executor = _executor()
    run_id = uuid4()
    _buffer_usage(executor, run_id)
    persisted: list[tuple[list[dict], list[dict]]] = []
    db = SimpleNamespace()

    async def flush(_db, steps, usages):
        persisted.append((list(steps), list(usages)))

    async def retry(_factory, unit, **_kwargs):
        await unit(db)

    monkeypatch.setattr(executor, "_flush_rows", flush)
    monkeypatch.setattr("src.core.database_retry.run_with_database_retry", retry)
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.publish_agent_run_step",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.append_agent_run_step",
        AsyncMock(),
    )
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()

    with agent_compute_execution(lambda *_args, **_kwargs: MagicMock()):
        await executor._record_step(str(run_id), 1, "llm_response", {"content": "done"})

    assert len(persisted) == 1
    steps, usages = persisted[0]
    assert [step["type"] for step in steps] == ["llm_response"]
    assert [usage["agent_run_id"] for usage in usages] == [run_id]
    db.commit.assert_awaited_once()
    assert executor._pending_steps == []
    assert executor._pending_ai_usage == []


@pytest.mark.asyncio
async def test_lost_commit_reply_retries_from_first_step_sentinel_without_duplicate_usage(monkeypatch):
    executor = _executor()
    run_id = uuid4()
    _buffer_usage(executor, run_id)
    first, retry = SimpleNamespace(), SimpleNamespace()
    flushed: list[tuple[list[dict], list[dict]]] = []

    async def flush(_db, steps, usages):
        flushed.append((list(steps), list(usages)))

    async def retry_after_lost_reply(_factory, unit, **_kwargs):
        first.scalar = AsyncMock(return_value=None)
        first.commit = AsyncMock(side_effect=ConnectionError("commit reply lost"))
        try:
            await unit(first)
        except ConnectionError:
            retry.scalar = AsyncMock(return_value=uuid4())
            retry.commit = AsyncMock()
            await unit(retry)

    monkeypatch.setattr(executor, "_flush_rows", flush)
    monkeypatch.setattr("src.core.database_retry.run_with_database_retry", retry_after_lost_reply)
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.publish_agent_run_step",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.append_agent_run_step",
        AsyncMock(),
    )

    with agent_compute_execution(lambda *_args, **_kwargs: MagicMock()):
        await executor._record_step(str(run_id), 1, "llm_response", {"content": "done"})

    assert len(flushed) == 1
    assert len(flushed[0][0]) == 1
    assert len(flushed[0][1]) == 1
    retry.commit.assert_not_awaited()
    assert executor._pending_steps == []
    assert executor._pending_ai_usage == []


@pytest.mark.asyncio
async def test_checkpoint_keeps_usage_added_while_database_write_is_awaiting(monkeypatch):
    executor = _executor()
    run_id = uuid4()
    _buffer_usage(executor, run_id)
    write_started, release_write = asyncio.Event(), asyncio.Event()
    persisted: list[tuple[list[dict], list[dict]]] = []
    db = SimpleNamespace(scalar=AsyncMock(return_value=None), commit=AsyncMock())

    async def flush(_db, steps, usages):
        persisted.append((list(steps), list(usages)))
        if len(persisted) == 1:
            write_started.set()
            await release_write.wait()

    async def retry(_factory, unit, **_kwargs):
        await unit(db)

    monkeypatch.setattr(executor, "_flush_rows", flush)
    monkeypatch.setattr("src.core.database_retry.run_with_database_retry", retry)
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.publish_agent_run_step",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.append_agent_run_step",
        AsyncMock(),
    )

    with agent_compute_execution(lambda *_args, **_kwargs: MagicMock()):
        first = asyncio.create_task(
            executor._record_step(str(run_id), 1, "llm_response", {"content": "first"})
        )
        await write_started.wait()
        _buffer_usage(executor, run_id)
        second = asyncio.create_task(
            executor._record_step(str(run_id), 2, "llm_response", {"content": "second"})
        )
        release_write.set()
        await asyncio.gather(first, second)

    assert [[step["step_number"] for step in steps] for steps, _ in persisted] == [[1], [2]]
    assert [[usage["output_tokens"] for usage in usages] for _, usages in persisted] == [[2], [2]]
    assert executor._pending_steps == []
    assert executor._pending_ai_usage == []


@pytest.mark.asyncio
async def test_cancelled_owner_drains_completed_evidence_before_cancellation(monkeypatch):
    executor = _executor()
    run_id = uuid4()
    _buffer_usage(executor, run_id)
    started, release = asyncio.Event(), asyncio.Event()
    db = SimpleNamespace(scalar=AsyncMock(return_value=None), commit=AsyncMock())

    async def flush(_db, _steps, _usages):
        started.set()
        await release.wait()

    async def retry(_factory, unit, **_kwargs):
        await unit(db)

    monkeypatch.setattr(executor, "_flush_rows", flush)
    monkeypatch.setattr("src.core.database_retry.run_with_database_retry", retry)
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.publish_agent_run_step", AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.append_agent_run_step", AsyncMock(),
    )
    with agent_compute_execution(lambda *_args, **_kwargs: MagicMock()):
        owner = asyncio.create_task(
            executor._record_step(str(run_id), 1, "llm_response", {"content": "completed"})
        )
        await started.wait()
        owner.cancel()
        # Observe delivery of cancellation while the shielded write is pending.
        await asyncio.sleep(0)
        assert not owner.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner

    db.commit.assert_awaited_once()
    assert executor._pending_steps == []
    assert executor._pending_ai_usage == []
