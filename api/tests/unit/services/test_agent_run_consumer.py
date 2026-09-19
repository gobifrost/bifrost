"""Unit tests for AgentRunConsumer error handling paths."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.jobs.consumers.agent_run import AgentRunConsumer
from src.models.contracts.agents import ChatStreamChunk
from src.models.enums import MessageRole
from src.models.orm.agents import Conversation
from src.models.orm.agent_runs import AgentRun
from src.services.execution import agent_run_service
from src.services.execution.agent_run_service import enqueue_agent_run


class FakeRedisCtx:
    """Async context manager that yields a mock redis client."""

    def __init__(self, redis_mock):
        self._redis = redis_mock

    async def __aenter__(self):
        return self._redis

    async def __aexit__(self, *args):
        pass


class FakeLateExecutor:
    """Executor stub that simulates a late terminalizer racing the consumer."""

    def __init__(self, session_factory, redis_client):
        self._session_factory = session_factory
        self._redis = redis_client

    async def run(self, *, run_id, **kwargs):
        async with self._session_factory() as db:
            run_obj = await db.get(AgentRun, UUID(run_id))
            run_obj.status = "timeout"
            run_obj.error = "scheduler terminalized the run"
            run_obj.completed_at = datetime.now(timezone.utc)
            await db.commit()

        return {
            "output": {"text": "late consumer result"},
            "iterations_used": 9,
            "tokens_used": 27,
            "status": "completed",
            "llm_model": "test-model",
        }

    async def flush_to_db(self, db):
        return None

    async def recover_durable_usage(self, *, agent, run_id):
        return None


async def _load_run(async_session_factory, run_id):
    async with async_session_factory() as db:
        result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        return result.scalar_one()


def _chat_executor_stub(
    chunks,
    *,
    usage_requests=3,
    usage_tokens=11,
    cancel_after=None,
    stall_after=None,
):
    executor = MagicMock()
    executor._save_message = AsyncMock()
    executor._active_usage = SimpleNamespace(requests=usage_requests, total_tokens=usage_tokens)
    executor._active_llm_model = None
    executor._active_failover_path = None

    def _chat(*args, **kwargs):
        async def _gen():
            for index, chunk in enumerate(chunks):
                yield chunk
                if cancel_after is not None and index == cancel_after:
                    raise asyncio.CancelledError()
                if stall_after is not None and index == stall_after:
                    await asyncio.sleep(60)

        return _gen()

    executor.chat = _chat
    return executor


@pytest.fixture
def consumer():
    with (
        patch("src.jobs.consumers.agent_run.get_settings") as mock_settings,
        patch("src.jobs.consumers.agent_run.get_session_factory"),
        patch("src.jobs.consumers.agent_run.BaseConsumer.__init__", return_value=None),
    ):
        mock_settings.return_value = MagicMock(max_concurrency=2)
        c = AgentRunConsumer()
        return c


@pytest.mark.asyncio
async def test_missing_snapshot_and_context_fails_closed(consumer):
    """A legacy row with no snapshot and no Redis context fails with a recovery reason."""
    run_id = str(uuid4())
    queued_run = MagicMock(status="queued")
    queued_run.trigger_type = "manual"
    queued_run.agent_id = uuid4()
    queued_run.execution_snapshot = None
    queued_run.org_id = None
    queued_run.caller_user_id = None
    queued_run.caller_email = None
    queued_run.caller_name = None
    queued_run.event_delivery_id = None
    queued_run.conversation_id = None
    queued_run.trigger_source = None
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    async def terminalize(_session, _run_id, status, *, error=None, **_kwargs):
        queued_run.status = status
        queued_run.error = error
        queued_run.completed_at = datetime.now(timezone.utc)
        return queued_run

    with (
        patch(
            "src.jobs.consumers.agent_run.get_redis",
            return_value=FakeRedisCtx(redis_mock),
        ),
        patch(
            "src.jobs.consumers.agent_run.run_store.terminalize_unleased",
            new=AsyncMock(side_effect=terminalize),
        ),
    ):
        await consumer.process_message({"run_id": run_id})

    assert queued_run.status == "failed"
    assert "snapshots" in queued_run.error
    assert queued_run.completed_at is not None


@pytest.mark.asyncio
async def test_agent_not_found_returns_early(consumer):
    """When the agent doesn't exist in the DB, process_message logs and returns without crashing."""
    run_id = str(uuid4())
    agent_id = uuid4()

    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    queued_run = MagicMock(status="queued")
    queued_run.trigger_type = "manual"
    queued_run.agent_id = agent_id
    queued_run.execution_snapshot = {"format_version": 1}
    queued_run.org_id = None
    queued_run.caller_user_id = None
    queued_run.caller_email = None
    queued_run.caller_name = None
    queued_run.event_delivery_id = None
    queued_run.conversation_id = None
    queued_run.trigger_source = None

    # DB session where the durable run exists but the agent no longer does.
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session.execute.return_value = mock_result

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    claimed = MagicMock()
    claimed.lease_token = "tok-1"
    claimed.attempt = 1

    async def terminalize(_session, _run_id, status, *, error=None, **_kwargs):
        queued_run.status = status
        queued_run.error = error
        return queued_run

    with (
        patch(
            "src.jobs.consumers.agent_run.get_redis",
            return_value=FakeRedisCtx(redis_mock),
        ),
        patch(
            "src.jobs.consumers.agent_run.run_store.claim_run",
            new=AsyncMock(return_value=claimed),
        ),
        patch(
            "src.jobs.consumers.agent_run.run_store.terminalize_unleased",
            new=AsyncMock(side_effect=terminalize),
        ),
    ):
        await consumer.process_message({"run_id": run_id})

    # Verify the agent query was executed
    mock_session.execute.assert_called_once()
    assert queued_run.status == "failed"
    assert queued_run.error == "Agent no longer exists"


