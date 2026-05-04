"""Unit tests for ``mcp_client.discovery.discover_oauth_metadata``.

Mocks ``httpx.AsyncClient`` to exercise the four return paths:

- 200 + valid JSON on both endpoints → merged dict
- 200 on authz, 404 on resource → just the authz dict
- 404 on both → ``None``
- timeout / connect error → ``None``
- invalid JSON → ``None``
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.services.mcp_client.discovery import discover_oauth_metadata


def _make_response(*, status_code: int, body=None, raise_decode: bool = False):
    """Build a fake ``httpx.Response``-shaped mock."""
    response = MagicMock()
    response.status_code = status_code
    if raise_decode:
        response.json = MagicMock(side_effect=ValueError("not json"))
    else:
        response.json = MagicMock(return_value=body)
    return response


class _FakeClient:
    """Minimal async-context-manager stand-in for ``httpx.AsyncClient``.

    We can't naively ``patch.object(httpx.AsyncClient, "get", ...)`` because
    ``discover_oauth_metadata`` constructs the client with custom kwargs
    inside an ``async with`` block. Replacing the class lets us control
    the per-URL response set.
    """

    def __init__(self, response_map: dict, *, raise_for=None):
        self._response_map = response_map
        self._raise_for = raise_for or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        if url in self._raise_for:
            raise self._raise_for[url]
        if url in self._response_map:
            return self._response_map[url]
        return _make_response(status_code=404)


@pytest.fixture
def server_url():
    return "https://vendor.example.com/path/to/mcp"


@pytest.fixture
def authz_url():
    return "https://vendor.example.com/.well-known/oauth-authorization-server"


@pytest.fixture
def resource_url():
    return "https://vendor.example.com/.well-known/oauth-protected-resource"


@pytest.mark.asyncio
async def test_discovery_merges_both_documents(
    server_url, authz_url, resource_url
):
    """200 on both endpoints → merged dict, resource doc wins on conflict."""
    authz_body = {
        "issuer": "https://vendor.example.com",
        "authorization_endpoint": "https://vendor.example.com/oauth/authorize",
        "token_endpoint": "https://vendor.example.com/oauth/token",
        "scopes_supported": ["read", "write"],
    }
    resource_body = {
        "resource": "https://vendor.example.com",
        "audience": "https://vendor.example.com/mcp",
        # Conflicts with authz on scopes_supported — resource wins
        "scopes_supported": ["read.specific"],
    }

    fake_client = _FakeClient(
        {
            authz_url: _make_response(status_code=200, body=authz_body),
            resource_url: _make_response(status_code=200, body=resource_body),
        }
    )

    with patch(
        "src.services.mcp_client.discovery.httpx.AsyncClient",
        return_value=fake_client,
    ):
        result = await discover_oauth_metadata(server_url)

    assert result is not None
    assert result["issuer"] == "https://vendor.example.com"
    assert result["authorization_endpoint"] == "https://vendor.example.com/oauth/authorize"
    assert result["audience"] == "https://vendor.example.com/mcp"
    # Resource doc wins on the conflicting key
    assert result["scopes_supported"] == ["read.specific"]


@pytest.mark.asyncio
async def test_discovery_returns_authz_only_when_resource_404(
    server_url, authz_url, resource_url
):
    """authz=200, resource=404 → return just the authz dict."""
    authz_body = {
        "authorization_endpoint": "https://vendor.example.com/oauth/authorize",
        "token_endpoint": "https://vendor.example.com/oauth/token",
    }

    fake_client = _FakeClient(
        {
            authz_url: _make_response(status_code=200, body=authz_body),
            resource_url: _make_response(status_code=404),
        }
    )

    with patch(
        "src.services.mcp_client.discovery.httpx.AsyncClient",
        return_value=fake_client,
    ):
        result = await discover_oauth_metadata(server_url)

    assert result is not None
    assert result["authorization_endpoint"] == "https://vendor.example.com/oauth/authorize"
    assert "audience" not in result


@pytest.mark.asyncio
async def test_discovery_returns_none_on_both_404(
    server_url, authz_url, resource_url
):
    """404 on both → None (caller falls back to manual entry)."""
    fake_client = _FakeClient(
        {
            authz_url: _make_response(status_code=404),
            resource_url: _make_response(status_code=404),
        }
    )

    with patch(
        "src.services.mcp_client.discovery.httpx.AsyncClient",
        return_value=fake_client,
    ):
        result = await discover_oauth_metadata(server_url)

    assert result is None


@pytest.mark.asyncio
async def test_discovery_returns_none_on_timeout(
    server_url, authz_url, resource_url
):
    """Network timeouts on both endpoints → None."""
    timeout = httpx.TimeoutException("timed out")
    fake_client = _FakeClient(
        {},
        raise_for={authz_url: timeout, resource_url: timeout},
    )

    with patch(
        "src.services.mcp_client.discovery.httpx.AsyncClient",
        return_value=fake_client,
    ):
        result = await discover_oauth_metadata(server_url)

    assert result is None


@pytest.mark.asyncio
async def test_discovery_returns_none_on_connect_error(
    server_url, authz_url, resource_url
):
    """Connection refused on both endpoints → None."""
    refused = httpx.ConnectError("connection refused")
    fake_client = _FakeClient(
        {},
        raise_for={authz_url: refused, resource_url: refused},
    )

    with patch(
        "src.services.mcp_client.discovery.httpx.AsyncClient",
        return_value=fake_client,
    ):
        result = await discover_oauth_metadata(server_url)

    assert result is None


@pytest.mark.asyncio
async def test_discovery_returns_none_on_invalid_json(
    server_url, authz_url, resource_url
):
    """200 OK but body is not JSON → None for that endpoint, full None if both."""
    fake_client = _FakeClient(
        {
            authz_url: _make_response(status_code=200, raise_decode=True),
            resource_url: _make_response(status_code=200, raise_decode=True),
        }
    )

    with patch(
        "src.services.mcp_client.discovery.httpx.AsyncClient",
        return_value=fake_client,
    ):
        result = await discover_oauth_metadata(server_url)

    assert result is None


@pytest.mark.asyncio
async def test_discovery_returns_none_on_invalid_url():
    """Server URL with no scheme → None (well-known base can't be derived)."""
    result = await discover_oauth_metadata("not a url")
    assert result is None


@pytest.mark.asyncio
async def test_discovery_skips_non_object_json(
    server_url, authz_url, resource_url
):
    """JSON body that's a list, not an object → that endpoint contributes nothing."""
    fake_client = _FakeClient(
        {
            authz_url: _make_response(status_code=200, body=["not", "an", "object"]),
            resource_url: _make_response(status_code=200, body={"audience": "x"}),
        }
    )

    with patch(
        "src.services.mcp_client.discovery.httpx.AsyncClient",
        return_value=fake_client,
    ):
        result = await discover_oauth_metadata(server_url)

    assert result == {"audience": "x"}


@pytest.mark.asyncio
async def test_discovery_handles_5xx_as_unavailable(
    server_url, authz_url, resource_url
):
    """500/503 on an endpoint → that endpoint contributes nothing."""
    fake_client = _FakeClient(
        {
            authz_url: _make_response(status_code=503),
            resource_url: _make_response(status_code=200, body={"audience": "x"}),
        }
    )

    # Use AsyncMock to also exercise httpx.AsyncClient construction kwargs
    mock_client_class = MagicMock(return_value=fake_client)
    with patch(
        "src.services.mcp_client.discovery.httpx.AsyncClient",
        mock_client_class,
    ):
        result = await discover_oauth_metadata(server_url)

    assert result == {"audience": "x"}
    # Verify the timeout was set as expected (5s per spec)
    _, kwargs = mock_client_class.call_args
    assert "timeout" in kwargs
