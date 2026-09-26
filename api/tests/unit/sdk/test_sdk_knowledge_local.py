"""Facade tests for ``bifrost.knowledge`` via ``BifrostClient.engine_request``."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


class TestEngineRequestFacade:
    """Gate C4c: the migrated knowledge facade rides ``engine_request``.

    Every fixed method sends its exact HTTP path, body, params, and retry
    flag through the shared client entry point, parses the same response
    shape, and maps a 404 miss to ``None`` — with no dedicated-channel
    frames and no silent HTTP fallback after a local failure.
    """

    def _client(self, responses):
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_all_seven_methods_call_engine_request(self):
        import httpx

        from bifrost.knowledge import knowledge

        doc = {
            "id": str(uuid4()), "namespace": "ns", "content": "hello",
            "metadata": {}, "score": 0.9,
            "organization_id": str(uuid4()), "key": "k",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        client = self._client([
            httpx.Response(200, json={"id": doc["id"]}),
            httpx.Response(200, json={"ids": [doc["id"]]}),
            httpx.Response(200, json=[doc]),
            httpx.Response(200, json={"deleted": True}),
            httpx.Response(200, json={"deleted_count": 2}),
            httpx.Response(200, json=[{"namespace": "ns", "scopes": {}}]),
            httpx.Response(200, json=doc),
        ])
        with (
            patch("bifrost.knowledge.get_client", return_value=client),
            patch("bifrost.knowledge.resolve_scope", lambda s: s),
        ):
            assert await knowledge.store(
                "hello", namespace="ns", key="k"
            ) == doc["id"]
            assert await knowledge.store_many(
                [{"content": "hi"}], namespace="ns"
            ) == [doc["id"]]
            results = await knowledge.search("hello", namespace="ns")
            assert [d.key for d in results] == ["k"]
            assert await knowledge.delete("k", namespace="ns") is True
            assert await knowledge.delete_namespace("ns") == 2
            namespaces = await knowledge.list_namespaces()
            assert [n.namespace for n in namespaces] == ["ns"]
            got = await knowledge.get("k", namespace="ns")
            assert got is not None and got.id == doc["id"]

        # Every call rode the shared client's engine-local entry point with
        # the ordinary HTTP verb and path — never a channel frame.
        calls = client.engine_request.await_args_list
        assert [(call.args[0], call.args[1]) for call in calls] == [
            ("POST", "/api/sdk/knowledge/store"),
            ("POST", "/api/sdk/knowledge/store-many"),
            ("POST", "/api/sdk/knowledge/search"),
            ("POST", "/api/sdk/knowledge/delete"),
            ("DELETE", "/api/sdk/knowledge/namespace/ns"),
            ("GET", "/api/sdk/knowledge/namespaces"),
            ("GET", "/api/sdk/knowledge/get"),
        ]
        # Bodies/params match the HTTP endpoints exactly.
        assert calls[0].kwargs["json"] == {
            "content": "hello", "namespace": "ns", "key": "k",
            "metadata": None, "scope": None,
        }
        assert calls[1].kwargs["json"] == {
            "documents": [{"content": "hi"}], "namespace": "ns",
            "scope": None,
        }
        assert calls[1].kwargs["timeout"] == 300.0
        assert calls[2].kwargs["json"]["namespace"] == ["ns"]
        assert calls[2].kwargs["retry_transient"] is True
        assert calls[3].kwargs["json"] == {
            "key": "k", "namespace": "ns", "scope": None,
        }
        assert calls[5].kwargs["params"] == {"include_global": True}
        assert calls[6].kwargs["params"] == {"key": "k", "namespace": "ns"}

    @pytest.mark.asyncio
    async def test_store_many_sends_one_realistic_batch(self):
        """A realistic batch is one request, not per-document frames."""
        import httpx

        from bifrost.knowledge import knowledge

        documents = [
            {"content": f"Document {i}", "key": f"doc-{i}",
             "metadata": {"index": i}}
            for i in range(50)
        ]
        client = self._client([
            httpx.Response(200, json={"ids": [f"id-{i}" for i in range(50)]}),
        ])
        with (
            patch("bifrost.knowledge.get_client", return_value=client),
            patch("bifrost.knowledge.resolve_scope", lambda s: s),
        ):
            ids = await knowledge.store_many(
                documents, namespace="faq", timeout=600.0
            )
        assert ids == [f"id-{i}" for i in range(50)]
        assert client.engine_request.await_count == 1
        call = client.engine_request.await_args
        assert call.args == ("POST", "/api/sdk/knowledge/store-many")
        assert call.kwargs["json"]["documents"] == documents
        assert call.kwargs["timeout"] == 600.0

    @pytest.mark.asyncio
    async def test_get_404_maps_to_none_without_fallback(self):
        import httpx

        from bifrost.knowledge import knowledge

        request = httpx.Request("GET", "http://engine/api/sdk/knowledge/get")
        client = self._client([
            httpx.Response(
                404, json={"detail": "Document not found"}, request=request
            ),
        ])
        with (
            patch("bifrost.knowledge.get_client", return_value=client),
            patch("bifrost.knowledge.resolve_scope", lambda s: s),
        ):
            assert await knowledge.get("absent", namespace="ns") is None
        assert client.engine_request.await_count == 1

    @pytest.mark.asyncio
    async def test_error_surfaces_without_channel(self):
        import httpx

        from bifrost.client import BifrostAuthorizationError
        from bifrost.knowledge import knowledge

        request = httpx.Request("POST", "http://engine/api/sdk/knowledge/store")
        client = self._client([
            httpx.Response(403, json={"detail": "denied"}, request=request),
        ])
        with (
            patch("bifrost.knowledge.get_client", return_value=client),
            patch("bifrost.knowledge.resolve_scope", lambda s: s),
        ):
            with pytest.raises(BifrostAuthorizationError):
                await knowledge.store("x", namespace="ns")
        assert client.engine_request.await_count == 1
