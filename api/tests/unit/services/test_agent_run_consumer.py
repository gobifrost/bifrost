"""Unit tests for AgentRunConsumer error handling paths."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.core.principal import UserPrincipal
from src.jobs.consumers.agent_run import (
    ACTING_USER_UNAVAILABLE_ERROR,
    RUN_AS_INACTIVE_ERROR,
    AgentRunConsumer,
)
from src.models.contracts.agents import ChatStreamChunk
from src.models.enums import MessageRole
from src.models.orm.agents import Conversation
from src.models.orm.agent_runs import AgentRun
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.execution.autonomous_agent_executor import ActingUser


class FakeRedisCtx:
    """Async context manager that yields a mock redis client."""

    def __init__(self, redis_mock):
        self._redis = redis_mock

    async def __aenter__(self):
        return self._redis

    async def __aexit__(self, *args):
        pass


def _dispatched(context):
    """The claim takes the run's context from its dispatch."""
    return patch(
        "src.services.work_dispatch.take_dispatch_payload",
        new=AsyncMock(return_value=context),
    )


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


def _chat_caller(user_id: UUID) -> Caller:
    return Caller(
        UserPrincipal(
            user_id=user_id,
            email="caller@example.com",
            organization_id=None,
            name="Caller",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_external=False,
            is_provider_org=False,
        ),
        None,
    )


def _loaded_actor() -> ActingUser:
    user_id = uuid4()
    return ActingUser(
        user_id=str(user_id),
        email="actor@example.com",
        name="Actor",
        organization_id=None,
        is_platform_admin=False,
        is_external=False,
        is_provider_org=False,
    )


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
    executor.chat_kwargs = None

    def _chat(*args, **kwargs):
        executor.chat_kwargs = kwargs

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
    with patch("src.jobs.consumers.agent_run.get_session_factory"):
        return AgentRunConsumer()


@pytest.mark.asyncio
async def test_agent_not_found_returns_early(consumer):
    """When the agent doesn't exist in the DB, process_message logs and returns without crashing."""
    run_id = str(uuid4())

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

    with (
        _dispatched({"org_id": str(uuid4()), "input": "hello"}),
        patch(
            "src.jobs.consumers.agent_run._load_acting_user",
            new=AsyncMock(return_value=_loaded_actor()),
        ),
    ):
        await consumer.process_message(
            {
                "run_id": run_id,
                "dispatch_id": str(uuid4()),
                "agent_id": str(uuid4()),
                "trigger_type": "manual",
            }
        )

    # Verify the agent query was executed
    mock_session.execute.assert_called_once()
    assert queued_run.status == "failed"
    assert queued_run.error == "Agent no longer exists"


@pytest.mark.asyncio
async def test_a_run_cancelled_while_queued_is_skipped(consumer):
    run_id = str(uuid4())
    cancelled_run = MagicMock(status="cancelled")
    mock_session = AsyncMock()
    mock_session.get.return_value = cancelled_run
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    with _dispatched(None):
        await consumer.process_message(
            {
                "run_id": run_id,
                "dispatch_id": str(uuid4()),
                "agent_id": str(uuid4()),
                "trigger_type": "manual",
            }
        )

    assert cancelled_run.status == "cancelled"
    mock_session.execute.assert_not_called()
    mock_session.commit.assert_not_awaited()
    _, get_kwargs = mock_session.get.call_args
    assert get_kwargs["with_for_update"] == {"of": AgentRun}


@pytest.mark.asyncio
@pytest.mark.parametrize("acting_user_id", [None, uuid4()], ids=["unbound", "deleted"])
async def test_unavailable_actor_fails_before_agent_execution(consumer, acting_user_id):
    run_id = str(uuid4())
    queued_run = MagicMock(status="queued", acting_user_id=acting_user_id)
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    with (
        _dispatched({"input": {}}),
        patch(
            "src.jobs.consumers.agent_run._load_acting_user",
            new=AsyncMock(return_value=None),
        ) as load_actor,
    ):
        await consumer.process_message(
            {
                "run_id": run_id,
                "dispatch_id": str(uuid4()),
                "agent_id": str(uuid4()),
                "trigger_type": "api",
            }
        )

    load_actor.assert_awaited_once_with(mock_session, acting_user_id)
    assert queued_run.status == "failed"
    assert queued_run.error == ACTING_USER_UNAVAILABLE_ERROR
    assert queued_run.completed_at is not None


