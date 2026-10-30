"""Challenge-driven refresh on every SDK transport (plan §5.3, D11, D20).

The SDK refreshes and replays a request only on the API's
``refresh_required`` challenge, with one budget of three refreshes per public
call shared by its transient retries. No other 401 is replayed, and a
returned stream is never replayed.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

import bifrost.client as client_mod
from bifrost._refreshing import (
    REFRESH_BUDGET_PER_CALL,
    RefreshingAsyncClient,
    RefreshingClient,
    is_refresh_challenge,
    refresh_extensions,
)
from bifrost.client import BifrostClient

CHALLENGE = {"WWW-Authenticate": 'Bearer error="invalid_token", error_description="refresh_required"'}
API = "http://test.local"


@pytest.fixture(autouse=True)
def _no_real_sleep():
    async def _async_noop(_delay: float) -> None:
        return None

    with patch("bifrost.client.asyncio.sleep", side_effect=_async_noop), patch(
        "bifrost.client.time.sleep", return_value=None
    ):
        yield


class _Server:
    """Answers each request from a script; every token but ``fresh-N`` is stale."""

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.requests: list[httpx.Request] = []
        self.effects = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else "auth"
        if step == "502":
            return httpx.Response(502, request=request)
        if step == "challenge":
            return httpx.Response(401, headers=CHALLENGE, json={"detail": "Not authenticated"}, request=request)
        if step == "plain401":
            return httpx.Response(401, headers={"WWW-Authenticate": "Bearer"}, request=request)
        # "auth": the auth dependency challenges a stale token before any
        # effect, as the API does.
        if not request.headers["authorization"].startswith("Bearer fresh"):
            return httpx.Response(401, headers=CHALLENGE, json={"detail": "Not authenticated"}, request=request)
        self.effects += 1
        return httpx.Response(200, json={"ok": True}, request=request)


class _Refresher:
    def __init__(self) -> None:
        self.observed: list[str | None] = []

    def token(self) -> str:
        return f"fresh-{len(self.observed)}"

    async def __call__(self, api_url: str, observed: str | None) -> str:
        assert api_url == API
        self.observed.append(observed)
        return self.token()

    def sync(self, api_url: str, observed: str | None) -> str:
        assert api_url == API
        self.observed.append(observed)
        return self.token()


def _client(server: _Server, monkeypatch: pytest.MonkeyPatch, refresher: _Refresher) -> BifrostClient:
    monkeypatch.setattr(client_mod, "refresh_connection_access_token", refresher)
    monkeypatch.setattr(client_mod, "_refresh_connection_access_token_sync", refresher.sync)
    client = BifrostClient(API, "stale")
    transport = httpx.MockTransport(server)
    client._http = RefreshingAsyncClient(
        refresh=client.refresh_access_token,
        base_url=API,
        headers={"Authorization": "Bearer stale"},
        transport=transport,
    )
    client._sync_http.close()
    client._sync_http = RefreshingClient(
        refresh=client._refresh_access_token_sync,
        base_url=API,
        headers={"Authorization": "Bearer stale"},
        transport=transport,
    )
    # Keep the prepared async client whatever the running loop.
    monkeypatch.setattr(client, "_get_async_client", lambda: client._http)
    return client


def test_only_the_refresh_required_challenge_counts() -> None:
    request = httpx.Request("GET", API)
    assert is_refresh_challenge(httpx.Response(401, headers=CHALLENGE, request=request))
    assert not is_refresh_challenge(httpx.Response(401, headers={"WWW-Authenticate": "Bearer"}, request=request))
    assert not is_refresh_challenge(httpx.Response(401, request=request))
    assert not is_refresh_challenge(httpx.Response(403, headers=CHALLENGE, request=request))


async def test_one_budget_spans_the_transient_retries_of_an_async_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # challenge -> refresh 1 -> 502 -> retry -> challenge -> refresh 2 ->
    # challenge -> refresh 3 -> challenge: the budget is spent.
    server = _Server(["challenge", "502", "challenge", "challenge", "challenge", "auth"])
    refresher = _Refresher()
    client = _client(server, monkeypatch, refresher)

    response = await client.get("/api/thing")

    assert response.status_code == 401
    assert is_refresh_challenge(response)
    assert len(refresher.observed) == REFRESH_BUDGET_PER_CALL
    assert len(server.requests) == 5


def test_one_budget_spans_the_transient_retries_of_a_sync_call(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(["challenge", "502", "challenge", "challenge", "challenge", "auth"])
    refresher = _Refresher()
    client = _client(server, monkeypatch, refresher)

    response = client.get_sync("/api/thing")

    assert response.status_code == 401
    assert len(refresher.observed) == REFRESH_BUDGET_PER_CALL
    assert len(server.requests) == 5


async def test_request_shares_one_budget_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(["challenge", "502", "auth"])
    refresher = _Refresher()
    client = _client(server, monkeypatch, refresher)

    response = await client.request("DELETE", "/api/thing", json={"id": 1}, retry_transient=True)

    assert response.status_code == 200
    assert refresher.observed == ["stale"]
    assert [r.headers["authorization"] for r in server.requests] == ["Bearer stale", "Bearer fresh-1", "Bearer fresh-1"]


async def test_a_challenged_post_is_replayed_and_takes_effect_once(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server([])
    refresher = _Refresher()
    client = _client(server, monkeypatch, refresher)

    response = await client.post("/api/things", json={"name": "one"})

    assert response.status_code == 200
    assert server.effects == 1
    assert [json.loads(r.content) for r in server.requests] == [{"name": "one"}, {"name": "one"}]
    # Every later request carries the refreshed token.
    assert client._access_token == "fresh-1"
    assert client._sync_http.headers["Authorization"] == "Bearer fresh-1"


async def test_a_401_without_the_challenge_is_never_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    # For example an AI provider's authentication error, mapped to 401
    # after the route ran.
    server = _Server(["plain401"])
    refresher = _Refresher()
    client = _client(server, monkeypatch, refresher)

    response = await client.post("/api/sdk/ai/complete", json={})

    assert response.status_code == 401
    assert refresher.observed == []
    assert len(server.requests) == 1


async def test_a_request_without_a_budget_is_never_replayed() -> None:
    calls: list[str | None] = []

    async def refresh(observed: str | None) -> str:
        calls.append(observed)
        return "fresh"

    transport = httpx.MockTransport(lambda request: httpx.Response(401, headers=CHALLENGE, request=request))
    async with RefreshingAsyncClient(refresh=refresh, base_url=API, transport=transport) as http:
        response = await http.get("/x")

    assert response.status_code == 401
    assert calls == []


async def test_a_failed_refresh_returns_the_challenge_unread() -> None:
    async def refresh(observed: str | None) -> None:
        return None

    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, headers=CHALLENGE, json={"detail": "Not authenticated"}, request=request)
    )
    async with RefreshingAsyncClient(refresh=refresh, base_url=API, transport=transport) as http:
        response = await http.get("/x", extensions=refresh_extensions())

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


class _StreamServer:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        authorization = request.headers["authorization"]
        self.requests.append(authorization)
        if not authorization.startswith("Bearer fresh"):
            return httpx.Response(401, headers=CHALLENGE, json={"detail": "Not authenticated"}, request=request)
        return httpx.Response(200, content=b"data: one\n\ndata: [DONE]\n\n", request=request)


async def test_a_stream_refreshes_on_the_status_line_before_any_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _StreamServer()
    refresher = _Refresher()
    monkeypatch.setattr(client_mod, "refresh_connection_access_token", refresher)
    client = BifrostClient(API, "stale")
    client._http = RefreshingAsyncClient(
        refresh=client.refresh_access_token,
        base_url=API,
        headers={"Authorization": "Bearer stale"},
        transport=httpx.MockTransport(server),
    )
    monkeypatch.setattr(client, "_get_async_client", lambda: client._http)

    async with client.stream("POST", "/api/sdk/ai/stream", json={}) as response:
        body = await response.aread()

    assert response.status_code == 200
    assert body == b"data: one\n\ndata: [DONE]\n\n"
    assert server.requests == ["Bearer stale", "Bearer fresh-1"]
    client._sync_http.close()


async def test_a_returned_stream_is_never_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    refresher = _Refresher()
    monkeypatch.setattr(client_mod, "refresh_connection_access_token", refresher)
    requests: list[str] = []

    def server(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers["authorization"])
        return httpx.Response(200, content=b"partial", request=request)

    client = BifrostClient(API, "stale")
    client._http = RefreshingAsyncClient(
        refresh=client.refresh_access_token, base_url=API, transport=httpx.MockTransport(server)
    )
    monkeypatch.setattr(client, "_get_async_client", lambda: client._http)

    async with client.stream("GET", "/api/stream") as response:
        assert await response.aread() == b"partial"

    assert requests == ["Bearer stale"]
    assert refresher.observed == []
    client._sync_http.close()


async def test_the_engine_socket_transports_refresh_on_the_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server([])
    refresher = _Refresher()
    monkeypatch.setattr(client_mod, "refresh_connection_access_token", refresher)
    monkeypatch.setattr(client_mod, "_refresh_connection_access_token_sync", refresher.sync)
    # Stand in for the worker socket: the engine clients pass a transport.
    monkeypatch.setattr(client_mod.httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(server))
    monkeypatch.setattr(client_mod.httpx, "HTTPTransport", lambda **_: httpx.MockTransport(server))
    client_mod._install_engine_socket("/tmp/bifrost-test-engine.sock")
    try:
        client = BifrostClient(API, "stale")
        unary = await client.engine_request("POST", "/api/sdk/config/set", json={"k": "v"})
        sync = client.engine_request_sync("GET", "/api/sdk/context")
        async with client.engine_stream("POST", "/api/sdk/ai/stream", json={}) as streamed:
            await streamed.aread()
    finally:
        client_mod._clear_engine_socket()

    assert (unary.status_code, sync.status_code, streamed.status_code) == (200, 200, 200)
    # One refresh: later requests on every engine client carry the new token.
    assert refresher.observed == ["stale"]
    assert server.effects == 3
    assert [r.headers["authorization"] for r in server.requests] == [
        "Bearer stale",
        "Bearer fresh-1",
        "Bearer fresh-1",
        "Bearer fresh-1",
    ]
    client._sync_http.close()


def test_the_cold_module_fetch_refreshes_on_the_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import module_cache_sync

    server_requests: list[str] = []

    def server(request: httpx.Request) -> httpx.Response:
        server_requests.append(request.headers["authorization"])
        if request.headers["authorization"] != "Bearer fresh-1":
            return httpx.Response(401, headers=CHALLENGE, request=request)
        return httpx.Response(200, json={"path": "modules/x.py", "content": "X = 1", "hash": "h"}, request=request)

    refresher = _Refresher()
    monkeypatch.setattr(client_mod, "_refresh_connection_access_token_sync", refresher.sync)
    monkeypatch.setattr(module_cache_sync, "_get_engine_credentials", lambda: (API, "stale"))
    http = RefreshingClient(
        refresh=module_cache_sync._refresh_engine_access_token, transport=httpx.MockTransport(server)
    )
    monkeypatch.setattr(module_cache_sync._solution_ctx, "http_client", http, raising=False)

    module = module_cache_sync._fetch_module_from_api("modules/x.py")

    assert module is not None and module["content"] == "X = 1"
    assert server_requests == ["Bearer stale", "Bearer fresh-1"]
    assert refresher.observed == ["stale"]
    http.close()


async def test_a_refreshed_token_is_redacted_from_the_runs_output(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[str] = []
    monkeypatch.setattr("bifrost._context.register_secret", registered.append)
    client = BifrostClient(API, "stale")

    client.install_access_token("fresh-access-token-value")

    assert registered == ["fresh-access-token-value"]
    client._sync_http.close()
