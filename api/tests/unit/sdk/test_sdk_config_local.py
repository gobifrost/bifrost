"""Config facade over the shared ``BifrostClient.engine_request`` transport.

The shared client entry point the facade uses (``engine_request``)
parses null/missing values and surfaces errors instead of swallowing them.
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestEngineRequestFacade:
    @pytest.mark.asyncio
    async def test_missing_key_returns_default(self):
        """A 200 null body from the config route yields the caller's default."""
        import httpx

        from bifrost.config import config

        response = httpx.Response(
            200,
            content=b"null",
            request=httpx.Request("POST", "http://api/api/sdk/config/get"),
        )
        client = AsyncMock()
        client.engine_request = AsyncMock(return_value=response)
        with patch("bifrost.config.get_client", return_value=client):
            assert (
                await config.get("absent", default="dflt", scope="global")
                == "dflt"
            )
        client.engine_request.assert_awaited_once_with(
            "POST",
            "/api/sdk/config/get",
            json={"key": "absent", "scope": "global"},
        )

    @pytest.mark.asyncio
    async def test_error_surfaces_instead_of_default(self):
        """A 403 from the config route raises; it is not masked by the default."""
        import httpx

        from bifrost.client import BifrostAuthorizationError
        from bifrost.config import config

        response = httpx.Response(
            403,
            json={"detail": "denied"},
            request=httpx.Request("POST", "http://api/api/sdk/config/get"),
        )
        client = AsyncMock()
        client.engine_request = AsyncMock(return_value=response)
        with patch("bifrost.config.get_client", return_value=client):
            with pytest.raises(BifrostAuthorizationError):
                await config.get("k", default="dflt", scope="global")
        client.engine_request.assert_awaited_once()


@pytest.mark.asyncio
class TestEngineRequestFacadeMutations:
    """Set/list/delete over the shared client, no HTTP."""

    @pytest.mark.asyncio
    async def test_set_list_delete_use_shared_client_transport(self):
        """The facade routes set/list/delete through the shared client."""
        import httpx

        from bifrost.config import config

        set_response = httpx.Response(
            204,
            request=httpx.Request("POST", "http://api/api/sdk/config/set"),
        )
        list_response = httpx.Response(
            200,
            json={"a": 1},
            request=httpx.Request("POST", "http://api/api/sdk/config/list"),
        )
        delete_response = httpx.Response(
            200,
            json=True,
            request=httpx.Request("POST", "http://api/api/sdk/config/delete"),
        )
        client = AsyncMock()
        client.engine_request = AsyncMock(
            side_effect=[set_response, list_response, delete_response]
        )
        with patch("bifrost.config.get_client", return_value=client):
            assert await config.set("k", {"n": 1}, scope="global") is None
            listed = await config.list(scope="global")
            assert listed["a"] == 1
            assert listed.a == 1
            assert await config.delete("k", scope="global") is True
        assert [
            (call.args[0], call.args[1])
            for call in client.engine_request.await_args_list
        ] == [
            ("POST", "/api/sdk/config/set"),
            ("POST", "/api/sdk/config/list"),
            ("POST", "/api/sdk/config/delete"),
        ]

    @pytest.mark.asyncio
    async def test_set_error_surfaces(self):
        """A denied set raises through the shared client; no fallback replay."""
        import httpx

        from bifrost.client import BifrostAuthorizationError
        from bifrost.config import config

        response = httpx.Response(
            403,
            json={"detail": "denied"},
            request=httpx.Request("POST", "http://api/api/sdk/config/set"),
        )
        client = AsyncMock()
        client.engine_request = AsyncMock(return_value=response)
        with patch("bifrost.config.get_client", return_value=client):
            with pytest.raises(BifrostAuthorizationError):
                await config.set("k", "v", scope="global")
        client.engine_request.assert_awaited_once()