@pytest.mark.asyncio
async def test_pre_cancel_updates_existing_queued_run(consumer):
    run_id = str(uuid4())
    queued_run = MagicMock(status="queued")
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session.add = MagicMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    # Pre-cancel is signalled by the dedicated cancel flag, not a context blob.
    redis_mock = AsyncMock()
    redis_mock.get.return_value = "1"

    async def terminalize(_session, _run_id, status, *, error=None, **_kwargs):
        queued_run.status = status
        queued_run.error = error
        queued_run.completed_at = datetime.now(timezone.utc)
        return queued_run

    with (
        patch(
            "src.jobs.consumers.agent_run.get_redis",
            return_value=FakeRedisCtx(redis_mock),
        ),
        patch(
            "src.jobs.consumers.agent_run.run_store.terminalize_unleased",
            new=AsyncMock(side_effect=terminalize),
        ),
    ):
        await consumer.process_message({"run_id": run_id})

    assert queued_run.status == "cancelled"
    assert queued_run.completed_at is not None
    mock_session.add.assert_not_called()
    _, get_kwargs = mock_session.get.call_args
    assert get_kwargs["with_for_update"] == {"of": AgentRun}


@pytest.mark.asyncio
async def test_late_terminalized_run_is_not_overwritten(
    consumer,
    db_session,
    async_session_factory,
    seed_agent,
):
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        agent_id=seed_agent.id,
        trigger_type="manual",
        status="queued",
        iterations_used=0,
        tokens_used=0,
        created_at=datetime.now(timezone.utc),
        execution_snapshot={
            "format_version": 1,
            "system_prompt": "You are a test agent.",
            "model": {"profile_id": None, "provider": "test", "model": "test"},
            "tools": [],
            "delegated_agents": [],
            "system_tools": [],
            "limits": {},
        },
    )
    db_session.add(run)
    await db_session.commit()

    consumer._session_factory = async_session_factory

    redis_mock = AsyncMock()
    context_key = f"bifrost:agent_run:{run_id}:context"
    cancel_key = f"bifrost:agent_run:{run_id}:cancel"

    async def _redis_get(key):
        if key == context_key:
            return json.dumps({"org_id": str(uuid4()), "input": "hello"})
        if key == cancel_key:
            return None
        return None

    redis_mock.get.side_effect = _redis_get

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            FakeLateExecutor,
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()) as publish_mock,
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()) as sync_mock,
    ):
        await consumer.process_message(
            {
                "run_id": str(run_id),
                "agent_id": str(seed_agent.id),
                "trigger_type": "manual",
                "sync": True,
            }
        )

    refreshed = await _load_run(async_session_factory, run_id)
    assert refreshed.status == "timeout"
    assert refreshed.error == "scheduler terminalized the run"
    assert refreshed.completed_at is not None
    assert publish_mock.await_count == 2
    assert publish_mock.await_args_list[0].args[0].status == "running"
    assert publish_mock.await_args_list[1].args[0].status == "timeout"
    sync_payload = sync_mock.await_args.args[1]
    assert sync_payload["status"] == "timeout"
    assert sync_payload["error"] == "scheduler terminalized the run"


