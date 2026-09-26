"""Execution reads over the shared ``BifrostClient.engine_request`` transport.

Covers the migrated surface that does not need a forked child:

- the SDK facades ride the shared ``BifrostClient.engine_request``
  transport with the exact HTTP verb/path/query, map results to
  the public surface (``WorkflowMetadata`` list, ``ExecutionList`` with
  continuation token, ``ValueError``/``PermissionError`` on get), preserve
  the existing ``workflow_id``-wins and limit-clamp behavior, and keep
  ``get_current_logs`` a direct Redis read.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def test_sdk_workflow_metadata_accepts_server_parameter_list():
    from bifrost.models import WorkflowMetadata as SdkWorkflowMetadata
    from src.models.contracts.workflows import (
        WorkflowMetadata as ServerWorkflowMetadata,
        WorkflowParameter,
    )

    server = ServerWorkflowMetadata(
        id=str(uuid4()),
        name="parameterized",
        parameters=[WorkflowParameter(name="count", type="int", required=True)],
        created_at=datetime.now(timezone.utc),
    )
    parsed = SdkWorkflowMetadata.model_validate(server.model_dump(mode="json"))
    assert parsed.parameters[0]["name"] == "count"


def _sdk_summary(**overrides):
    """One SDK-shaped execution dict, as the engine parent returns it."""
    data = {
        "execution_id": str(uuid4()),
        "workflow_name": "wf",
        "org_id": None,
        "form_id": None,
        "executed_by": str(uuid4()),
        "executed_by_name": "User",
        "status": "Success",
        "result_type": None,
        "error_message": None,
        "duration_ms": 3,
        "started_at": None,
        "completed_at": None,
        "scheduled_at": None,
        "created_at": None,
        "session_id": None,
        "peak_memory_bytes": None,
        "process_rss_bytes": None,
        "cpu_total_seconds": None,
    }
    data.update(overrides)
    return data


class TestEngineRequestFacade:
    """Gate C5a: the migrated workflow/execution facades ride ``engine_request``.

    Every fixed method sends its exact HTTP verb, path, body, and query
    through the shared client entry point and parses the same response
    shape — with no dedicated-channel frames, no explicit timeout override
    (the shared client's default applies), and no silent HTTP fallback.
    """

    def _client(self, responses):
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_workflows_list_rides_engine_request(self):
        import httpx

        from bifrost.workflows import workflows

        meta = {
            "id": str(uuid4()),
            "name": "local-wf",
            "description": None,
            "category": None,
            "tags": [],
            "parameters": [],
            "execution_mode": "sync",
            "timeout_seconds": 1800,
            "retry_policy": None,
            "endpoint_enabled": False,
            "allowed_methods": None,
            "disable_global_key": False,
            "public_endpoint": False,
            "is_tool": False,
            "tool_description": None,
            "time_saved": None,
            "source_file_path": None,
            "relative_file_path": None,
        }
        client = self._client([httpx.Response(200, json=[meta])])
        with patch("bifrost.workflows.get_client", return_value=client):
            result = await workflows.list()

        assert [w.name for w in result] == ["local-wf"]
        # timeout_seconds is preserved through the response model.
        assert result[0].timeout_seconds == 1800
        call = client.engine_request.await_args
        assert call.args == ("GET", "/api/workflows")
        assert call.kwargs == {}

    @pytest.mark.asyncio
    async def test_executions_list_forwards_exact_query(self):
        import httpx

        from bifrost.executions import executions

        client = self._client([
            httpx.Response(
                200,
                json={
                    "executions": [_sdk_summary()],
                    "continuation_token": "tok-1",
                },
            )
        ])
        with patch("bifrost.executions.get_client", return_value=client):
            result = await executions.list(
                workflow_id=str(uuid4()),
                workflow_name="ignored",
                status="Success",
                exclude_local=False,
                limit=5000,
                continuation_token="tok-0",
            )

        assert len(result) == 1
        assert result.continuation_token == "tok-1"
        call = client.engine_request.await_args
        assert call.args == ("GET", "/api/executions")
        params = call.kwargs["params"]
        # workflow_id wins over workflow_name; limit clamps to 1000.
        assert params["workflow_id"] is not None
        assert "workflow_name" not in params
        assert params["status"] == "Success"
        assert params["exclude_local"] == "false"
        assert params["limit"] == 1000
        assert params["continuation_token"] == "tok-0"
        assert "timeout" not in call.kwargs

    @pytest.mark.asyncio
    async def test_executions_list_defaults(self):
        import httpx

        from bifrost.executions import executions

        client = self._client([
            httpx.Response(
                200, json={"executions": [], "continuation_token": None}
            )
        ])
        with patch("bifrost.executions.get_client", return_value=client):
            result = await executions.list()

        assert result == [] and result.continuation_token is None
        params = client.engine_request.await_args.kwargs["params"]
        assert params == {"limit": 50}

    @pytest.mark.asyncio
    async def test_executions_get_rides_engine_request(self):
        import httpx

        from bifrost.executions import executions

        execution_id = str(uuid4())
        client = self._client([
            httpx.Response(200, json=_sdk_summary(execution_id=execution_id))
        ])
        with patch("bifrost.executions.get_client", return_value=client):
            detail = await executions.get(execution_id)

        assert detail.execution_id == execution_id
        assert client.engine_request.await_args.args == (
            "GET",
            f"/api/executions/{execution_id}",
        )

    @pytest.mark.asyncio
    async def test_workflows_get_delegates_to_executions_get(self):
        import httpx

        from bifrost.workflows import workflows

        execution_id = str(uuid4())
        client = self._client([
            httpx.Response(200, json=_sdk_summary(execution_id=execution_id))
        ])
        with patch("bifrost.executions.get_client", return_value=client):
            detail = await workflows.get(execution_id)

        assert detail.execution_id == execution_id
        assert client.engine_request.await_args.args == (
            "GET",
            f"/api/executions/{execution_id}",
        )

    @pytest.mark.asyncio
    async def test_get_error_mapping_matches_http(self):
        import httpx

        from bifrost.client import BifrostAPIError
        from bifrost.executions import executions

        request = httpx.Request("GET", "http://engine/api/executions/x")
        for status, expected in ((404, ValueError), (403, PermissionError)):
            client = self._client([
                httpx.Response(status, json={"detail": "no"}, request=request)
            ])
            with patch("bifrost.executions.get_client", return_value=client):
                with pytest.raises(expected):
                    await executions.get(str(uuid4()))
            assert client.engine_request.await_count == 1

        client = self._client([
            httpx.Response(500, json={"detail": "boom"}, request=request)
        ])
        with patch("bifrost.executions.get_client", return_value=client):
            with pytest.raises(BifrostAPIError):
                await executions.get(str(uuid4()))


class TestCurrentLogsDirectPath:
    """``get_current_logs`` stays a direct Redis read with no API transport."""

    @pytest.mark.asyncio
    async def test_reads_redis_without_engine_socket(self):
        from bifrost.executions import executions

        entries = [
            (
                "1-0",
                {
                    "execution_id": "exec-1",
                    "level": "INFO",
                    "message": "hello",
                    "metadata": '{"a": 1}',
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
            )
        ]

        class _FakeRedis:
            async def xrange(self, key, min, count):
                return entries

        @contextlib.asynccontextmanager
        async def _fake_get_redis():
            yield _FakeRedis()

        client = MagicMock()
        client.engine_request = AsyncMock(
            side_effect=AssertionError("get_current_logs must not call the API")
        )
        with (
            patch("src.core.cache.get_redis", _fake_get_redis),
            patch("bifrost.executions.get_client", return_value=client),
        ):
            logs = await executions.get_current_logs("exec-1")

        assert [log.message for log in logs] == ["hello"]
        assert logs[0].level == "INFO"
        assert logs[0].metadata == {"a": 1}
        client.engine_request.assert_not_awaited()