@pytest.mark.asyncio
async def test_late_terminalized_run_is_not_overwritten(
    consumer,
    db_session,
    async_session_factory,
    seed_agent,
):
    run_id = uuid4()
    org = Organization(name=f"late-terminal-{uuid4().hex}", created_by="agent-run-test")
    db_session.add(org)
    await db_session.flush()
    actor = User(
        email=f"actor-{uuid4().hex}@example.com",
        organization_id=org.id,
    )
    db_session.add(actor)
    await db_session.flush()
    run = AgentRun(
        id=run_id,
        agent_id=seed_agent.id,
        trigger_type="manual",
        status="queued",
        org_id=org.id,
        run_user_id=actor.id,
        iterations_used=0,
        tokens_used=0,
        acting_user_id=actor.id,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()

    consumer._session_factory = async_session_factory

    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    try:
        with (
            _dispatched({"org_id": str(uuid4()), "input": "hello"}),
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
                    "dispatch_id": str(uuid4()),
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
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(AgentRun.__table__.delete().where(AgentRun.id == run_id))
            await cleanup.execute(User.__table__.delete().where(User.id == actor.id))
            await cleanup.execute(Organization.__table__.delete().where(Organization.id == org.id))
            await cleanup.commit()


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
            caller=_chat_caller(user_id),
            sync=False,
            start_time=time.time(),
            agent_execution_mode="automatic",
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
    assert fake_executor.chat_kwargs is not None
    assert fake_executor.chat_kwargs["agent_execution_mode"] == "automatic"


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
            caller=_chat_caller(user_id),
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
    original_user_id = uuid4()
    actor_user_id = uuid4()
    org_id = uuid4()
    queued_run = MagicMock(
        id=UUID(run_id),
        status="queued",
        conversation_id=uuid4(),
        run_user_id=original_user_id,
        acting_user_id=actor_user_id,
    )
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session.execute = AsyncMock(side_effect=AssertionError("agent lookup should be skipped for chat"))
    mock_session.commit = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    context = {
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

    process_chat = AsyncMock()
    runtime_principal = UserPrincipal(
        user_id=actor_user_id,
        email="actor@example.com",
        organization_id=org_id,
        name="Actor",
    )
    runtime_caller = Caller(
        runtime_principal,
        AuthorizationContext(
            user_id=original_user_id,
            home_organization_id=org_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(),
        ),
    )

    publish_chat = AsyncMock()
    with (
        _dispatched(context),
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.jobs.consumers.agent_run._load_acting_user",
            new=AsyncMock(return_value=_loaded_actor()),
        ),
        patch(
            "src.services.authorization.runtime_auth.load_runtime_principal",
            new=AsyncMock(return_value=runtime_principal),
        ) as load_runtime,
        patch(
            "src.services.authorization.enforce.load_caller",
            new=AsyncMock(return_value=runtime_caller),
        ) as load_caller,
        patch(
            "src.jobs.consumers.agent_run.AIBehaviorService.resolve_agent_run_execution_mode",
            new=AsyncMock(return_value="automatic"),
        ),
        patch("src.jobs.consumers.agent_run.publish_chat_run_event", publish_chat),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch.object(consumer, "_process_chat_run", process_chat),
    ):
        await consumer.process_message(
            {
                "run_id": run_id,
                "dispatch_id": str(uuid4()),
                "agent_id": None,
                "trigger_type": "chat",
            }
        )

    mock_session.execute.assert_not_called()
    load_runtime.assert_awaited_once_with(
        mock_session,
        {"credential_kind": "agent_run", "sub": run_id},
    )
    load_caller.assert_awaited_once_with(mock_session, runtime_principal)
    process_chat.assert_awaited_once()
    assert process_chat.await_args.kwargs["agent"] is None
    assert process_chat.await_args.kwargs["agent_execution_mode"] == "automatic"
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
    context = {
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

    publish_chat = AsyncMock()
    publish_run = AsyncMock()

    with (
        _dispatched(context),
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.jobs.consumers.agent_run._load_acting_user",
            new=AsyncMock(return_value=_loaded_actor()),
        ),
        patch(
            "src.jobs.consumers.agent_run.AIBehaviorService.resolve_agent_run_execution_mode",
            new=AsyncMock(return_value="delegated"),
        ),
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
                "dispatch_id": str(uuid4()),
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


def _recording_executor(runs: list[dict[str, Any]]):
    """An executor stand-in that records how the consumer starts the run."""

    class RecordingExecutor:
        subtree_tokens = 0

        def __init__(
            self,
            session_factory,
            redis_client=None,
            *,
            _delegation_depth=0,
            _ancestor_run_ids=(),
        ):
            self._session_factory = session_factory

        async def run(
            self,
            agent,
            *,
            input_data=None,
            output_schema=None,
            run_id=None,
            caller,
            _shared_usage=None,
            _shared_budget=None,
            run_user_id,
            run_as,
            acting=None,
            agent_execution_mode=None,
        ):
            runs.append(
                {
                    "caller": caller,
                    "run_user_id": run_user_id,
                    "run_as": run_as,
                    "acting": acting,
                    "agent_execution_mode": agent_execution_mode,
                }
            )
            return {
                "output": {"text": "done"},
                "iterations_used": 1,
                "tokens_used": 2,
                "status": "completed",
                "llm_model": "test-model",
            }

        async def flush_to_db(self, session):
            return None

    return RecordingExecutor


async def _run_as_fixture(db_session, seed_agent, *, target_active=True, with_run_as=True):
    """A Contoso run started by a Contoso lead, acting as a Contoso portal user."""
    contoso = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="run-as-test")
    db_session.add(contoso)
    await db_session.flush()
    lead = User(
        email=f"lead-{uuid4().hex[:8]}@contoso.example",
        name="Contoso Helpdesk Lead",
        organization_id=contoso.id,
    )
    target = User(
        email=f"portal-{uuid4().hex[:8]}@contoso.example",
        name="Contoso Portal User",
        organization_id=contoso.id,
        is_external=True,
        is_active=target_active,
    )
    db_session.add_all([lead, target])
    await db_session.flush()
    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="api",
        status="queued",
        org_id=contoso.id,
        caller_user_id=str(lead.id),
        caller_email=lead.email,
        caller_name=lead.name,
        run_user_id=lead.id,
        run_as_user_id=target.id if with_run_as else None,
        acting_user_id=target.id if with_run_as else lead.id,
        iterations_used=0,
        tokens_used=0,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()
    return contoso, lead, target, run


async def _cleanup_run_as_fixture(async_session_factory, contoso, lead, target, run):
    """Remove rows committed for a cross-session consumer test."""
    async with async_session_factory() as cleanup:
        await cleanup.execute(AgentRun.__table__.delete().where(AgentRun.id == run.id))
        await cleanup.execute(User.__table__.delete().where(User.id.in_([lead.id, target.id])))
        await cleanup.execute(Organization.__table__.delete().where(Organization.id == contoso.id))
        await cleanup.commit()


async def _consume(consumer, run, context, runs):
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    with (
        _dispatched(context),
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            _recording_executor(runs),
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()),
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()) as sync_mock,
        patch(
            "src.services.execution.summary_dispatch.stage_summary",
            AsyncMock(return_value=None),
        ),
    ):
        await consumer.process_message(
            {
                "run_id": str(run.id),
                "dispatch_id": str(uuid4()),
                "agent_id": str(run.agent_id),
                "trigger_type": "api",
                "sync": True,
            }
        )
    return sync_mock


