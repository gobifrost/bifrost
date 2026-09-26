"""Migrated ``bifrost.agents`` facade tests over ``engine_request``.

``enqueue`` posts the exact HTTP body, ``get_run`` reads the exact HTTP
detail path, and errors surface as the same public exceptions as the HTTP
path — with no dedicated-channel frames and no silent HTTP fallback.
"""

from __future__ import annotations

import asyncio
import importlib as _importlib
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _agent_run_body(run_id, status="completed", output=None):
    body = {
        "id": run_id,
        "agent_id": str(uuid4()),
        "trigger_type": "api",
        "status": status,
        "iterations_used": 0,
        "tokens_used": 0,
        "metadata": {},
        "created_at": "2026-09-01T12:00:00+00:00",
    }
    if output is not None:
        body["output"] = output
    return body


@pytest.mark.asyncio
class TestEngineRequestFacade:
    """Gate C5b: the migrated agent facade rides ``engine_request``.

    ``enqueue`` posts the exact HTTP body, ``get_run`` reads the exact HTTP
    detail path, both keep the shared client's default timeout with no
    override, and errors surface as the same public exceptions as the HTTP
    path — with no dedicated-channel frames and no silent HTTP fallback.
    """

    def _client(self, responses):
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    async def test_enqueue_posts_exact_body_and_returns_handle(self):
        import httpx

        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost.models import AgentRunHandle

        run_id = str(uuid4())
        client = self._client(
            [httpx.Response(202, json={"run_id": run_id, "status": "queued"})]
        )
        with patch.object(agents_mod, "get_client", return_value=client):
            handle = await agents_mod.agents.enqueue(
                "Local Agent", {"a": 1}, output_schema={"type": "object"}
            )

        assert isinstance(handle, AgentRunHandle)
        assert handle.run_id == run_id
        call = client.engine_request.await_args
        assert call.args == ("POST", "/api/agent-runs/enqueue")
        assert call.kwargs["json"] == {
            "agent_name": "Local Agent",
            "input": {"a": 1},
            "output_schema": {"type": "object"},
        }
        assert "timeout" not in call.kwargs

    async def test_enqueue_paused_maps_to_typed_error(self):
        import httpx

        agents_mod = _importlib.import_module("bifrost.agents")

        agent_id = str(uuid4())
        client = self._client(
            [
                httpx.Response(
                    200,
                    json={
                        "status": "paused",
                        "accepted": False,
                        "message": "Agent 'P' is paused. Request not processed.",
                        "agent_id": agent_id,
                    },
                )
            ]
        )
        with patch.object(agents_mod, "get_client", return_value=client):
            with pytest.raises(agents_mod.AgentPausedError) as exc_info:
                await agents_mod.agents.enqueue("P")

        assert exc_info.value.agent_id == agent_id

    async def test_get_run_reads_exact_path_and_returns_model(self):
        import httpx

        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost.models import AgentRun

        run_id = str(uuid4())
        client = self._client(
            [httpx.Response(200, json=_agent_run_body(run_id))]
        )
        with patch.object(agents_mod, "get_client", return_value=client):
            run = await agents_mod.agents.get_run(run_id)

        assert isinstance(run, AgentRun)
        call = client.engine_request.await_args
        assert call.args == ("GET", f"/api/agent-runs/{run_id}")
        assert call.kwargs == {}

    async def test_get_run_404_and_403_map_to_public_errors(self):
        import httpx

        agents_mod = _importlib.import_module("bifrost.agents")

        request = httpx.Request("GET", "http://engine/api/agent-runs/x")
        for status, exc_type in ((404, ValueError), (403, PermissionError)):
            client = self._client(
                [httpx.Response(status, json={"detail": "no"}, request=request)]
            )
            with patch.object(agents_mod, "get_client", return_value=client):
                with pytest.raises(exc_type):
                    await agents_mod.agents.get_run(str(uuid4()))

    async def test_enqueue_error_statuses_surface_without_channel(self):
        import httpx

        from bifrost.client import BifrostAPIError

        agents_mod = _importlib.import_module("bifrost.agents")
        request = httpx.Request("POST", "http://engine/api/agent-runs/enqueue")
        for status in (400, 409, 500):
            client = self._client(
                [httpx.Response(status, json={"detail": "denied"}, request=request)]
            )
            with patch.object(agents_mod, "get_client", return_value=client):
                with pytest.raises(BifrostAPIError) as exc_info:
                    await agents_mod.agents.enqueue("A")
            assert exc_info.value.response.status_code == status

    async def test_run_composes_enqueue_and_wait(self):
        import httpx

        agents_mod = _importlib.import_module("bifrost.agents")

        run_id = str(uuid4())
        client = self._client(
            [
                httpx.Response(202, json={"run_id": run_id, "status": "queued"}),
                httpx.Response(
                    200,
                    json=_agent_run_body(
                        run_id, status="completed", output={"text": "done"}
                    ),
                ),
            ]
        )
        with (
            patch.object(agents_mod, "get_client", return_value=client),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            result = await agents_mod.agents.run("Local Agent", timeout=5.0)

        assert result == "done"
        assert client.engine_request.await_count == 2

    async def test_wait_timeout_returns_pending_without_reading(self):
        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost.models import AgentRunPending

        run_id = str(uuid4())
        client = self._client([])
        with patch.object(agents_mod, "get_client", return_value=client):
            pending = await agents_mod.agents.wait(run_id, timeout=0.0)

        assert isinstance(pending, AgentRunPending)
        assert pending.run_id == run_id
        assert pending.reason == "wait_timeout"
        assert pending.last_known_status is None
        client.engine_request.assert_not_awaited()

    async def test_wait_short_timeout_cancels_socket_read(self):
        """A short wait timeout cancels the in-flight HTTPX status read.

        The removed dedicated channel had to let a timed-out read finish so
        its response could not poison the next call; ordinary HTTPX request
        cancellation makes that workaround unnecessary.
        """
        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost.models import AgentRunPending

        run_id = str(uuid4())
        cancelled = asyncio.Event()

        async def _slow_read(*args, **kwargs):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=_slow_read)
        with patch.object(agents_mod, "get_client", return_value=client):
            pending = await agents_mod.agents.wait(run_id, timeout=0.05)

        assert isinstance(pending, AgentRunPending)
        assert pending.reason == "wait_timeout"
        assert cancelled.is_set(), "the socket read must be cancelled"

    async def test_wait_respects_workflow_deadline(self):
        from types import SimpleNamespace
        from datetime import datetime, timedelta, timezone

        agents_mod = _importlib.import_module("bifrost.agents")
        from bifrost.models import AgentRunPending

        run_id = str(uuid4())
        client = self._client([])
        token = agents_mod._execution_context.set(
            SimpleNamespace(
                workflow_deadline=datetime.now(timezone.utc) + timedelta(seconds=5),
                workflow_timeout_seconds=60,
            )
        )
        try:
            with patch.object(agents_mod, "get_client", return_value=client):
                pending = await agents_mod.agents.wait(run_id)
        finally:
            agents_mod._execution_context.reset(token)

        assert isinstance(pending, AgentRunPending)
        assert pending.run_id == run_id
        assert pending.reason == "workflow_deadline"
        client.engine_request.assert_not_awaited()
