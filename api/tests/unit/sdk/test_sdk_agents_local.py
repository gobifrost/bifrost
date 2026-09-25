"""Engine-local transport for ``bifrost.agents.enqueue`` / ``get_run``.

Covers the acceptance surface that does not need a forked child:

- the parent dispatcher calls the shared ``shared.sdk_agent_runs``
  service with a parent-derived actor (engine superuser vs service
  non-superuser — never child fields) and maps unknown-agent 404,
  inactive-Solution 409, paused bodies, hidden-run 404, and malformed
  frames to HTTP-style statuses;
- the child transport performs real enqueue/get_run round trips with
  zero HTTP requests and no silent HTTP fallback;
- the SDK facade maps local results to the public models/errors
  (``AgentRunHandle``, ``AgentRun``, ``AgentPausedError``,
  ``ValueError``/``PermissionError``) and preserves ``run``/``wait``
  timeout and pending semantics;
- external callers (no transport) keep the HTTP path unchanged.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from bifrost._local_transport import OP_AGENTS_ENQUEUE, OP_AGENTS_GET_RUN


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _context_data(org_id=None, **kwargs):
    data = {
        "organization": {"id": str(org_id)} if org_id is not None else None,
        "is_platform_admin": kwargs.get("is_platform_admin", False),
        "execution_id": kwargs.get("execution_id", "exec-1"),
    }
    if kwargs.get("solution_id") is not None:
        data["solution_id"] = str(kwargs["solution_id"])
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _engine_principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(org_id, **kwargs))


def _service_principal(org_id, **kwargs):
    service_id = str(kwargs.get("service_id", uuid4()))
    attempt_id = str(kwargs.get("attempt_id", uuid4()))
    return _engine_principal(
        org_id,
        service={"service_id": service_id, "attempt_id": attempt_id},
        execution_id=attempt_id,
    )


def _agent_user(principal):
    from src.services.execution.sdk_local_dispatch import (
        _agent_user_for_principal,
    )

    return _agent_user_for_principal(principal)


async def _seed_org(db_session, **kwargs):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-agents-org-{uuid4().hex[:8]}",
        is_active=kwargs.get("is_active", True),
        is_provider=kwargs.get("is_provider", False),
        created_by="sdk-agents-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_solution(db_session, *, status="active"):
    from src.models.orm.solutions import Solution as SolutionModel

    row = SolutionModel(
        slug=f"sdk-agents-sol-{uuid4().hex[:8]}",
        name="SDK Agents Solution",
        status=status,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_agent(db_session, name, **kwargs):
    from src.models.orm.agents import Agent as AgentModel

    row = AgentModel(
        name=name,
        system_prompt="Test agent prompt.",
        is_active=kwargs.get("is_active", True),
        organization_id=kwargs.get("organization_id"),
        solution_id=kwargs.get("solution_id"),
        created_by="sdk-agents-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_run(db_session, agent_id, **kwargs):
    from src.models.orm.agent_runs import AgentRun as AgentRunModel

    row = AgentRunModel(
        agent_id=agent_id,
        trigger_type=kwargs.get("trigger_type", "api"),
        status=kwargs.get("status", "completed"),
        org_id=kwargs.get("org_id"),
        caller_user_id=kwargs.get("caller_user_id"),
        parent_run_id=kwargs.get("parent_run_id"),
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _redis_stream_context(entries):
    redis = AsyncMock()
    redis.xrange = AsyncMock(return_value=entries)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=redis)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


@pytest.mark.asyncio
class TestEnqueueDispatch:
    async def test_success_returns_handle_and_attributes_actor(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        agent = await _seed_agent(db_session, "Local Agent")
        principal = _engine_principal()
        user = _agent_user(principal)
        receipt_id = str(uuid4())

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=receipt_id),
        ) as mock_enqueue:
            response = await dispatch_frame(
                lambda: _db_factory(db_session),
                principal,
                {
                    "v": 1,
                    "id": "enq-1",
                    "op": OP_AGENTS_ENQUEUE,
                    "agent_name": "Local Agent",
                    "input": {"ticket_id": 7},
                    "output_schema": None,
                },
            )

        assert response["ok"] is True, response
        assert response["result"]["run_id"] == receipt_id
        assert response["result"]["status"] == "queued"
        mock_enqueue.assert_awaited_once()
        claimed = mock_enqueue.call_args.kwargs
        assert claimed["agent_id"] == str(agent.id)
        # Actor comes from the parent principal, never child fields.
        assert claimed["org_id"] is None
        assert claimed["caller_user_id"] == str(user.user_id)
        assert claimed["caller_email"] == user.email
        assert claimed["trigger_type"] == "api"
        assert claimed["sync"] is False

    async def test_forged_actor_fields_are_ignored(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        await _seed_agent(db_session, "Local Agent")
        principal = _engine_principal()
        user = _agent_user(principal)
        receipt_id = str(uuid4())

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=receipt_id),
        ) as mock_enqueue:
            response = await dispatch_frame(
                lambda: _db_factory(db_session),
                principal,
                {
                    "v": 1,
                    "id": "enq-forge",
                    "op": OP_AGENTS_ENQUEUE,
                    "agent_name": "Local Agent",
                    "input": {},
                    "output_schema": None,
                    "caller_email": "attacker@evil.local",
                    "caller_user_id": str(uuid4()),
                    "org_id": str(uuid4()),
                },
            )

        assert response["ok"] is True, response
        claimed = mock_enqueue.call_args.kwargs
        assert claimed["caller_email"] == user.email
        assert claimed["caller_user_id"] == str(user.user_id)

    async def test_unknown_agent_is_404(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _engine_principal()
        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(),
        ) as mock_enqueue:
            response = await dispatch_frame(
                lambda: _db_factory(db_session),
                principal,
                {
                    "v": 1,
                    "id": "enq-404",
                    "op": OP_AGENTS_ENQUEUE,
                    "agent_name": "No Such Agent",
                    "input": {},
                    "output_schema": None,
                },
            )

        assert response["ok"] is False
        assert response["status"] == 404
        assert "No Such Agent" in response["detail"]
        mock_enqueue.assert_not_awaited()

    async def test_paused_agent_returns_paused_body(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        agent = await _seed_agent(db_session, "Paused Agent", is_active=False)
        principal = _engine_principal()
        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(),
        ) as mock_enqueue:
            response = await dispatch_frame(
                lambda: _db_factory(db_session),
                principal,
                {
                    "v": 1,
                    "id": "enq-paused",
                    "op": OP_AGENTS_ENQUEUE,
                    "agent_name": "Paused Agent",
                    "input": {},
                    "output_schema": None,
                },
            )

        assert response["ok"] is True, response
        assert response["result"]["status"] == "paused"
        assert response["result"]["agent_id"] == str(agent.id)
        mock_enqueue.assert_not_awaited()

    async def test_inactive_solution_is_409(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        solution = await _seed_solution(db_session, status="inactive")
        await _seed_agent(
            db_session, "Solution Agent", solution_id=solution.id
        )
        principal = _engine_principal()
        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(),
        ) as mock_enqueue:
            response = await dispatch_frame(
                lambda: _db_factory(db_session),
                principal,
                {
                    "v": 1,
                    "id": "enq-409",
                    "op": OP_AGENTS_ENQUEUE,
                    "agent_name": "Solution Agent",
                    "input": {},
                    "output_schema": None,
                },
            )

        assert response["ok"] is False
        assert response["status"] == 409
        mock_enqueue.assert_not_awaited()

    async def test_http_and_local_enqueue_agree(self, db_session):
        from fastapi import Response

        from src.routers.agent_runs import enqueue_agent_run_request
        from src.models.contracts.agent_runs import AgentRunEnqueueRequest
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        await _seed_agent(db_session, "Parity Agent")
        principal = _engine_principal()
        user = _agent_user(principal)
        receipt_id = str(uuid4())

        with patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=receipt_id),
        ):
            http_result = await enqueue_agent_run_request(
                AgentRunEnqueueRequest(agent_name="Parity Agent", input={}),
                Response(),
                db_session,
                user,
            )
            local = await dispatch_frame(
                lambda: _db_factory(db_session),
                principal,
                {
                    "v": 1,
                    "id": "enq-parity",
                    "op": OP_AGENTS_ENQUEUE,
                    "agent_name": "Parity Agent",
                    "input": {},
                    "output_schema": None,
                },
            )

        assert local["ok"] is True, local
        assert local["result"] == http_result.model_dump(mode="json")

    async def test_malformed_enqueue_is_422(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _engine_principal()
        response = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {"v": 1, "id": "enq-bad", "op": OP_AGENTS_ENQUEUE, "input": {}},
        )
        assert response["ok"] is False
        assert response["status"] == 422


@pytest.mark.asyncio
class TestGetRunDispatch:
    async def test_visible_run_returns_detail(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        agent = await _seed_agent(db_session, "History Agent")
        run = await _seed_run(db_session, agent.id, status="completed")
        principal = _engine_principal()

        response = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {
                "v": 1,
                "id": "get-1",
                "op": OP_AGENTS_GET_RUN,
                "run_id": str(run.id),
            },
        )

        assert response["ok"] is True, response
        assert response["result"]["id"] == str(run.id)
        assert response["result"]["agent_name"] == "History Agent"

    async def test_http_and_local_get_run_agree(self, db_session):
        from src.routers.agent_runs import get_agent_run
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        agent = await _seed_agent(db_session, "Parity Get Agent")
        run = await _seed_run(db_session, agent.id, status="completed")
        principal = _engine_principal()
        user = _agent_user(principal)

        http_detail = await get_agent_run(run.id, db_session, user)
        local = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {
                "v": 1,
                "id": "get-parity",
                "op": OP_AGENTS_GET_RUN,
                "run_id": str(run.id),
            },
        )

        assert local["ok"] is True, local
        assert local["result"] == http_detail.model_dump(mode="json")

    async def test_missing_run_is_404(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _engine_principal()
        missing = uuid4()
        response = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {
                "v": 1,
                "id": "get-404",
                "op": OP_AGENTS_GET_RUN,
                "run_id": str(missing),
            },
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_hidden_run_is_404_for_other_org_service(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        agent = await _seed_agent(db_session, "Scoped Agent")
        run = await _seed_run(db_session, agent.id, org_id=org_a.id)
        principal = _service_principal(org_b.id)

        response = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {
                "v": 1,
                "id": "get-hidden",
                "op": OP_AGENTS_GET_RUN,
                "run_id": str(run.id),
            },
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_service_sees_own_org_run(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        org = await _seed_org(db_session)
        agent = await _seed_agent(db_session, "Own Agent")
        run = await _seed_run(db_session, agent.id, org_id=org.id)
        principal = _service_principal(org.id)

        response = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {
                "v": 1,
                "id": "get-own",
                "op": OP_AGENTS_GET_RUN,
                "run_id": str(run.id),
            },
        )
        assert response["ok"] is True, response
        assert response["result"]["id"] == str(run.id)

    async def test_malformed_run_id_is_422(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _engine_principal()
        for frame in [
            {"v": 1, "id": "get-bad-1", "op": OP_AGENTS_GET_RUN},
            {"v": 1, "id": "get-bad-2", "op": OP_AGENTS_GET_RUN,
             "run_id": "not-a-uuid"},
            {"v": 1, "id": "get-bad-3", "op": OP_AGENTS_GET_RUN,
             "run_id": 42},
        ]:
            response = await dispatch_frame(
                lambda: _db_factory(db_session), principal, frame
            )
            assert response["ok"] is False, frame
            assert response["status"] == 422, frame


@pytest.mark.asyncio
class TestFacadeLocalMapping:
    async def test_enqueue_paused_maps_to_typed_error_without_http(self):
        import importlib as _importlib
        agents_mod = _importlib.import_module("bifrost.agents")

        async def _dead_client(*args, **kwargs):
            raise AssertionError("HTTP must not be used in the engine path")

        paused_body = {
            "status": "paused",
            "accepted": False,
            "message": "Agent 'P' is paused. Request not processed.",
            "agent_id": str(uuid4()),
        }

        class _FakeTransport:
            async def call_agents_enqueue(self, *args, **kwargs):
                return paused_body

            async def call_agents_get_run(self, *args, **kwargs):
                raise AssertionError("unexpected get_run")

        with (
            patch.object(agents_mod, "get_client", new=_dead_client),
            patch(
                "bifrost._local_transport.get", return_value=_FakeTransport()
            ),
        ):
            with pytest.raises(agents_mod.AgentPausedError) as exc_info:
                await agents_mod.agents.enqueue("P")

        assert exc_info.value.agent_id == paused_body["agent_id"]

    async def test_enqueue_success_maps_to_handle_without_http(self):
        import importlib as _importlib
        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost.models import AgentRunHandle

        run_id = str(uuid4())

        async def _dead_client(*args, **kwargs):
            raise AssertionError("HTTP must not be used in the engine path")

        class _FakeTransport:
            async def call_agents_enqueue(self, agent_name, input, output_schema):
                assert agent_name == "Local Agent"
                assert input == {"a": 1}
                return {"run_id": run_id, "status": "queued"}

            async def call_agents_get_run(self, *args, **kwargs):
                raise AssertionError("unexpected get_run")

        with (
            patch.object(agents_mod, "get_client", new=_dead_client),
            patch(
                "bifrost._local_transport.get", return_value=_FakeTransport()
            ),
        ):
            handle = await agents_mod.agents.enqueue(
                "Local Agent", {"a": 1}
            )

        assert isinstance(handle, AgentRunHandle)
        assert handle.run_id == run_id

    async def test_get_run_404_maps_to_value_error_without_http(self):
        import importlib as _importlib
        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost._local_transport import raise_for_local_status

        async def _dead_client(*args, **kwargs):
            raise AssertionError("HTTP must not be used in the engine path")

        class _FakeTransport:
            async def call_agents_enqueue(self, *args, **kwargs):
                raise AssertionError("unexpected enqueue")

            async def call_agents_get_run(self, run_id):
                raise_for_local_status(404, "not found", OP_AGENTS_GET_RUN)

        with (
            patch.object(agents_mod, "get_client", new=_dead_client),
            patch(
                "bifrost._local_transport.get", return_value=_FakeTransport()
            ),
        ):
            with pytest.raises(ValueError, match="not found"):
                await agents_mod.agents.get_run(str(uuid4()))

    async def test_get_run_403_maps_to_permission_error_without_http(self):
        import importlib as _importlib
        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost._local_transport import raise_for_local_status

        async def _dead_client(*args, **kwargs):
            raise AssertionError("HTTP must not be used in the engine path")

        class _FakeTransport:
            async def call_agents_enqueue(self, *args, **kwargs):
                raise AssertionError("unexpected enqueue")

            async def call_agents_get_run(self, run_id):
                raise_for_local_status(403, "denied", OP_AGENTS_GET_RUN)

        with (
            patch.object(agents_mod, "get_client", new=_dead_client),
            patch(
                "bifrost._local_transport.get", return_value=_FakeTransport()
            ),
        ):
            with pytest.raises(PermissionError, match="Access denied"):
                await agents_mod.agents.get_run(str(uuid4()))

    async def test_run_composes_local_enqueue_and_wait(self):
        import importlib as _importlib
        agents_mod = _importlib.import_module("bifrost.agents")

        run_id = str(uuid4())
        calls = {"enqueues": 0, "gets": 0}

        async def _dead_client(*args, **kwargs):
            raise AssertionError("HTTP must not be used in the engine path")

        class _FakeTransport:
            async def call_agents_enqueue(self, *args, **kwargs):
                calls["enqueues"] += 1
                return {"run_id": run_id, "status": "queued"}

            async def call_agents_get_run(self, rid):
                calls["gets"] += 1
                assert rid == run_id
                return {
                    "id": run_id,
                    "agent_id": str(uuid4()),
                    "trigger_type": "api",
                    "status": "completed",
                    "output": {"text": "done"},
                    "iterations_used": 0,
                    "tokens_used": 0,
                    "metadata": {},
                    "created_at": "2026-09-01T12:00:00+00:00",
                }

        with (
            patch.object(agents_mod, "get_client", new=_dead_client),
            patch(
                "bifrost._local_transport.get", return_value=_FakeTransport()
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            result = await agents_mod.agents.run("Local Agent", timeout=5.0)

        assert result == "done"
        assert calls["enqueues"] == 1
        assert calls["gets"] >= 1

    async def test_wait_timeout_preserves_pending_semantics_locally(self):
        import importlib as _importlib
        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost.models import AgentRunPending

        run_id = str(uuid4())

        async def _dead_client(*args, **kwargs):
            raise AssertionError("HTTP must not be used in the engine path")

        class _FakeTransport:
            async def call_agents_enqueue(self, *args, **kwargs):
                raise AssertionError("unexpected enqueue")

            async def call_agents_get_run(self, rid):
                return {
                    "id": run_id,
                    "agent_id": str(uuid4()),
                    "trigger_type": "api",
                    "status": "running",
                    "iterations_used": 0,
                    "tokens_used": 0,
                    "metadata": {},
                    "created_at": "2026-09-01T12:00:00+00:00",
                }

        with (
            patch.object(agents_mod, "get_client", new=_dead_client),
            patch(
                "bifrost._local_transport.get", return_value=_FakeTransport()
            ),
        ):
            pending = await agents_mod.agents.wait(run_id, timeout=0.0)

        assert isinstance(pending, AgentRunPending)
        assert pending.run_id == run_id
        assert pending.reason == "wait_timeout"
        assert pending.last_known_status is None

    async def test_external_http_path_unchanged(self):
        import importlib as _importlib
        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost.models import AgentRun, AgentRunHandle

        run_id = str(uuid4())
        response = MagicMock()
        response.json.return_value = {"run_id": run_id, "status": "queued"}

        client = MagicMock()
        client.post = AsyncMock(return_value=response)

        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "id": run_id,
            "agent_id": str(uuid4()),
            "trigger_type": "api",
            "status": "completed",
            "output": {"text": "done"},
            "iterations_used": 0,
            "tokens_used": 0,
            "metadata": {},
            "created_at": "2026-09-01T12:00:00+00:00",
        }
        client.get = AsyncMock(return_value=get_response)

        with (
            patch(
                "bifrost._local_transport.get", return_value=None
            ),
            patch.object(agents_mod, "get_client", return_value=client),
        ):
            handle = await agents_mod.agents.enqueue("Ext Agent")
            assert isinstance(handle, AgentRunHandle)
            run = await agents_mod.agents.get_run(run_id)
            assert isinstance(run, AgentRun)

        client.post.assert_awaited_once()
        client.get.assert_awaited_once()