@pytest.mark.asyncio
async def test_run_as_user_comes_from_the_database_not_the_redis_context(
    consumer, db_session, async_session_factory, seed_agent
):
    contoso, lead, target, run = await _run_as_fixture(db_session, seed_agent)
    # The Redis context names someone else; only the rows decide who acts.
    redis_caller = {
        "user_id": str(uuid4()),
        "email": "admin@fabrikam.example",
        "name": "Fabrikam Admin",
        "organization_id": str(uuid4()),
        "is_platform_admin": True,
    }
    consumer._session_factory = async_session_factory
    runs: list[dict[str, Any]] = []

    try:
        await _consume(consumer, run, {"input": "hello", "caller": redis_caller}, runs)

        expected_run_as = ActingUser(
            user_id=str(target.id),
            email=target.email,
            name="Contoso Portal User",
            organization_id=contoso.id,
            is_platform_admin=False,
            is_external=True,
            is_provider_org=False,
        )
        assert len(runs) == 1
        runtime_caller = runs[0]["caller"]
        assert runtime_caller.principal.user_id == target.id
        assert runtime_caller.principal.runtime_authority is not None
        assert runtime_caller.principal.runtime_authority.kind == "agent_run"
        assert runtime_caller.ctx.user_id == lead.id
        assert runs[0]["run_user_id"] == lead.id
        assert runs[0]["run_as"] == expected_run_as
        assert runs[0]["acting"] == expected_run_as
        assert runs[0]["agent_execution_mode"] == "delegated"
        refreshed = await _load_run(async_session_factory, run.id)
        assert refreshed.status == "completed"
    finally:
        await _cleanup_run_as_fixture(async_session_factory, contoso, lead, target, run)