class FakeSnapshotExecutor:
    """Executor stub recording the snapshot-driven invocation."""

    seen_kwargs: dict = {}

    def __init__(self, session_factory, redis_client):
        self._session_factory = session_factory

    async def run(self, **kwargs):
        FakeSnapshotExecutor.seen_kwargs = kwargs
        return {
            "output": {"text": "executed from snapshot"},
            "iterations_used": 1,
            "tokens_used": 5,
            "status": "completed",
            "llm_model": "test-model",
        }

    async def flush_to_db(self, db):
        return None

    async def recover_durable_usage(self, *, agent, run_id):
        return None


class FakeSyntheticSnapshotExecutor(FakeSnapshotExecutor):
    """Snapshot stub that proves the consumer attached the persisted router."""

    loaded_router = None

    async def run(self, **kwargs):
        type(self).loaded_router = getattr(self, "_synthetic_router", None)
        return await super().run(**kwargs)


@pytest.mark.asyncio
async def test_executes_from_postgres_after_redis_loss(
    consumer,
    db_session,
    async_session_factory,
    seed_agent,
):
    """An admitted run executes from its PG snapshot with no Redis state."""
    run_id = uuid4()
    snapshot = {
        "format_version": 1,
        "system_prompt": "Pinned prompt.",
        "model": {"profile_id": None, "provider": "test", "model": "test"},
        "tools": [],
        "delegated_agents": [],
        "system_tools": [],
        "limits": {},
    }
    run = AgentRun(
        id=run_id,
        agent_id=seed_agent.id,
        trigger_type="manual",
        status="queued",
        input={"task": "from pg"},
        output_schema={"type": "object"},
        iterations_used=0,
        tokens_used=0,
        created_at=datetime.now(timezone.utc),
        execution_snapshot=snapshot,
    )
    db_session.add(run)
    await db_session.commit()

    consumer._session_factory = async_session_factory
    FakeSnapshotExecutor.seen_kwargs = {}

    # Redis lost everything: no cancel flag, no legacy context.
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            FakeSnapshotExecutor,
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()) as sync_mock,
        patch(
            "src.services.execution.run_summarizer.enqueue_summarize",
            AsyncMock(),
        ),
    ):
        # Nudge carries only the run ID.
        await consumer.process_message({"run_id": str(run_id)})

    seen = FakeSnapshotExecutor.seen_kwargs
    assert seen["run_id"] == str(run_id)
    assert seen["input_data"] == {"task": "from pg"}
    assert seen["output_schema"] == {"type": "object"}
    assert seen["execution_snapshot"] == snapshot

    refreshed = await _load_run(async_session_factory, run_id)
    assert refreshed.status == "completed"
    assert refreshed.output == {"text": "executed from snapshot"}
    sync_payload = sync_mock.await_args.args[1]
    assert sync_payload["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "is_superuser",
        "is_external",
        "is_provider_org",
        "roles",
        "is_platform_admin",
    ),
    [
        (False, False, True, [], False),
        (False, True, False, [], False),
        (True, False, False, [], True),
        (False, False, False, ["Support"], False),
        (False, False, False, ["Platform Admin"], True),
    ],
    ids=("provider-org", "external", "superuser", "roles", "role-admin"),
)
async def test_enqueue_to_consumer_preserves_trusted_caller_authorization(
    consumer,
    db_session,
    async_session_factory,
    seed_agent,
    is_superuser,
    is_external,
    is_provider_org,
    roles,
    is_platform_admin,
):
    """The queue nudge cannot erase caller authorization facts."""
    caller_id = uuid4()
    # enqueue_agent_run opens its own session, so make the seeded FK visible.
    await db_session.commit()
    snapshot = {
        "format_version": 1,
        "system_prompt": "Pinned prompt.",
        "model": {"profile_id": None, "provider": "test", "model": "test"},
        "tools": [],
        "delegated_agents": [],
        "system_tools": [],
        "limits": {},
    }
    with (
        patch.object(
            agent_run_service,
            "get_session_factory",
            return_value=async_session_factory,
        ),
        patch.object(agent_run_service, "publish_message", AsyncMock()),
        patch(
            "src.services.agent_runtime.execution_snapshot.snapshot_agent",
            new=AsyncMock(return_value=snapshot),
        ),
    ):
        run_id = await enqueue_agent_run(
            agent_id=str(seed_agent.id),
            trigger_type="api",
            input_data={"task": "preserve authorization"},
            org_id=None,
            caller_user_id=str(caller_id),
            caller_email="caller@example.test",
            caller_name="Caller",
            caller_is_superuser=is_superuser,
            caller_is_platform_admin=is_platform_admin,
            caller_is_external=is_external,
            caller_is_provider_org=is_provider_org,
            caller_roles=roles,
        )

    consumer._session_factory = async_session_factory
    FakeSnapshotExecutor.seen_kwargs = {}
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            FakeSnapshotExecutor,
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()),
        patch("src.services.execution.run_summarizer.enqueue_summarize", AsyncMock()),
    ):
        await consumer.process_message({"run_id": run_id})

    caller = FakeSnapshotExecutor.seen_kwargs["_caller"]
    assert caller["user_id"] == str(caller_id)
    assert caller["is_superuser"] is is_superuser
    assert caller["is_platform_admin"] is is_platform_admin
    assert caller["is_external"] is is_external
    assert caller["is_provider_org"] is is_provider_org
    assert caller["roles"] == roles


