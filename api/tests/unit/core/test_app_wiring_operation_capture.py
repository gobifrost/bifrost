"""Unit tests for R2a-2's post-routing operation-id capture and the
per-workflow catalog-operation-usage counter (src/core/app_wiring.py).
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from src.core.app_wiring import (
    _capture_operation_id,
    _engine_workflow_id,
    _record_workflow_operation_usage,
)
from src.services.audit_context import ActorContext, clear_actor, set_actor


@dataclass
class _FakeRoute:
    operation_id: str | None
    path: str


def _request(*, method: str = "GET", route: _FakeRoute | None) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "headers": [],
        "route": route,
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _reset_actor_and_workflow_id():
    clear_actor()
    token = _engine_workflow_id.set(None)
    yield
    clear_actor()
    _engine_workflow_id.reset(token)


class TestCaptureOperationId:
    @pytest.mark.asyncio
    async def test_catalogued_route_stamps_operation_id(self):
        set_actor(ActorContext(user_id=None, organization_id=None))
        route = _FakeRoute(operation_id="agents.list", path="/api/agents")

        await _capture_operation_id(_request(route=route))

        from src.services.audit_context import current_actor

        actor = current_actor()
        assert actor is not None
        assert actor.operation_id == "agents.list"

    @pytest.mark.asyncio
    async def test_uncatalogued_route_leaves_operation_id_none(self):
        set_actor(ActorContext(user_id=None, organization_id=None))
        route = _FakeRoute(operation_id=None, path="/api/whatever")

        await _capture_operation_id(_request(route=route))

        from src.services.audit_context import current_actor

        actor = current_actor()
        assert actor is not None
        assert actor.operation_id is None

    @pytest.mark.asyncio
    async def test_no_route_is_a_no_op(self):
        set_actor(ActorContext(user_id=None, organization_id=None))

        await _capture_operation_id(_request(route=None))

        from src.services.audit_context import current_actor

        assert current_actor().operation_id is None


class TestWorkflowOperationUsageIncrement:
    @pytest.mark.asyncio
    async def test_catalogued_operation_uses_operation_id_as_key(self):
        set_actor(ActorContext(user_id=None, organization_id=None))
        _engine_workflow_id.set("wf-1")
        route = _FakeRoute(operation_id="config.get", path="/api/sdk/config/{key}")

        fake_pipe = MagicMock()
        fake_pipe.execute = AsyncMock()
        fake_conn = MagicMock()
        fake_conn.pipeline = MagicMock(return_value=fake_pipe)
        fake_redis_client = MagicMock()
        fake_redis_client._get_redis = AsyncMock(return_value=fake_conn)

        with patch(
            "src.core.redis_client.get_redis_client",
            return_value=fake_redis_client,
        ):
            await _capture_operation_id(_request(route=route))

        fake_pipe.hincrby.assert_called_once()
        (key, field, amount), _ = fake_pipe.hincrby.call_args
        assert key.startswith("bifrost:wf_usage:")
        assert field == "wf-1|config.get"
        assert amount == 1
        fake_pipe.expire.assert_called_once()
        fake_pipe.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uncatalogued_route_uses_method_and_path_template(self):
        set_actor(ActorContext(user_id=None, organization_id=None))
        _engine_workflow_id.set("wf-2")
        route = _FakeRoute(operation_id=None, path="/api/sdk/tables/{table_id}")

        fake_pipe = MagicMock()
        fake_pipe.execute = AsyncMock()
        fake_conn = MagicMock()
        fake_conn.pipeline = MagicMock(return_value=fake_pipe)
        fake_redis_client = MagicMock()
        fake_redis_client._get_redis = AsyncMock(return_value=fake_conn)

        with patch(
            "src.core.redis_client.get_redis_client",
            return_value=fake_redis_client,
        ):
            await _capture_operation_id(
                _request(method="GET", route=route)
            )

        (_, field, _), _ = fake_pipe.hincrby.call_args
        assert field == "wf-2|GET /api/sdk/tables/{table_id}"

    @pytest.mark.asyncio
    async def test_no_workflow_id_skips_increment_entirely(self):
        set_actor(ActorContext(user_id=None, organization_id=None))
        route = _FakeRoute(operation_id="agents.list", path="/api/agents")

        with patch("src.core.redis_client.get_redis_client") as get_redis:
            await _capture_operation_id(_request(route=route))

        get_redis.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_raise(self):
        """A Redis outage must never fail (or slow) the request it tags."""
        with patch(
            "src.core.redis_client.get_redis_client",
            side_effect=RuntimeError("redis is down"),
        ):
            # Must not raise.
            await _record_workflow_operation_usage("wf-3", "agents.list")
