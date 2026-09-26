"""Stage 3a: shared-client transport for ``tables.get/query/count``.

The migrated facade methods (``get/query/count``) ride the shared
``BifrostClient.engine_request`` transport (the worker Unix socket inside an
engine child, the network API elsewhere), map 404s to the facade's
method-specific results, and compose filtered counts through
``tables.query``.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest


class TestSharedClientTransport:
    """Gate C3a: the migrated reads ride the shared client.

    ``tables.get/query/count`` now go through
    ``BifrostClient.engine_request`` (the worker Unix socket inside an engine
    child, the network API elsewhere). The extracted document, 404, and
    error mapping stays identical to the HTTP endpoints.
    """

    def _client(self, responses):
        from unittest.mock import AsyncMock, MagicMock

        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_get_query_count_use_shared_client_not_channel(self):
        import httpx

        from bifrost._context import (
            clear_execution_context,
            set_execution_context,
        )
        from bifrost.tables import tables
        from src.sdk.context import ExecutionContext

        table_id = str(uuid4())
        doc = {
            "id": "d1", "table_id": table_id, "data": {"v": 1},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "created_by": "t", "updated_by": "t",
        }
        document_list = {
            "table_id": table_id, "documents": [doc],
            "total": 1, "limit": 100, "offset": 0,
        }
        client = self._client([
            httpx.Response(200, json=doc),
            httpx.Response(200, json=document_list),
            httpx.Response(200, json={"count": 7}),
            httpx.Response(200, json=document_list),
        ])
        set_execution_context(
            ExecutionContext(
                user_id="u1", email="e@e.com", name="T", scope="org-1",
                organization=None, is_platform_admin=False,
                is_function_key=False, execution_id="exec-1",
            )
        )
        try:
            with patch("bifrost.tables.get_client", return_value=client):
                got = await tables.get("t", "d1")
                assert got is not None and got.id == "d1"
                queried = await tables.query("t", where={"v": 1})
                assert queried.total == 1
                assert [d.id for d in queried.documents] == ["d1"]
                assert await tables.count("t") == 7
                assert await tables.count("t", where={"v": 1}) == 1
        finally:
            clear_execution_context()
        # Every call went through the shared client's engine-local entry
        # point.
        assert [
            (call.args[0], call.args[1])
            for call in client.engine_request.await_args_list
        ] == [
            ("GET", "/api/tables/t/documents/d1"),
            ("POST", "/api/tables/t/documents/query"),
            ("GET", "/api/tables/t/documents/count"),
            ("POST", "/api/tables/t/documents/query"),
        ]
        # Filtered count composes through query(limit=1): no count frame.
        assert (
            client.engine_request.await_args_list[3].kwargs["json"]["limit"] == 1
        )

    @pytest.mark.asyncio
    async def test_local_404_maps_to_method_results_without_http(self):
        import httpx

        from bifrost.tables import tables

        client = self._client([
            httpx.Response(404, json={"detail": "not found"}),
            httpx.Response(404, json={"detail": "not found"}),
            httpx.Response(404, json={"detail": "not found"}),
        ])
        with patch("bifrost.tables.get_client", return_value=client):
            assert await tables.get("ghost", "d1") is None
            queried = await tables.query("ghost")
            assert queried.documents == [] and queried.total == 0
            assert await tables.count("ghost") == 0
        assert client.engine_request.await_count == 3

    @pytest.mark.asyncio
    async def test_error_status_surfaces_without_channel(self):
        import httpx

        from bifrost.client import BifrostAPIError, BifrostAuthorizationError
        from bifrost.tables import tables

        client = self._client([
            httpx.Response(
                403, json={"detail": "denied"},
                request=httpx.Request(
                    "GET", "http://api/api/tables/t/documents/d1"
                ),
            ),
            httpx.Response(
                500, json={"detail": "boom"},
                request=httpx.Request(
                    "POST", "http://api/api/tables/t/documents/query"
                ),
            ),
        ])
        with patch("bifrost.tables.get_client", return_value=client):
            with pytest.raises(BifrostAuthorizationError):
                await tables.get("t", "d1")
            with pytest.raises(BifrostAPIError):
                await tables.query("t")
        assert client.engine_request.await_count == 2