@pytest.mark.asyncio
async def test_reclaims_running_expired_lease_from_run_id_nudge(
    consumer,
    db_session,
    async_session_factory,
    seed_agent,
):
    """Recovery nudges must reach the fenced claim, not be rejected as running."""
    from datetime import timedelta

    run_id = uuid4()
    snapshot = {
        "format_version": 1,
        "system_prompt": "Pinned prompt.",
        "model": {"profile_id": None, "provider": "test", "model": "test"},
        "tools": [],
        "delegated_agents": [],
        "system_tools": [],
        "knowledge_sources": [],
        "limits": {},
    }
    db_session.add(
        AgentRun(
            id=run_id,
            agent_id=seed_agent.id,
            trigger_type="manual",
            status="running",
            input={"task": "recover"},
            execution_snapshot=snapshot,
            attempt=1,
            lease_owner="lost-worker",
            lease_token="expired-token",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    await db_session.commit()
    consumer._session_factory = async_session_factory
    FakeSnapshotExecutor.seen_kwargs = {}
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            FakeSnapshotExecutor,
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()),
        patch("src.services.execution.run_summarizer.enqueue_summarize", AsyncMock()),
    ):
        await consumer.process_message({"run_id": str(run_id)})

    refreshed = await _load_run(async_session_factory, run_id)
    assert refreshed.status == "completed"
    assert refreshed.attempt == 2