@pytest.mark.asyncio
async def test_inactive_run_as_user_fails_the_run_without_executing(
    consumer, db_session, async_session_factory, seed_agent
):
    contoso, lead, target, run = await _run_as_fixture(
        db_session, seed_agent, target_active=False
    )
    consumer._session_factory = async_session_factory
    runs: list[dict[str, Any]] = []

    try:
        sync_mock = await _consume(
            consumer, run, {"input": "hello", "caller": {"user_id": str(lead.id)}}, runs
        )

        assert runs == []
        refreshed = await _load_run(async_session_factory, run.id)
        assert refreshed.status == "failed"
        assert refreshed.error == RUN_AS_INACTIVE_ERROR
        assert refreshed.completed_at is not None
        sync_mock.assert_awaited_once_with(
            str(run.id),
            {"output": None, "status": "failed", "error": RUN_AS_INACTIVE_ERROR},
        )
    finally:
        await _cleanup_run_as_fixture(async_session_factory, contoso, lead, target, run)


@pytest.mark.asyncio
async def test_run_without_run_as_passes_the_persisted_actor(
    consumer, db_session, async_session_factory, seed_agent
):
    contoso, lead, target, run = await _run_as_fixture(
        db_session, seed_agent, with_run_as=False
    )
    caller = {"user_id": str(lead.id), "email": lead.email, "name": lead.name}
    consumer._session_factory = async_session_factory
    runs: list[dict[str, Any]] = []

    try:
        await _consume(consumer, run, {"input": "hello", "caller": caller}, runs)

        assert len(runs) == 1
        runtime_caller = runs[0]["caller"]
        assert runtime_caller.principal.user_id == lead.id
        assert runtime_caller.principal.runtime_authority is not None
        assert runtime_caller.principal.runtime_authority.kind == "agent_run"
        assert runtime_caller.ctx.user_id == lead.id
        assert runs[0]["run_user_id"] == lead.id
        assert runs[0]["run_as"] is None
        assert runs[0]["agent_execution_mode"] == "delegated"
        assert runs[0]["acting"] == ActingUser(
            user_id=str(lead.id),
            email=lead.email,
            name=lead.name,
            organization_id=lead.organization_id,
            is_platform_admin=False,
            is_external=False,
            is_provider_org=False,
        )
    finally:
        await _cleanup_run_as_fixture(async_session_factory, contoso, lead, target, run)
