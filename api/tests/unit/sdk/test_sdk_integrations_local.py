"""Facade tests for ``integrations`` reads via ``BifrostClient.engine_request``."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest


class TestFacadeEngineRequest:
    """The facade sends fixed operations through ``BifrostClient.engine_request``.

    The shared client owns transport selection (worker socket vs network),
    retry, error mapping, and the no-failure-replay contract; the facade only
    shapes the HTTP request. The dedicated-channel round trips these tests
    used to drive were replaced by the real socket round trips in
    ``tests/unit/services/test_worker_sdk_http.py`` and
    ``tests/unit/execution/test_worker_sdk_http_fork.py``.
    """

    @pytest.mark.asyncio
    async def test_get_uses_engine_request_with_scope_and_solution(self):
        from bifrost._context import (
            clear_execution_context,
            set_execution_context,
        )
        from bifrost.integrations import integrations
        from src.sdk.context import ExecutionContext, Organization

        ctx = ExecutionContext(
            user_id="u1", email="e@e.com", name="T", scope="org-1",
            organization=Organization(id="org-1", name="Org One"),
            is_platform_admin=False, is_function_key=False,
            execution_id="exec-1", solution_id="sol-9",
        )
        set_execution_context(ctx)
        try:
            response = httpx.Response(
                200,
                json={
                    "integration_id": "iid",
                    "entity_id": "tenant-1",
                    "entity_name": None,
                    "config": {"api_key": "shh-value"},
                    "oauth": None,
                    "config_secret_keys": ["api_key"],
                },
                request=httpx.Request(
                    "POST", "http://api/api/sdk/integrations/get"
                ),
            )
            client = AsyncMock()
            client.engine_request = AsyncMock(return_value=response)
            with patch("bifrost.integrations.get_client", return_value=client):
                data = await integrations.get("P")

            assert data is not None
            assert data.entity_id == "tenant-1"
            assert "shh-value" in ctx._collect_secret_values()
            client.engine_request.assert_awaited_once_with(
                "POST",
                "/api/sdk/integrations/get",
                json={"name": "P", "scope": "org-1", "solution": "sol-9"},
                retry_transient=True,
            )
        finally:
            clear_execution_context()

    @pytest.mark.asyncio
    async def test_mapping_reads_use_engine_request(self):
        from bifrost.integrations import integrations

        item = {
            "id": "mid", "integration_id": "iid", "organization_id": "oid",
            "entity_id": "ent-1", "entity_name": None, "oauth_token_id": None,
            "config": {}, "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        client = AsyncMock()
        client.engine_request = AsyncMock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"items": [item]},
                    request=httpx.Request(
                        "POST",
                        "http://api/api/sdk/integrations/list_mappings",
                    ),
                ),
                httpx.Response(
                    200,
                    json=item,
                    request=httpx.Request(
                        "POST", "http://api/api/sdk/integrations/get_mapping"
                    ),
                ),
            ]
        )
        with patch("bifrost.integrations.get_client", return_value=client):
            mappings = await integrations.list_mappings("P")
            mapping = await integrations.get_mapping("P")

        assert mappings is not None
        assert [m.entity_id for m in mappings] == ["ent-1"]
        assert mapping is not None
        assert mapping.entity_id == "ent-1"
        assert [
            call.args[1] for call in client.engine_request.await_args_list
        ] == [
            "/api/sdk/integrations/list_mappings",
            "/api/sdk/integrations/get_mapping",
        ]

    @pytest.mark.asyncio
    async def test_missing_returns_none_through_engine_request(self):
        from bifrost.integrations import integrations

        client = AsyncMock()
        client.engine_request = AsyncMock(
            return_value=httpx.Response(
                200,
                content=b"null",
                headers={"content-type": "application/json"},
                request=httpx.Request(
                    "POST", "http://api/api/sdk/integrations/get"
                ),
            )
        )
        with patch("bifrost.integrations.get_client", return_value=client):
            assert await integrations.get("Missing") is None
            assert await integrations.list_mappings("Missing") is None
            assert await integrations.get_mapping("Missing") is None
        assert client.engine_request.await_count == 3

    @pytest.mark.asyncio
    async def test_error_response_surfaces_without_replay(self):
        from bifrost.client import BifrostAPIError
        from bifrost.integrations import integrations

        client = AsyncMock()
        client.engine_request = AsyncMock(
            return_value=httpx.Response(
                424,
                json={"detail": "declared connection missing"},
                request=httpx.Request(
                    "POST", "http://api/api/sdk/integrations/get"
                ),
            )
        )
        with patch("bifrost.integrations.get_client", return_value=client):
            with pytest.raises(BifrostAPIError):
                await integrations.get("P")
        # One attempt: a local failure is never replayed over another path.
        assert client.engine_request.await_count == 1
