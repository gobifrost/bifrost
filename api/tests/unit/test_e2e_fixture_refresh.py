"""The shared E2E client refreshes only known fixture credentials on challenge."""

from __future__ import annotations

import httpx

from bifrost._refreshing import RefreshingClient
from tests.e2e.conftest import _E2EClient
from tests.e2e.fixtures.users import E2EUser


CHALLENGE = {
    "WWW-Authenticate": 'Bearer error="invalid_token", error_description="refresh_required"',
}


def _user(access_token: str, refresh_token: str) -> E2EUser:
    user = E2EUser(email=f"{access_token}@example.test", password="password", name="Fixture")
    user.set_tokens(access_token, refresh_token)
    return user


def test_shared_e2e_client_replays_only_a_refresh_required_challenge(monkeypatch) -> None:
    requests: list[str] = []
    refreshes: list[dict[str, str]] = []

    def server(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers["authorization"])
        if request.headers["authorization"] == "Bearer stale":
            return httpx.Response(401, headers=CHALLENGE, request=request)
        return httpx.Response(200, request=request)

    def refresh_endpoint(url: str, *, json: dict[str, str], timeout: float) -> httpx.Response:
        assert url == "http://e2e.test/auth/refresh"
        assert timeout == 60.0
        refreshes.append(json)
        return httpx.Response(200, json={"access_token": "fresh", "refresh_token": "rotated"})

    monkeypatch.setattr("tests.e2e.conftest.httpx.post", refresh_endpoint)
    holder: dict[str, _E2EClient] = {}
    raw = RefreshingClient(
        base_url="http://e2e.test",
        transport=httpx.MockTransport(server),
        refresh=lambda access_token: holder["client"]._refresh_fixture_user(access_token),
    )
    client = _E2EClient(raw)
    holder["client"] = client
    user = _user("stale", "one-time-refresh")

    try:
        response = client.get("/protected", headers=user.headers)
    finally:
        raw.close()

    assert response.status_code == 200
    assert requests == ["Bearer stale", "Bearer fresh"]
    assert refreshes == [{"refresh_token": "one-time-refresh"}]
    assert user.access_token == "fresh"
    assert user.refresh_token == "rotated"


def test_shared_e2e_client_does_not_replay_an_ordinary_401(monkeypatch) -> None:
    requests: list[str] = []

    def server(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers["authorization"])
        return httpx.Response(401, headers={"WWW-Authenticate": "Bearer"}, request=request)

    def unexpected_refresh(*_args, **_kwargs) -> httpx.Response:
        raise AssertionError("ordinary 401 must not invoke /auth/refresh")

    monkeypatch.setattr("tests.e2e.conftest.httpx.post", unexpected_refresh)
    holder: dict[str, _E2EClient] = {}
    raw = RefreshingClient(
        base_url="http://e2e.test",
        transport=httpx.MockTransport(server),
        refresh=lambda access_token: holder["client"]._refresh_fixture_user(access_token),
    )
    client = _E2EClient(raw)
    holder["client"] = client
    user = _user("known-token", "one-time-refresh")

    try:
        response = client.get("/forbidden", headers=user.headers)
    finally:
        raw.close()

    assert response.status_code == 401
    assert requests == ["Bearer known-token"]
