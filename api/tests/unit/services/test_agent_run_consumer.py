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
async def test_missing_redis_context_returns_early(consumer):
    """Missing Redis context durably fails the queued run."""
    queued_run = MagicMock(status="queued")
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    with patch(
        "src.jobs.consumers.agent_run.get_redis",
        return_value=FakeRedisCtx(redis_mock),
    ):
        await consumer.process_message(
            {
                "run_id": str(uuid4()),
                "agent_id": str(uuid4()),
                "trigger_type": "manual",
            }
        )

    redis_mock.get.assert_called_once()
    assert queued_run.status == "failed"
    assert queued_run.error == "Agent run context was unavailable"
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_not_found_returns_early(consumer):
    """When the agent doesn't exist in the DB, process_message logs and returns without crashing."""
    run_id = str(uuid4())

    # Redis returns valid context
    redis_mock = AsyncMock()
    redis_mock.get.return_value = json.dumps({"org_id": str(uuid4()), "input": "hello"})

    queued_run = MagicMock(status="queued")

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

    # get_redis is called multiple times (initial context read, then inside finally block)
    # We need it to work for both calls
    with patch(
        "src.jobs.consumers.agent_run.get_redis",
        return_value=FakeRedisCtx(redis_mock),
    ):
        await consumer.process_message(
            {
                "run_id": run_id,
                "agent_id": str(uuid4()),
                "trigger_type": "manual",
            }
        )

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

    redis_mock = AsyncMock()
    redis_mock.get.return_value = json.dumps({"cancelled": True})

    with patch(
        "src.jobs.consumers.agent_run.get_redis",
        return_value=FakeRedisCtx(redis_mock),
    ):
        await consumer.process_message(
            {
                "run_id": run_id,
                "agent_id": str(uuid4()),
                "trigger_type": "manual",
            }
        )

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
        patch("src.jobs.consumers.agent_run.AgentExecutor", return_value=fake_executor),
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
        patch("src.jobs.consumers.agent_run.AgentExecutor", return_value=fake_executor),
        patch("src.jobs.consumers.agent_run.DEFAULT_RUN_TIMEOUT", 0.001),
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
    queued_run = MagicMock(status="queued")
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session.execute = AsyncMock(side_effect=AssertionError("agent lookup should be skipped for chat"))
    mock_session.commit = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = json.dumps(
        {
            "trigger_type": "chat",
            "input": {
                "conversation_id": str(uuid4()),
                "content": "Hello",
                "client_run_id": str(uuid4()),
            },
            "caller": {
                "user_id": str(uuid4()),
                "email": "caller@example.com",
                "name": "Caller",
            },
        }
    )

    process_chat = AsyncMock()

    publish_chat = AsyncMock()
    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch("src.jobs.consumers.agent_run.publish_chat_run_event", publish_chat),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch.object(consumer, "_process_chat_run", process_chat),
    ):
        await consumer.process_message(
            {
                "run_id": run_id,
                "agent_id": None,
                "trigger_type": "chat",
            }
        )

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
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session.execute = AsyncMock(side_effect=AssertionError("agent lookup should be skipped for chat"))
    mock_session.commit = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = json.dumps(
        {
            "trigger_type": "chat",
            "input": {
                "conversation_id": str(conversation_id),
                "content": "Hello",
                "client_run_id": str(uuid4()),
            },
            "caller": {
                "user_id": str(uuid4()),
                "email": "caller@example.com",
                "name": "Caller",
            },
        }
    )

    publish_chat = AsyncMock()
    publish_run = AsyncMock()

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch("src.jobs.consumers.agent_run.publish_chat_run_event", publish_chat),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", publish_run),
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