@pytest.mark.asyncio
async def test_synthetic_marker_mismatch_fails_closed(
    consumer,
    db_session,
    async_session_factory,
    seed_agent,
):
    """Evaluation execution may not proceed without matching durable markers."""
    run_id = uuid4()
    snapshot = {
        "format_version": 1,
        "system_prompt": "Pinned prompt.",
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
    db_session.add(
        AgentRun(
            id=run_id,
            agent_id=seed_agent.id,
            trigger_type="evaluation_synthetic",
            status="queued",
            input={"task": "synthetic"},
            execution_snapshot=snapshot,
            correlation={"evaluation_mode": "production"},
        )
    )
    await db_session.commit()
    consumer._session_factory = async_session_factory
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            FakeSnapshotExecutor,
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()),
    ):
        await consumer.process_message({"run_id": str(run_id)})

    refreshed = await _load_run(async_session_factory, run_id)
    assert refreshed.status == "failed"
    assert "marker, trigger type, and correlation disagree" in (refreshed.error or "")


@pytest.mark.asyncio
async def test_synthetic_run_loads_persisted_router_before_execution(
    consumer,
    db_session,
    async_session_factory,
    seed_agent,
):
    """A valid synthetic row loads its durable simulator, never a real router."""
    from src.models.orm.agent_evaluations import AgentSimulationSession

    run_id = uuid4()
    simulation_id = uuid4()
    snapshot = {
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
    correlation = {
        "evaluation_mode": "evaluation_synthetic",
        "evaluation_suite_id": str(uuid4()),
        "evaluation_case_id": str(uuid4()),
        "evaluation_execution_id": str(uuid4()),
        "evaluation_side": "candidate",
        "evaluation_repetition": 0,
    }
    db_session.add(
        AgentRun(
            id=run_id,
            agent_id=seed_agent.id,
            trigger_type="evaluation_synthetic",
            status="queued",
            input={"task": "synthetic"},
            execution_snapshot=snapshot,
            correlation=correlation,
        )
    )
    await db_session.flush()
    db_session.add(
        AgentSimulationSession(
            id=simulation_id,
            case_version=1,
            root_run_id=run_id,
            state={},
            fixture={},
            tool_schemas={},
        )
    )
    await db_session.commit()

    consumer._session_factory = async_session_factory
    FakeSyntheticSnapshotExecutor.loaded_router = None
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            FakeSyntheticSnapshotExecutor,
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()),
        patch("src.services.execution.run_summarizer.enqueue_summarize", AsyncMock()),
    ):
        await consumer.process_message({"run_id": str(run_id)})

    router = FakeSyntheticSnapshotExecutor.loaded_router
    assert router is not None
    assert router._simulation_session_id == simulation_id
    assert router._run_id == run_id
    assert router.correlation == correlation
    assert (await _load_run(async_session_factory, run_id)).status == "completed"


@pytest.mark.asyncio
async def test_legacy_redis_context_still_executes_without_snapshot(
    consumer,
    db_session,
    async_session_factory,
    seed_agent,
):
    """Rolling upgrade: a pre-snapshot row with in-flight Redis context runs live."""
    import json as json_module

    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        agent_id=seed_agent.id,
        trigger_type="manual",
        status="queued",
        input={"task": "legacy"},
        iterations_used=0,
        tokens_used=0,
        created_at=datetime.now(timezone.utc),
        execution_snapshot=None,
    )
    db_session.add(run)
    await db_session.commit()

    consumer._session_factory = async_session_factory
    FakeSnapshotExecutor.seen_kwargs = {}

    legacy_context = {
        "run_id": str(run_id),
        "agent_id": str(seed_agent.id),
        "trigger_type": "manual",
        "input": {"task": "legacy"},
        "output_schema": None,
        "org_id": None,
        "caller": {},
        "event_delivery_id": None,
        "conversation_id": None,
        "sync": False,
        "cancelled": False,
    }
    context_key = f"bifrost:agent_run:{run_id}:context"

    async def _redis_get(key):
        if key == context_key:
            return json_module.dumps(legacy_context)
        return None

    redis_mock = AsyncMock()
    redis_mock.get.side_effect = _redis_get

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            FakeSnapshotExecutor,
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()) as sync_mock,
        patch(
            "src.services.execution.run_summarizer.enqueue_summarize",
            AsyncMock(),
        ),
    ):
        await consumer.process_message({"run_id": str(run_id)})

    seen = FakeSnapshotExecutor.seen_kwargs
    assert seen["run_id"] == str(run_id)
    assert seen["input_data"] == {"task": "legacy"}
    assert seen["execution_snapshot"] is None

    refreshed = await _load_run(async_session_factory, run_id)
    assert refreshed.status == "completed"
    sync_payload = sync_mock.await_args.args[1]
    assert sync_payload["status"] == "completed"


