from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models.orm.agent_runs import AgentRun
from src.models.orm.work_dispatches import WorkDispatch
from src.services import work_dispatch
from src.services.execution import agent_run_dispatch, agent_run_service
from src.services.execution.agent_run_service import enqueue_agent_run
from src.services.audit_context import ActorContext, clear_actor, set_actor


@pytest.fixture
def env(monkeypatch):
    calls: list[str] = []
    session = AsyncMock()
    session.add = MagicMock()

    @asynccontextmanager
    async def accept_context():
        yield session
        calls.append("commit")

    monkeypatch.setattr(agent_run_service, "get_dispatch_db_context", accept_context)

    redis = AsyncMock()

    @asynccontextmanager
    async def redis_context():
        yield redis

    monkeypatch.setattr(agent_run_dispatch, "get_redis", redis_context)

    async def wake() -> None:
        calls.append("wake")

    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)

    def added(kind):  # type: ignore[no-untyped-def]
        return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], kind)]

    return SimpleNamespace(
        calls=calls,
        session=session,
        redis=redis,
        runs=lambda: added(AgentRun),
        dispatches=lambda: added(WorkDispatch),
    )


class TestEnqueueAgentRun:
    _real_wake = staticmethod(work_dispatch.wake_pool_workers)

    @pytest.mark.asyncio
    async def test_persists_current_impersonation_provenance(self, env):
        operator_id, session_id = uuid4(), uuid4()
        token = set_actor(
            ActorContext(
                user_id=uuid4(),
                organization_id=uuid4(),
                impersonated_by_user_id=operator_id,
                impersonation_session_id=session_id,
            )
        )
        try:
            await enqueue_agent_run(agent_id=str(uuid4()), trigger_type="api", lineage=None)
        finally:
            clear_actor(token)

        [row] = env.runs()
        assert row.impersonated_by_user_id == operator_id
        assert row.impersonation_session_id == session_id

    @pytest.mark.asyncio
    async def test_commits_run_and_dispatch_together_before_waking_executors(self, env):
        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="event",
            input_data={"ticket_id": 123},
            lineage=None,
        )

        [queued_run] = env.runs()
        [dispatch] = env.dispatches()
        assert str(queued_run.id) == run_id
        assert queued_run.status == "queued"
        assert queued_run.input == {"ticket_id": 123}
        assert (dispatch.subject_id, dispatch.family) == (queued_run.id, "agent_run")
        assert dispatch.payload["input"] == {"ticket_id": 123}
        assert env.calls[:2] == ["commit", "wake"]
        assert dispatch.assigned_worker_id is None
        assert dispatch.message["dispatch_id"] == str(dispatch.id)

    @pytest.mark.asyncio
    async def test_runs_lifecycle_hook_before_queue_publish(self, env):
        before_publish = AsyncMock(side_effect=lambda *_: env.calls.append("lifecycle"))

        run_id = await enqueue_agent_run(
            agent_id=None,
            trigger_type="chat",
            before_queue_publish=before_publish,
            lineage=None,
        )

        before_publish.assert_awaited_once_with(run_id)
        assert env.calls[:3] == ["commit", "lifecycle", "wake"]

    @pytest.mark.asyncio
    async def test_context_is_durable_and_not_cached_in_redis(self, env):
        org_id = str(uuid4())

        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            input_data={"task": "analyze"},
            output_schema={"action": {"type": "string"}},
            org_id=org_id,
            caller_user_id=str(uuid4()),
            lineage=None,
        )

        [dispatch] = env.dispatches()
        assert dispatch.payload["caller"]["organization_id"] == org_id
        env.redis.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enqueue_serializes_caller_scope_claims(self, env):
        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            caller_is_superuser=True,
            caller_is_external=True,
            caller_is_provider_org=True,
            lineage=None,
        )

        [dispatch] = env.dispatches()
        assert dispatch.payload["caller"] == {
            "user_id": None,
            "email": None,
            "name": None,
            "organization_id": None,
            "is_superuser": True,
            "is_external": True,
            "is_provider_org": True,
            "roles": [],
        }

    @pytest.mark.asyncio
    async def test_run_as_is_recorded_on_the_row_only(self, env):
        acting_user_id = uuid4()
        launch = {
            "agent_id": str(uuid4()),
            "trigger_type": "api",
            "org_id": str(uuid4()),
            "caller_user_id": str(uuid4()),
            "run_id": str(uuid4()),
            "lineage": None,
        }

        await enqueue_agent_run(**launch)
        await enqueue_agent_run(**launch, run_as_user_id=acting_user_id)

        plain_row, run_as_row = env.runs()
        plain, run_as = env.dispatches()
        assert (plain_row.run_as_user_id, run_as_row.run_as_user_id) == (None, acting_user_id)
        assert run_as.payload == plain.payload
        strip = lambda message: {k: v for k, v in message.items() if k != "dispatch_id"}  # noqa: E731
        assert strip(run_as.message) == strip(plain.message)

    @pytest.mark.asyncio
    async def test_enqueue_binds_the_callers_actor_id(self, env):
        caller_id = uuid4()

        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="api",
            caller_user_id=str(caller_id),
            lineage=None,
        )

        assert env.runs()[0].acting_user_id == caller_id

    @pytest.mark.asyncio
    async def test_enqueue_binds_the_authorized_run_as_actor_id(self, env):
        run_as_user_id = uuid4()

        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="api",
            caller_user_id=str(uuid4()),
            run_as_user_id=run_as_user_id,
            lineage=None,
        )

        assert env.runs()[0].acting_user_id == run_as_user_id

    @pytest.mark.asyncio
    async def test_enqueue_supports_agentless_conversation_runs(self, env):
        conversation_id = str(uuid4())

        await enqueue_agent_run(
            agent_id=None,
            trigger_type="chat",
            conversation_id=conversation_id,
            input_data={"content": "hello"},
            lineage=None,
        )

        [queued_run] = env.runs()
        assert queued_run.agent_id is None
        assert str(queued_run.conversation_id) == conversation_id
        assert env.dispatches()[0].payload["conversation_id"] == conversation_id
        assert env.dispatches()[0].message["agent_id"] is None

    @pytest.mark.asyncio
    async def test_enqueue_uses_provided_run_id(self, env):
        expected_run_id = str(uuid4())

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            run_id=expected_run_id,
            lineage=None,
        )

        assert run_id == expected_run_id

    @pytest.mark.asyncio
    async def test_enqueue_message_contains_sync_flag(self, env):
        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            sync=True,
            lineage=None,
        )

        assert env.dispatches()[0].message["sync"] is True

    @pytest.mark.asyncio
    async def test_a_redis_outage_does_not_fail_an_accepted_run(self, env, monkeypatch):
        """The executor wake is best-effort; the run is claimable from PostgreSQL."""
        monkeypatch.setattr(work_dispatch, "wake_pool_workers", self._real_wake)
        monkeypatch.setattr(
            "src.core.cache.redis_client.get_shared_redis",
            AsyncMock(side_effect=ConnectionError("redis unavailable")),
        )

        run_id = await enqueue_agent_run(agent_id=str(uuid4()), trigger_type="sdk", lineage=None)

        [queued_run] = env.runs()
        assert str(queued_run.id) == run_id
        assert queued_run.status == "queued"
        assert env.dispatches()[0].subject_id == queued_run.id


@pytest.mark.asyncio
async def test_queued_run_records_its_run_user(env):
    from shared.run_lineage import person_lineage

    person = uuid4()

    await enqueue_agent_run(
        agent_id=str(uuid4()),
        trigger_type="api",
        input_data={},
        lineage=person_lineage(person),
    )

    [queued_run] = env.runs()
    assert queued_run.run_user_id == person
    assert queued_run.acting_user_id == person
