"""``bifrost.workflows.execute`` / ``cancel`` over ``engine_request``.

Covers the migrated surface that does not need a forked child:

- the SDK facade rides the shared ``BifrostClient.engine_request``
  transport, posting the exact HTTP execute/cancel paths and
  bodies, mapping results to the public surface (execution-id string,
  ``None`` on cancel), and preserving the existing exception mapping and
  client-side ``scheduled_at``/``delay_seconds`` validation;
- external callers (no socket) keep the network HTTP path unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


class TestEngineRequestFacade:
    """Gate C5a: the migrated workflow mutations ride ``engine_request``.

    ``execute`` posts the exact HTTP body (fire-and-forget ``sync=False``),
    ``cancel`` posts the exact HTTP cancel path, both keep the shared
    client's default timeout with no override, and errors surface as the
    same public exceptions as the HTTP path — with no dedicated-channel
    frames and no silent HTTP fallback.
    """

    def _client(self, responses):
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_execute_posts_exact_body_and_returns_id(self):
        import httpx

        from bifrost.workflows import workflows

        client = self._client([
            httpx.Response(200, json={"execution_id": "exec-http-1"})
        ])
        with patch("bifrost.workflows.get_client", return_value=client):
            eid = await workflows.execute("workflows/child.py::main", {"x": 1})

        assert eid == "exec-http-1"
        call = client.engine_request.await_args
        assert call.args == ("POST", "/api/workflows/execute")
        assert call.kwargs["json"]["workflow_id"] == "workflows/child.py::main"
        assert call.kwargs["json"]["input_data"] == {"x": 1}
        assert call.kwargs["json"]["sync"] is False
        assert "timeout" not in call.kwargs

    @pytest.mark.asyncio
    async def test_execute_forwards_scope_solution_and_schedule(self):
        import httpx

        from bifrost._context import clear_execution_context, set_execution_context
        from bifrost._execution_context import ExecutionContext, Organization
        from bifrost.workflows import workflows

        org_id = str(uuid4())
        solution_id = str(uuid4())
        ctx = ExecutionContext(
            user_id="u",
            email="u@example.com",
            name="User",
            scope=org_id,
            organization=Organization(id=org_id, name="Org"),
            is_platform_admin=False,
            is_function_key=False,
            execution_id="exec",
            solution_id=solution_id,
        )
        set_execution_context(ctx)
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        client = self._client([
            httpx.Response(200, json={"execution_id": "exec-http-2"})
        ])
        try:
            with patch("bifrost.workflows.get_client", return_value=client):
                eid = await workflows.execute(
                    "wf",
                    {"x": 1},
                    run_as="22222222-2222-2222-2222-222222222222",
                    scheduled_at=run_at,
                )
        finally:
            clear_execution_context()

        assert eid == "exec-http-2"
        payload = client.engine_request.await_args.kwargs["json"]
        assert payload["org_id"] == org_id
        assert payload["solution_id"] == solution_id
        assert payload["caller_solution_id"] == solution_id
        assert payload["run_as"] == "22222222-2222-2222-2222-222222222222"
        assert payload["scheduled_at"] == run_at.isoformat()
        assert "delay_seconds" not in payload

    @pytest.mark.asyncio
    async def test_execute_client_validation_runs_before_request(self):
        from bifrost.workflows import workflows

        client = self._client([])
        run_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        with patch("bifrost.workflows.get_client", return_value=client):
            with pytest.raises(ValueError, match="mutually exclusive"):
                await workflows.execute(
                    "wf", scheduled_at=run_at, delay_seconds=60
                )
            with pytest.raises(ValueError, match="timezone"):
                await workflows.execute(
                    "wf", scheduled_at=datetime.now() + timedelta(minutes=5)
                )
        client.engine_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_posts_exact_path_and_returns_none(self):
        import httpx

        from bifrost.workflows import workflows

        client = self._client([
            httpx.Response(
                200,
                json={"execution_id": "exec-1", "status": "Cancelled"},
            )
        ])
        with patch("bifrost.workflows.get_client", return_value=client):
            assert await workflows.cancel("exec-1") is None

        call = client.engine_request.await_args
        assert call.args == ("POST", "/api/workflows/executions/exec-1/cancel")
        assert call.kwargs == {}

    @pytest.mark.asyncio
    async def test_error_statuses_surface_without_channel(self):
        import httpx

        from bifrost.client import BifrostAPIError
        from bifrost.workflows import workflows

        request = httpx.Request("POST", "http://engine/api/workflows/execute")
        for op, status in (
            ("execute", 404),
            ("execute", 403),
            ("cancel", 404),
            ("cancel", 403),
            ("cancel", 409),
        ):
            client = self._client([
                httpx.Response(status, json={"detail": "denied"}, request=request)
            ])
            with patch("bifrost.workflows.get_client", return_value=client):
                with pytest.raises(BifrostAPIError) as exc_info:
                    if op == "execute":
                        await workflows.execute("wf")
                    else:
                        await workflows.cancel(str(uuid4()))
            assert exc_info.value.response.status_code == status