@pytest.mark.asyncio
async def test_chat_run_publishes_stream_chunks_and_terminal_completion(
    consumer,
):
    run_id = str(uuid4())
    conversation_id = uuid4()
    user_id = uuid4()
    assistant_message_id = uuid4()
    user_message_id = uuid4()

    queued_run = MagicMock(
        status="running",
        agent_id=None,
        conversation_id=conversation_id,
        output=None,
        error=None,
        iterations_used=0,
        tokens_used=0,
    )
    conversation = MagicMock(spec=Conversation)
    conversation.id = conversation_id
    conversation.title = "Existing title"
    conversation.agent = None
    conversation.user_id = user_id
    conversation.user = MagicMock(id=user_id)

    run_obj = MagicMock(
        status="running",
        output=None,
        error=None,
        iterations_used=0,
        tokens_used=0,
        llm_model=None,
        duration_ms=None,
        completed_at=None,
    )

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: conversation)
    )

    async def _get(model, obj_id, **kwargs):
        if model is AgentRun:
            return run_obj
        if model is Conversation:
            return conversation
        return None

    mock_session.get = AsyncMock(side_effect=_get)
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    publish_chat = AsyncMock()
    publish_run = AsyncMock()

    fake_executor = _chat_executor_stub(
        [
            ChatStreamChunk(
                type="message_start",
                user_message_id=str(user_message_id),
                assistant_message_id=str(assistant_message_id),
            ),
            ChatStreamChunk(type="delta", content="Hello "),
            ChatStreamChunk(
                type="done",
                content="Hello world",
                message_id=str(assistant_message_id),
                finish_reason="stop",
                incomplete=False,
                run_status="completed",
            ),
        ]
    )

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.ai_model_service.AIModelService.resolve_chat_profile",
            new=AsyncMock(
                return_value=(
                    MagicMock(id=uuid4(), name="Everyday"),
                    SimpleNamespace(model="test-model"),
                    SimpleNamespace(),
                )
            ),
        ),
        patch("src.services.agent_executor.AgentExecutor", return_value=fake_executor),
        patch("src.jobs.consumers.agent_run.publish_chat_run_event", publish_chat),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", publish_run),
    ):
        await consumer._process_chat_run(
            run_id=run_id,
            context={
                "input": {
                    "conversation_id": str(conversation_id),
                    "content": "Hello world",
                    "user_message_id": str(user_message_id),
                    "client_run_id": str(uuid4()),
                },
                "caller": {
                    "user_id": str(user_id),
                    "email": "caller@example.com",
                    "name": "Caller",
                },
            },
            agent_run=queued_run,
            agent=None,
            sync=False,
            start_time=time.time(),
        )

    assert [call.kwargs["kind"] for call in publish_chat.await_args_list] == [
        "message_start",
        "delta",
        "done",
    ]
    assert [call.kwargs["status"] for call in publish_chat.await_args_list] == [
        "running",
        "running",
        "completed",
    ]
    assert run_obj.status == "completed"
    assert run_obj.output == {
        "text": "Hello world",
        "finish_reason": "stop",
        "incomplete": False,
    }
    assert publish_run.await_count == 1
    assert publish_run.await_args.args[0].status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interruption", "expected_kind", "expected_status", "expected_error"),
    [
        ("cancel", "cancelled", "cancelled", "Chat run cancelled"),
        ("timeout", "error", "timeout", "Chat run timed out after 0.001s"),
    ],
)
async def test_chat_run_interruption_persists_partial_output_and_terminal_event(
    consumer,
    interruption,
    expected_kind,
    expected_status,
    expected_error,
):
    run_id = str(uuid4())
    conversation_id = uuid4()
    user_id = uuid4()
    assistant_message_id = uuid4()
    user_message_id = uuid4()

    queued_run = MagicMock(
        status="running",
        agent_id=None,
        conversation_id=conversation_id,
        output=None,
        error=None,
        iterations_used=0,
        tokens_used=0,
    )
    conversation = MagicMock(spec=Conversation)
    conversation.id = conversation_id
    conversation.title = "Existing title"
    conversation.agent = None
    conversation.user_id = user_id
    conversation.user = MagicMock(id=user_id)

    run_obj = MagicMock(
        status="running",
        output=None,
        error=None,
        iterations_used=0,
        tokens_used=0,
        llm_model=None,
        duration_ms=None,
        completed_at=None,
    )

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: conversation)
    )

    async def _get(model, obj_id, **kwargs):
        if model is AgentRun:
            return run_obj
        if model is Conversation:
            return conversation
        return None

    mock_session.get = AsyncMock(side_effect=_get)
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    publish_chat = AsyncMock()
    publish_run = AsyncMock()

    fake_executor = _chat_executor_stub(
        [
            ChatStreamChunk(
                type="message_start",
                user_message_id=str(user_message_id),
                assistant_message_id=str(assistant_message_id),
            ),
            ChatStreamChunk(type="delta", content="Partial "),
        ],
        usage_requests=4,
        usage_tokens=19,
        cancel_after=1 if interruption == "cancel" else None,
        stall_after=1 if interruption == "timeout" else None,
    )

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.ai_model_service.AIModelService.resolve_chat_profile",
            new=AsyncMock(
                return_value=(
                    MagicMock(id=uuid4(), name="Everyday"),
                    SimpleNamespace(model="test-model"),
                    SimpleNamespace(),
                )
            ),
        ),
        patch("src.services.agent_executor.AgentExecutor", return_value=fake_executor),
        patch("src.jobs.consumers.agent_run.DEFAULT_RUN_TIMEOUT_SECONDS", 0.001),
        patch("src.jobs.consumers.agent_run.publish_chat_run_event", publish_chat),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", publish_run),
    ):
        await consumer._process_chat_run(
            run_id=run_id,
            context={
                "input": {
                    "conversation_id": str(conversation_id),
                    "content": "Hello world",
                    "user_message_id": str(user_message_id),
                    "client_run_id": str(uuid4()),
                },
                "caller": {
                    "user_id": str(user_id),
                    "email": "caller@example.com",
                    "name": "Caller",
                },
            },
            agent_run=queued_run,
            agent=None,
            sync=False,
            start_time=time.time(),
        )

    assert [call.kwargs["kind"] for call in publish_chat.await_args_list] == [
        "message_start",
        "delta",
        expected_kind,
    ]
    assert [call.kwargs["status"] for call in publish_chat.await_args_list] == [
        "running",
        "running",
        expected_status,
    ]
    fake_executor._save_message.assert_awaited_once()
    save_kwargs = fake_executor._save_message.await_args.kwargs
    assert save_kwargs["message_id"] == assistant_message_id
    assert save_kwargs["role"] == MessageRole.ASSISTANT
    assert save_kwargs["content"] == "Partial "
    assert run_obj.status == expected_status
    assert run_obj.output == {"text": "Partial ", "partial": True}
    assert run_obj.error == expected_error
    assert publish_run.await_count == 1
    assert publish_run.await_args.args[0].status == expected_status


