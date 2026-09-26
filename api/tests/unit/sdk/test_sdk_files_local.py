"""Migrated ``bifrost.files`` facade tests (Gate C4a).

Every fixed files method goes through ``BifrostClient.engine_request``
(the worker Unix socket inside an engine child, the network API
elsewhere) and never touches a dedicated channel. Text/base64 encoding,
request paths and bodies, and public error mapping stay identical to the
HTTP endpoints.
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFilesSharedClientTransport:
    """Gate C4a: the migrated facade rides the shared client, not the channel."""

    def _client(self, responses):
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @staticmethod
    def _stat_doc():
        return {
            "path": "a.txt", "exists": True, "version": "sha256:abc",
            "size": 5, "last_modified": None, "updated_by": "t",
        }

    @staticmethod
    def _search_doc():
        return {
            "query": "TODO", "total_matches": 1, "files_searched": 2,
            "results": [], "truncated": False, "search_time_ms": 3,
        }

    @pytest.mark.asyncio
    async def test_all_methods_use_shared_client_not_channel(self):
        import httpx

        from bifrost.files import files

        stat_doc = self._stat_doc()
        search_doc = self._search_doc()
        client = self._client([
            httpx.Response(200, json={"content": "hello", "binary": False}),
            httpx.Response(
                200,
                json={
                    "content": base64.b64encode(b"hello").decode(),
                    "binary": True,
                },
            ),
            httpx.Response(204),
            httpx.Response(204),
            httpx.Response(200, json={"files": ["a.txt"]}),
            httpx.Response(204),
            httpx.Response(200, json={"exists": True}),
            httpx.Response(200, json=stat_doc),
            httpx.Response(
                200,
                json={"url": "https://s3/x", "path": "k", "expires_in": 600},
            ),
            httpx.Response(200, json=search_doc),
        ])
        with patch("bifrost.files.get_client", return_value=client):
            assert await files.read("a.txt") == "hello"
            assert await files.read_bytes("a.bin") == b"hello"
            await files.write("a.txt", "hello")
            await files.write_bytes("a.bin", b"hello")
            assert await files.list("") == ["a.txt"]
            await files.delete("a.txt")
            assert await files.exists("a.txt") is True
            assert await files.stat("a.txt") == stat_doc
            signed = await files.get_signed_url("a.txt")
            assert signed["url"] == "https://s3/x"
            assert signed["expires_in"] == 600
            hits = await files.search("TODO")
            assert hits["total_matches"] == 1

        # The migrated facade never reads a channel frame: every call went
        # through the shared client's engine-local entry point.
        calls = client.engine_request.await_args_list
        assert [(call.args[0], call.args[1]) for call in calls] == [
            ("POST", "/api/files/read"),
            ("POST", "/api/files/read"),
            ("POST", "/api/files/write"),
            ("POST", "/api/files/write"),
            ("POST", "/api/files/list"),
            ("POST", "/api/files/delete"),
            ("POST", "/api/files/exists"),
            ("POST", "/api/files/stat"),
            ("POST", "/api/files/signed-url"),
            ("POST", "/api/files/search"),
        ]
        # read_bytes rides files.read with binary=true; write_bytes rides
        # files.write with binary=true and base64 content.
        assert calls[0].kwargs["json"]["binary"] is False
        assert calls[1].kwargs["json"]["binary"] is True
        assert calls[3].kwargs["json"]["binary"] is True
        assert calls[3].kwargs["json"]["content"] == base64.b64encode(
            b"hello"
        ).decode()
        assert calls[9].kwargs["json"]["query"] == "TODO"

    @pytest.mark.asyncio
    async def test_large_binary_roundtrip_through_engine_request(self):
        import httpx

        from bifrost.files import files

        payload = bytes((i * 7) % 256 for i in range(2 * 1024 * 1024))
        encoded = base64.b64encode(payload).decode()
        client = self._client([
            httpx.Response(204),
            httpx.Response(200, json={"content": encoded, "binary": True}),
        ])
        with patch("bifrost.files.get_client", return_value=client):
            await files.write_bytes("big.bin", payload)
            assert await files.read_bytes("big.bin") == payload
        assert client.engine_request.await_args_list[0].kwargs["json"][
            "content"
        ] == encoded

    @pytest.mark.asyncio
    async def test_error_status_surfaces_without_channel(self):
        import httpx

        from bifrost.client import BifrostAPIError, BifrostAuthorizationError
        from bifrost.files import files

        client = self._client([
            httpx.Response(
                404,
                json={"detail": "not found"},
                request=httpx.Request("POST", "http://api/api/files/read"),
            ),
            httpx.Response(
                403,
                json={"detail": "denied"},
                request=httpx.Request("POST", "http://api/api/files/write"),
            ),
        ])
        with patch("bifrost.files.get_client", return_value=client):
            with pytest.raises(BifrostAPIError):
                await files.read("ghost.txt")
            with pytest.raises(BifrostAuthorizationError):
                await files.write("a.txt", "x")
            assert client.engine_request.await_count == 2
