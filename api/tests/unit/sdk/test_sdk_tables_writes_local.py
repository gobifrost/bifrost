"""Stage 3b: shared-client transport for the full ``tables`` facade writes.

The migrated facade methods now ride the shared
``BifrostClient.engine_request`` transport (the worker Unix socket inside an
engine child, the network API elsewhere): auto-create-once retries, 409
retry loops, and 404 mappings (``update``→None, ``delete_document``→False,
``delete_batch``→empty) without HTTP fallback.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest


def _doc(table_id, doc_id="d1", data=None):
    return {
        "id": doc_id, "table_id": table_id, "data": data or {"v": 1},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": "t", "updated_by": "t",
    }


def _table_info(name="t"):
    return {
        "id": str(uuid4()), "name": name, "organization_id": None,
        "table_schema": None, "description": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


class TestSharedClientTransport:
    """Gate C3b: the migrated writes ride the shared client.

    ``tables.insert/upsert/update/delete_document`` and the batch writes now
    call ``BifrostClient.engine_request`` (the worker Unix socket inside an
    engine child, the network API elsewhere). Route, body, retry,
    auto-create, and result mapping stay identical to the HTTP endpoints.
    """

    @pytest.fixture(autouse=True)
    def _clear_execution_context(self):
        from bifrost._context import clear_execution_context

        clear_execution_context()
        yield
        clear_execution_context()

    @staticmethod
    def _response(status: int, payload=None):
        import httpx

        return httpx.Response(
            status,
            json=payload,
            request=httpx.Request("POST", "http://api/test"),
        )

    @staticmethod
    def _client(responses):
        from unittest.mock import AsyncMock, MagicMock

        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_insert_auto_creates_once_over_shared_client(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client(
            [
                self._response(404, {"detail": "Table 't' not found"}),
                self._response(201, _table_info("t")),
                self._response(201, _doc(table_id, "d1")),
            ]
        )
        with patch("bifrost.tables.get_client", return_value=client):
            doc = await tables.insert("t", {"v": 1}, id="d1", scope="global")

        assert doc.id == "d1"
        calls = client.engine_request.await_args_list
        assert [(c.args[0], c.args[1]) for c in calls] == [
            ("POST", "/api/tables/t/documents?scope=global"),
            ("POST", "/api/tables?scope=global"),
            ("POST", "/api/tables/t/documents?scope=global"),
        ]
        assert calls[0].kwargs["json"] == {"id": "d1", "data": {"v": 1}}
        assert calls[1].kwargs["json"] == {"name": "t"}

    @pytest.mark.asyncio
    async def test_insert_in_solution_never_auto_creates(self):
        from bifrost._context import set_execution_context
        from bifrost.client import BifrostAPIError
        from bifrost.tables import tables
        from src.sdk.context import ExecutionContext

        ctx = ExecutionContext(
            user_id="u1", email="e@e.com", name="T", scope="org-1",
            organization=None, is_platform_admin=False,
            is_function_key=False, execution_id="exec-1",
        )
        ctx.solution_id = str(uuid4())
        set_execution_context(ctx)

        client = self._client(
            [self._response(404, {"detail": "Table 't' not found"})]
        )
        with patch("bifrost.tables.get_client", return_value=client):
            with pytest.raises(BifrostAPIError):
                await tables.insert("t", {"v": 1})
        # No auto-create retry inside a Solution.
        assert client.engine_request.await_count == 1

    @pytest.mark.asyncio
    async def test_upsert_uses_replace_route_with_transient_retry(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client([self._response(200, _doc(table_id, "k"))])
        with patch("bifrost.tables.get_client", return_value=client):
            doc = await tables.upsert("t", "k", {"v": 2}, scope="global")

        assert doc.id == "k"
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "POST", "/api/tables/t/documents/upsert?scope=global",
        )
        assert call.kwargs["json"] == {"id": "k", "data": {"v": 2}}
        assert call.kwargs["retry_transient"] is True

    @pytest.mark.asyncio
    async def test_update_maps_404_to_none_with_transient_retry(self):
        from bifrost.tables import tables

        client = self._client([self._response(404, {"detail": "not found"})])
        with patch("bifrost.tables.get_client", return_value=client):
            updated = await tables.update("t", "d1", {"a": 2}, scope="global")

        assert updated is None
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "PATCH", "/api/tables/t/documents/d1?scope=global",
        )
        assert call.kwargs["json"] == {"data": {"a": 2}}
        assert call.kwargs["retry_transient"] is True

    @pytest.mark.asyncio
    async def test_delete_document_maps_404_to_false(self):
        from bifrost.tables import tables

        client = self._client([self._response(404, {"detail": "not found"})])
        with patch("bifrost.tables.get_client", return_value=client):
            deleted = await tables.delete_document("t", "d1", scope="global")

        assert deleted is False
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "DELETE", "/api/tables/t/documents/d1?scope=global",
        )
        assert call.kwargs == {}

    @pytest.mark.asyncio
    async def test_insert_batch_posts_batch_without_transient_retry(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client(
            [self._response(201, {"inserted": 1, "documents": [_doc(table_id, "a")]})]
        )
        with patch("bifrost.tables.get_client", return_value=client):
            result = await tables.insert_batch(
                "t", [{"id": "a", "data": {"v": 1}}], scope="global"
            )

        assert result.count == 1
        assert result.documents[0].id == "a"
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "POST", "/api/tables/t/documents/batch?scope=global",
        )
        assert call.kwargs["json"] == {
            "documents": [{"id": "a", "data": {"v": 1}}],
            "upsert": False,
        }
        assert call.kwargs["retry_transient"] is False

    @pytest.mark.asyncio
    async def test_upsert_batch_retries_transient_with_explicit_ids(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client(
            [self._response(200, {"inserted": 1, "documents": [_doc(table_id, "a")]})]
        )
        with patch("bifrost.tables.get_client", return_value=client):
            result = await tables.upsert_batch(
                "t", [{"id": "a", "data": {"v": 1}}], scope="global"
            )

        assert result.count == 1
        call = client.engine_request.await_args_list[0]
        assert call.kwargs["json"]["upsert"] is True
        assert call.kwargs["retry_transient"] is True

    @pytest.mark.asyncio
    async def test_insert_batch_404_auto_creates_once(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client(
            [
                self._response(404, {"detail": "Table 't' not found"}),
                self._response(201, _table_info("t")),
                self._response(
                    201, {"inserted": 1, "documents": [_doc(table_id, "a")]}
                ),
            ]
        )
        with patch("bifrost.tables.get_client", return_value=client):
            result = await tables.insert_batch(
                "t", [{"id": "a", "data": {"v": 1}}], scope="global"
            )

        assert result.count == 1
        calls = client.engine_request.await_args_list
        assert [(c.args[0], c.args[1]) for c in calls] == [
            ("POST", "/api/tables/t/documents/batch?scope=global"),
            ("POST", "/api/tables?scope=global"),
            ("POST", "/api/tables/t/documents/batch?scope=global"),
        ]

    @pytest.mark.asyncio
    async def test_bulk_upsert_retries_bounded_409_count_only(self):
        from bifrost.tables import tables

        client = self._client(
            [
                self._response(409, {"detail": "conflict"}),
                self._response(200, {"inserted": 2, "documents": []}),
            ]
        )
        with patch("bifrost.tables.get_client", return_value=client):
            result = await tables.bulk_upsert(
                "t",
                [{"id": "a", "data": {}}, {"id": "b", "data": {}}],
                scope="global",
                conflict_retries=1,
            )

        assert result.count == 2
        calls = client.engine_request.await_args_list
        assert len(calls) == 2
        assert calls[0].args == (
            "POST", "/api/tables/t/documents/batch?scope=global",
        )
        assert calls[0].kwargs["json"]["write_mode"] == "replace_upsert"
        assert calls[0].kwargs["json"]["return_documents"] is False
        assert "retry_transient" not in calls[0].kwargs

    @pytest.mark.asyncio
    async def test_bulk_upsert_404_auto_creates_then_retries(self):
        from bifrost.tables import tables

        client = self._client(
            [
                self._response(404, {"detail": "Table 't' not found"}),
                self._response(201, _table_info("t")),
                self._response(200, {"inserted": 1, "documents": []}),
            ]
        )
        with patch("bifrost.tables.get_client", return_value=client):
            result = await tables.bulk_upsert(
                "t", [{"id": "a", "data": {}}], scope="global"
            )

        assert result.count == 1
        calls = client.engine_request.await_args_list
        assert [(c.args[0], c.args[1]) for c in calls] == [
            ("POST", "/api/tables/t/documents/batch?scope=global"),
            ("POST", "/api/tables?scope=global"),
            ("POST", "/api/tables/t/documents/batch?scope=global"),
        ]

    @pytest.mark.asyncio
    async def test_delete_batch_maps_404_to_empty(self):
        from bifrost.tables import tables

        client = self._client([self._response(404, {"detail": "not found"})])
        with patch("bifrost.tables.get_client", return_value=client):
            result = await tables.delete_batch("t", ["a"], scope="global")

        assert result.deleted_ids == [] and result.count == 0
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "POST", "/api/tables/t/documents/batch-delete?scope=global",
        )
        assert call.kwargs["json"] == {"ids": ["a"]}

    @pytest.mark.asyncio
    async def test_delete_batch_maps_response(self):
        from bifrost.tables import tables

        client = self._client(
            [self._response(200, {"deleted": 1, "deleted_ids": ["a"]})]
        )
        with patch("bifrost.tables.get_client", return_value=client):
            result = await tables.delete_batch("t", ["a", "ghost"], scope="global")

        assert result.deleted_ids == ["a"] and result.count == 1