@pytest.mark.asyncio
async def test_agentless_chat_skips_agent_lookup_and_socket_dependencies(
    consumer,
):
    run_id = str(uuid4())
    conversation_id = uuid4()
    caller_id = uuid4()
    queued_run = MagicMock(status="queued")
    queued_run.trigger_type = "chat"
    queued_run.trigger_source = None
    queued_run.agent_id = None
    queued_run.org_id = None
    queued_run.input = {
        "conversation_id": str(conversation_id),
        "content": "Hello",
        "client_run_id": str(uuid4()),
    }
    queued_run.output_schema = None
    queued_run.caller_user_id = str(caller_id)
    queued_run.caller_email = "caller@example.com"
    queued_run.caller_name = "Caller"
    queued_run.event_delivery_id = None
    queued_run.conversation_id = conversation_id
    queued_run.execution_snapshot = None
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session.execute = AsyncMock(side_effect=AssertionError("agent lookup should be skipped for chat"))
    mock_session.commit = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    process_chat = AsyncMock()

    publish_chat = AsyncMock()
    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch("src.jobs.consumers.agent_run.publish_chat_run_event", publish_chat),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch.object(consumer, "_process_chat_run", process_chat),
    ):
        await consumer.process_message({"run_id": run_id})

    mock_session.execute.assert_not_called()
    process_chat.assert_awaited_once()
    assert process_chat.await_args.kwargs["agent"] is None
    assert queued_run.status == "running"
    publish_chat.assert_awaited_once()
    assert publish_chat.await_args.kwargs["kind"] == "run_status"
    assert publish_chat.await_args.kwargs["status"] == "running"


