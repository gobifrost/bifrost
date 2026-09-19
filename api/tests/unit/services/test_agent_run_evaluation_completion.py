"""Consumer fast path: committed synthetic roots reach apply_synthetic_terminal.

Covers the ``process_message`` finally-block wiring plus the helper's
eligibility rules. The scheduler reconciliation stays the crash-recovery
owner; these tests prove the consumer only accelerates eligible roots and
never disturbs the committed terminal outcome.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.agents import Agent

from src.jobs.consumers.agent_run import (
    AgentRunConsumer,
    _maybe_apply_synthetic_terminal,
)
from src.models.orm.agent_runs import AgentRun
from src.services.agent_evaluations.runner import build_synthetic_correlation


class FakeRedisCtx:
    """Async context manager that yields a mock redis client."""

    def __init__(self, redis_mock):
        self._redis = redis_mock

    async def __aenter__(self):
        return self._redis

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def consumer():
    with (
        patch("src.jobs.consumers.agent_run.get_settings") as mock_settings,
        patch("src.jobs.consumers.agent_run.get_session_factory"),
        patch("src.jobs.consumers.agent_run.BaseConsumer.__init__", return_value=None),
    ):
        mock_settings.return_value = MagicMock(max_concurrency=2)
        yield AgentRunConsumer()


@pytest.fixture(autouse=True)
async def clean_committed_fixtures(db_session, seed_agent):
    # These tests commit so the consumer's independent session can see rows.
    # The shared flush-only seed fixture cannot roll those commits back.
    agent_id = seed_agent.id
    yield
    if not db_session.is_active:
        await db_session.rollback()
    await db_session.execute(delete(AgentRun).where(AgentRun.agent_id == agent_id))
    await db_session.execute(delete(Agent).where(Agent.id == agent_id))
    await db_session.commit()


def _synthetic_snapshot() -> dict:
    return {
        "format_version": 1,
        "system_prompt": "Pinned synthetic prompt.",
        "model": {"profile_id": None, "provider": "test", "model": "test"},
        "tools": [],
        "delegated_agents": [],
        "system_tools": [],
        "knowledge_sources": [],
        "limits": {},
        "evaluation": {
            "mode": "evaluation_synthetic",
            "evaluation_only": True,
        },
    }


def _evaluation_ids() -> tuple[UUID, UUID, UUID]:
    return uuid4(), uuid4(), uuid4()


async def _insert_run(db_session, **kwargs) -> AgentRun:
    run = AgentRun(
        id=kwargs.pop("id", uuid4()),
        trigger_type=kwargs.pop("trigger_type", "evaluation_synthetic"),
        status=kwargs.pop("status", "queued"),
        input=kwargs.pop("input", {"task": "synthetic"}),
        iterations_used=0,
        tokens_used=0,
        created_at=datetime.now(timezone.utc),
        **kwargs,
    )
    db_session.add(run)
    await db_session.commit()
    return run


async def _insert_synthetic_root(db_session, seed_agent, **overrides) -> tuple[AgentRun, dict]:
    suite_id, case_id, execution_id = _evaluation_ids()
    correlation = build_synthetic_correlation(
        suite_id=suite_id,
        case_id=case_id,
        execution_id=execution_id,
        side=overrides.pop("side", "candidate"),
        repetition_index=overrides.pop("repetition_index", 0),
    )
    run_id = overrides.pop("id", uuid4())
    run = await _insert_run(
        db_session,
        id=run_id,
        agent_id=seed_agent.id,
        execution_snapshot=_synthetic_snapshot(),
        correlation=correlation,
        root_run_id=run_id,
        **overrides,
    )
    return run, correlation


async def _insert_simulation_session(db_session, run_id: UUID) -> None:
    from src.models.orm.agent_evaluations import AgentSimulationSession

    db_session.add(
        AgentSimulationSession(
            id=uuid4(),
            case_version=1,
            root_run_id=run_id,
            state={},
            fixture={},
            tool_schemas={},
        )
    )
    await db_session.commit()


async def _load_run(async_session_factory, run_id: UUID) -> AgentRun:
    async with async_session_factory() as db:
        return await db.get(AgentRun, run_id)


class _CompletingExecutor:
    """Minimal executor stub: records kwargs, returns a completed result."""

    seen_kwargs: dict = {}

    def __init__(self, session_factory, redis_client):
        self._session_factory = session_factory

    async def run(self, **kwargs):
        type(self).seen_kwargs = kwargs
        return {
            "output": {"text": "synthetic output"},
            "iterations_used": 1,
            "tokens_used": 5,
            "status": "completed",
            "llm_model": "test-model",
        }

    async def flush_to_db(self, db):
        return None

    async def recover_durable_usage(self, *, agent, run_id):
        return None


class _FailingExecutor(_CompletingExecutor):
    async def run(self, **kwargs):
        raise RuntimeError("synthetic executor exploded")


def _redis_blank() -> AsyncMock:
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    return redis_mock


def _patch_run_harness(redis_mock, executor_cls=_CompletingExecutor):
    return (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            executor_cls,
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()),
        patch("src.services.execution.run_summarizer.enqueue_summarize", AsyncMock()),
    )


# ── helper eligibility ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_helper_dispatches_eligible_terminal_root(
    db_session, async_session_factory, seed_agent
):
    run, correlation = await _insert_synthetic_root(
        db_session, seed_agent, status="completed"
    )
    applied = AsyncMock(return_value=True)
    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
    ):
        await _maybe_apply_synthetic_terminal(
            async_session_factory, str(run.id), True
        )
    applied.assert_awaited_once()
    call = applied.await_args
    assert call.args[0] == UUID(correlation["evaluation_execution_id"])
    assert call.kwargs["side"] == "candidate"
    assert call.kwargs["case_id"] == UUID(correlation["evaluation_case_id"])
    assert call.kwargs["repetition_index"] == 0
    assert call.kwargs["run_id"] == run.id
    assert call.kwargs["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["queued", "running", "cancelling", "waiting_child", "sleeping", "recovery_required"],
)
async def test_helper_skips_non_terminal_rows(
    db_session, async_session_factory, seed_agent, status
):
    run, _ = await _insert_synthetic_root(db_session, seed_agent, status=status)
    applied = AsyncMock(return_value=True)
    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
    ):
        await _maybe_apply_synthetic_terminal(
            async_session_factory, str(run.id), True
        )
    applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_helper_skips_production_trigger_and_mode(
    db_session, async_session_factory, seed_agent
):
    run = await _insert_run(
        db_session,
        agent_id=seed_agent.id,
        trigger_type="manual",
        status="completed",
        execution_snapshot=_synthetic_snapshot(),
        correlation={"evaluation_mode": "production"},
        root_run_id=None,
    )
    applied = AsyncMock(return_value=True)
    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
    ):
        await _maybe_apply_synthetic_terminal(
            async_session_factory, str(run.id), None
        )
    applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_helper_skips_designer_run_without_execution_correlation(
    db_session, async_session_factory, seed_agent
):
    run_id = uuid4()
    run = await _insert_run(
        db_session,
        id=run_id,
        agent_id=seed_agent.id,
        trigger_type="evaluation_synthetic",
        status="completed",
        execution_snapshot=_synthetic_snapshot(),
        correlation={
            "evaluation_mode": "evaluation_synthetic",
            "evaluation_designer": True,
        },
        root_run_id=run_id,
    )
    applied = AsyncMock(return_value=True)
    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
    ):
        await _maybe_apply_synthetic_terminal(
            async_session_factory, str(run.id), True
        )
    applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_helper_skips_delegated_child(
    db_session, async_session_factory, seed_agent
):
    parent, _ = await _insert_synthetic_root(db_session, seed_agent, status="completed")
    run, _ = await _insert_synthetic_root(
        db_session, seed_agent, status="completed", parent_run_id=parent.id
    )
    applied = AsyncMock(return_value=True)
    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
    ):
        await _maybe_apply_synthetic_terminal(
            async_session_factory, str(run.id), None
        )
    applied.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.pop("evaluation_execution_id"),
        lambda c: c.pop("evaluation_case_id"),
        lambda c: c.update({"evaluation_side": "middle"}),
        lambda c: c.update({"evaluation_repetition": "not-a-number"}),
        lambda c: c.update({"evaluation_mode": "production"}),
        lambda c: c.update({"evaluation_repetition": 0.5}),
        lambda c: c.update({"evaluation_repetition": -1}),
        lambda c: c.update({"evaluation_repetition": True}),
    ],
    ids=("no-execution", "no-case", "bad-side", "bad-repetition", "prod-mode", "fraction", "negative", "boolean"),
)
async def test_helper_skips_invalid_correlation(
    db_session, async_session_factory, seed_agent, mutate
):
    run, correlation = await _insert_synthetic_root(
        db_session, seed_agent, status="completed"
    )
    changed = dict(correlation)
    mutate(changed)
    run.correlation = changed
    await db_session.commit()
    applied = AsyncMock(return_value=True)
    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
    ):
        await _maybe_apply_synthetic_terminal(
            async_session_factory, str(run.id), True
        )
    applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_helper_skips_missing_row_and_bad_id(async_session_factory):
    applied = AsyncMock(return_value=True)
    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
    ):
        await _maybe_apply_synthetic_terminal(
            async_session_factory, str(uuid4()), None
        )
        await _maybe_apply_synthetic_terminal(
            async_session_factory, "not-a-uuid", None
        )
        await _maybe_apply_synthetic_terminal(async_session_factory, None, None)
    applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_helper_hint_false_skips_session_factory():
    def _exploding_factory():
        raise AssertionError("production path must not open a session")

    applied = AsyncMock(return_value=True)
    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
    ):
        await _maybe_apply_synthetic_terminal(
            _exploding_factory, str(uuid4()), False
        )
    applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_helper_error_isolation_preserves_outcome(
    db_session, async_session_factory, seed_agent
):
    run, _ = await _insert_synthetic_root(
        db_session, seed_agent, status="failed"
    )
    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal",
        AsyncMock(side_effect=RuntimeError("evaluation store down")),
    ):
        # Must not raise: the committed terminal outcome stands and the
        # scheduler reconciliation remains the recovery owner.
        await _maybe_apply_synthetic_terminal(
            async_session_factory, str(run.id), True
        )
    refreshed = await _load_run(async_session_factory, run.id)
    assert refreshed.status == "failed"


@pytest.mark.asyncio
async def test_helper_dispatches_after_session_release(
    db_session, async_session_factory, seed_agent
):
    """The handler runs only after the fresh read session is closed."""
    run, _ = await _insert_synthetic_root(
        db_session, seed_agent, status="completed"
    )
    events: list[str] = []
    real_factory = async_session_factory

    def _tracking_factory():
        ctx = real_factory()

        class _TrackingCtx:
            async def __aenter__(self):
                self._db = await ctx.__aenter__()
                return self._db

            async def __aexit__(self, *args):
                result = await ctx.__aexit__(*args)
                events.append("session-closed")
                return result

        return _TrackingCtx()

    async def _recording_apply(*args, **kwargs):
        events.append("apply-called")
        return True

    with patch(
        "src.jobs.platform.agent_evaluation.apply_synthetic_terminal",
        _recording_apply,
    ):
        await _maybe_apply_synthetic_terminal(
            _tracking_factory, str(run.id), True
        )
    assert events == ["session-closed", "apply-called"]


# ── process_message wiring ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_message_applies_committed_synthetic_completion(
    consumer, db_session, async_session_factory, seed_agent
):
    run, correlation = await _insert_synthetic_root(db_session, seed_agent)
    await _insert_simulation_session(db_session, run.id)
    consumer._session_factory = async_session_factory
    redis_mock = _redis_blank()
    applied = AsyncMock(return_value=True)
    patches = _patch_run_harness(redis_mock)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch(
            "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
        ),
    ):
        await consumer.process_message({"run_id": str(run.id)})
    applied.assert_awaited_once()
    assert applied.await_args.kwargs["run_id"] == run.id
    assert applied.await_args.kwargs["status"] == "completed"
    refreshed = await _load_run(async_session_factory, run.id)
    assert refreshed.status == "completed"


@pytest.mark.asyncio
async def test_process_message_skips_production_run(
    consumer, db_session, async_session_factory, seed_agent
):
    run = await _insert_run(
        db_session,
        agent_id=seed_agent.id,
        trigger_type="manual",
        execution_snapshot={
            "format_version": 1,
            "system_prompt": "Pinned prompt.",
            "model": {"profile_id": None, "provider": "test", "model": "test"},
            "tools": [],
            "delegated_agents": [],
            "system_tools": [],
            "limits": {},
        },
        correlation={"evaluation_mode": "production"},
    )
    consumer._session_factory = async_session_factory
    redis_mock = _redis_blank()
    applied = AsyncMock(return_value=True)
    patches = _patch_run_harness(redis_mock)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch(
            "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
        ),
    ):
        await consumer.process_message({"run_id": str(run.id)})
    applied.assert_not_awaited()
    refreshed = await _load_run(async_session_factory, run.id)
    assert refreshed.status == "completed"


@pytest.mark.asyncio
async def test_process_message_duplicate_terminal_nudge_still_applies(
    consumer, db_session, async_session_factory, seed_agent
):
    """A redelivered nudge for an already-terminal row takes the early
    return path; the finally block still closes the evaluation."""
    run, _ = await _insert_synthetic_root(
        db_session, seed_agent, status="completed"
    )
    consumer._session_factory = async_session_factory
    redis_mock = _redis_blank()
    applied = AsyncMock(return_value=True)
    with (
        patch(
            "src.jobs.consumers.agent_run.get_redis",
            return_value=FakeRedisCtx(redis_mock),
        ),
        patch(
            "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
        ),
    ):
        await consumer.process_message({"run_id": str(run.id)})
    applied.assert_awaited_once()
    assert applied.await_args.kwargs["status"] == "completed"


@pytest.mark.asyncio
async def test_process_message_precancelled_synthetic_applies(
    consumer, db_session, async_session_factory, seed_agent
):
    run, _ = await _insert_synthetic_root(db_session, seed_agent, status="queued")
    consumer._session_factory = async_session_factory
    redis_mock = AsyncMock()
    redis_mock.get.return_value = "1"
    applied = AsyncMock(return_value=True)
    with (
        patch(
            "src.jobs.consumers.agent_run.get_redis",
            return_value=FakeRedisCtx(redis_mock),
        ),
        patch(
            "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
        ),
    ):
        await consumer.process_message({"run_id": str(run.id)})
    applied.assert_awaited_once()
    assert applied.await_args.kwargs["status"] == "cancelled"
    refreshed = await _load_run(async_session_factory, run.id)
    assert refreshed.status == "cancelled"


@pytest.mark.asyncio
async def test_process_message_error_path_applies_and_preserves_failure(
    consumer, db_session, async_session_factory, seed_agent
):
    run, _ = await _insert_synthetic_root(db_session, seed_agent)
    await _insert_simulation_session(db_session, run.id)
    consumer._session_factory = async_session_factory
    redis_mock = _redis_blank()
    applied = AsyncMock(return_value=True)
    patches = _patch_run_harness(redis_mock, executor_cls=_FailingExecutor)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patch(
            "src.jobs.platform.agent_evaluation.apply_synthetic_terminal", applied
        ),
    ):
        await consumer.process_message({"run_id": str(run.id)})
    applied.assert_awaited_once()
    assert applied.await_args.kwargs["status"] == "failed"
    refreshed = await _load_run(async_session_factory, run.id)
    assert refreshed.status == "failed"
    assert "synthetic executor exploded" in (refreshed.error or "")


@pytest.mark.asyncio
async def test_process_message_apply_failure_preserves_terminal_outcome(
    consumer, db_session, async_session_factory, seed_agent
):
    run, _ = await _insert_synthetic_root(db_session, seed_agent)
    await _insert_simulation_session(db_session, run.id)
    consumer._session_factory = async_session_factory
    redis_mock = _redis_blank()
    patches = _patch_run_harness(redis_mock)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch(
            "src.jobs.platform.agent_evaluation.apply_synthetic_terminal",
            AsyncMock(side_effect=RuntimeError("evaluation store down")),
        ),
    ):
        # A throwing handler must not fail the message: the committed
        # terminal outcome stands and reconciliation recovers later.
        await consumer.process_message({"run_id": str(run.id)})
    refreshed = await _load_run(async_session_factory, run.id)
    assert refreshed.status == "completed"
    assert refreshed.output == {"text": "synthetic output"}