@pytest.mark.asyncio
async def test_chat_outer_failure_publishes_terminal_error_envelope(
    consumer,
):
    run_id = str(uuid4())
    conversation_id = uuid4()
    queued_run = MagicMock(status="queued", conversation_id=conversation_id)
    queued_run.trigger_type = "chat"
    queued_run.trigger_source = None
    queued_run.agent_id = None
    queued_run.org_id = None
    queued_run.input = {
        "conversation_id": str(conversation_id),
        "content": "Hello",
        "client_run_id": str(uuid4()),
    }
    queued_run.output_schema = None
    queued_run.caller_user_id = str(uuid4())
    queued_run.caller_email = "caller@example.com"
    queued_run.caller_name = "Caller"
    queued_run.event_delivery_id = None
    queued_run.execution_snapshot = None
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session.execute = AsyncMock(side_effect=AssertionError("agent lookup should be skipped for chat"))
    mock_session.commit = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    publish_chat = AsyncMock()
    publish_run = AsyncMock()

    async def terminalize(_session, _run_id, status, *, error=None, **_kwargs):
        queued_run.status = status
        queued_run.error = error
        queued_run.completed_at = datetime.now(timezone.utc)
        return queued_run

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch("src.jobs.consumers.agent_run.publish_chat_run_event", publish_chat),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", publish_run),
        patch(
            "src.jobs.consumers.agent_run.run_store.terminalize_unleased",
            new=AsyncMock(side_effect=terminalize),
        ),
        patch.object(
            consumer,
            "_process_chat_run",
            AsyncMock(side_effect=RuntimeError("chat exploded before streaming")),
        ),
    ):
        await consumer.process_message(
            {
                "run_id": run_id,
                "agent_id": None,
                "trigger_type": "chat",
            }
        )

    assert [call.kwargs["kind"] for call in publish_chat.await_args_list] == [
        "run_status",
        "error",
    ]
    assert [call.kwargs["status"] for call in publish_chat.await_args_list] == [
        "running",
        "failed",
    ]
    terminal_payload = publish_chat.await_args_list[-1].kwargs["payload"]
    assert terminal_payload.type == "error"
    assert terminal_payload.run_status == "failed"
    assert publish_run.await_args.args[0].status == "failed"
