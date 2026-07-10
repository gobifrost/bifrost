"""Tests for BifrostClient credential resolution and refresh coordination."""

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest


@pytest.fixture
def isolated_credentials(tmp_path, monkeypatch):
    from bifrost import client as client_mod
    from bifrost import credentials as creds_mod

    if hasattr(client_mod._thread_local, "bifrost_client"):
        delattr(client_mod._thread_local, "bifrost_client")
    client_mod._reset_refresh_coordinators_for_tests()
    monkeypatch.setattr(
        creds_mod,
        "get_credentials_path",
        lambda: tmp_path / "credentials.json",
    )
    monkeypatch.setattr(creds_mod, "get_config_path", lambda: tmp_path / "config.json")
    creds_mod._reset_persistent_backend_for_tests()
    monkeypatch.setattr(creds_mod, "_persistent_backend", creds_mod.JsonBackend())
    monkeypatch.delenv("BIFROST_API_URL", raising=False)
    monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
    yield creds_mod
    if hasattr(client_mod._thread_local, "bifrost_client"):
        delattr(client_mod._thread_local, "bifrost_client")
    client_mod._reset_refresh_coordinators_for_tests()
    creds_mod._reset_persistent_backend_for_tests()


def test_get_instance_does_not_report_default_ambiguity_when_env_selects_url(
    isolated_credentials, monkeypatch
) -> None:
    from bifrost import client as client_mod

    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    isolated_credentials.save_credentials(
        "https://first.example.com", "at1", "rt1", expired_at
    )
    isolated_credentials.save_credentials(
        "https://second.example.com", "at2", "rt2", expired_at
    )
    monkeypatch.setenv("BIFROST_API_URL", "https://second.example.com")

    async def refresh_fails(*_args) -> None:
        return None

    async def login_fails(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(
        client_mod, "refresh_connection_access_token", refresh_fails
    )
    monkeypatch.setattr(client_mod, "login_flow", login_fails)

    with pytest.raises(RuntimeError, match="Not logged in"):
        client_mod.BifrostClient.get_instance(require_auth=True)


@pytest.mark.asyncio
async def test_get_instance_refreshes_expired_credentials_inside_running_loop(
    isolated_credentials, monkeypatch
) -> None:
    from bifrost import client as client_mod

    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    fresh_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    isolated_credentials.save_credentials(
        "https://api.example.com", "expired-access-token", "refresh-token", expired_at
    )

    refreshed = False

    async def refresh_succeeds(api_url, observed_access_token) -> str:
        nonlocal refreshed
        assert api_url == "https://api.example.com"
        assert observed_access_token == "expired-access-token"
        refreshed = True
        isolated_credentials.save_credentials(
            "https://api.example.com",
            "fresh-access-token",
            "fresh-refresh-token",
            fresh_at,
        )
        return "fresh-access-token"

    monkeypatch.setattr(
        client_mod, "refresh_connection_access_token", refresh_succeeds
    )

    client = client_mod.BifrostClient.get_instance(require_auth=True)

    assert refreshed is True
    assert client.api_url == "https://api.example.com"
    assert client._access_token == "fresh-access-token"


@pytest.mark.asyncio
async def test_concurrent_refreshes_for_same_stale_token_are_coalesced(
    isolated_credentials, monkeypatch
) -> None:
    from bifrost import client as client_mod

    api_url = "https://api.example.com"
    isolated_credentials.save_credentials(
        api_url,
        "stale-access-token",
        "rotating-refresh-token",
        (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    )
    refresh_calls = 0

    class FakeAsyncClient:
        def __init__(self, *, base_url, timeout):
            assert base_url == api_url
            assert timeout == 30.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, *, json):
            nonlocal refresh_calls
            assert path == "/auth/refresh"
            assert json == {"refresh_token": "rotating-refresh-token"}
            refresh_calls += 1
            await asyncio.sleep(0.01)
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-access-token",
                    "refresh_token": "fresh-refresh-token",
                    "expires_in": 1800,
                },
                request=httpx.Request("POST", f"{api_url}/auth/refresh"),
            )

    monkeypatch.setattr(client_mod.httpx, "AsyncClient", FakeAsyncClient)

    first, second = await asyncio.gather(
        client_mod.refresh_connection_access_token(api_url, "stale-access-token"),
        client_mod.refresh_connection_access_token(api_url, "stale-access-token"),
    )

    assert first == second == "fresh-access-token"
    assert refresh_calls == 1
    assert isolated_credentials.get_credentials(api_url)["access_token"] == "fresh-access-token"


@pytest.mark.asyncio
async def test_cancelled_refresh_lock_waiter_does_not_leak_lock() -> None:
    from bifrost import client as client_mod

    lock = threading.Lock()
    lock.acquire()
    waiter = asyncio.create_task(client_mod._acquire_refresh_lock(lock))
    await asyncio.sleep(0)

    waiter.cancel()
    lock.release()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, timeout=1)
    assert lock.acquire(blocking=False) is True
    lock.release()


@pytest.mark.asyncio
async def test_refresh_reuses_credentials_replaced_by_another_caller(
    isolated_credentials, monkeypatch
) -> None:
    from bifrost import client as client_mod

    api_url = "https://api.example.com"
    isolated_credentials.save_credentials(
        api_url,
        "fresh-access-token",
        "fresh-refresh-token",
        (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )

    class UnexpectedAsyncClient:
        def __init__(self, **_kwargs):
            raise AssertionError("credentials were already refreshed; no network call expected")

    monkeypatch.setattr(client_mod.httpx, "AsyncClient", UnexpectedAsyncClient)

    token = await client_mod.refresh_connection_access_token(
        api_url, "stale-access-token"
    )

    assert token == "fresh-access-token"


@pytest.mark.asyncio
async def test_env_credentials_survive_multiple_rotating_refreshes(
    isolated_credentials, monkeypatch, tmp_path
) -> None:
    from bifrost import client as client_mod

    api_url = "https://api.example.com"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BIFROST_API_URL", api_url)
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "access-0")
    monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "refresh-0")
    (tmp_path / ".env").write_text(
        "BIFROST_API_URL=https://api.example.com\n"
        "BIFROST_ACCESS_TOKEN=access-0\n"
        "BIFROST_REFRESH_TOKEN=refresh-0\n"
    )
    refresh_tokens_seen: list[str] = []

    class FakeAsyncClient:
        def __init__(self, *, base_url, timeout):
            assert base_url == api_url
            assert timeout == 30.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, *, json):
            assert path == "/auth/refresh"
            refresh_tokens_seen.append(json["refresh_token"])
            generation = len(refresh_tokens_seen)
            return httpx.Response(
                200,
                json={
                    "access_token": f"access-{generation}",
                    "refresh_token": f"refresh-{generation}",
                    "expires_in": 1800,
                },
                request=httpx.Request("POST", f"{api_url}/auth/refresh"),
            )

    monkeypatch.setattr(client_mod.httpx, "AsyncClient", FakeAsyncClient)

    first = await client_mod.refresh_connection_access_token(api_url, "access-0")
    second = await client_mod.refresh_connection_access_token(api_url, "access-1")

    assert first == "access-1"
    assert second == "access-2"
    assert refresh_tokens_seen == ["refresh-0", "refresh-1"]
    assert isolated_credentials.get_persistent_backend().get(api_url) is None
    assert isolated_credentials.get_credentials(api_url)["access_token"] == "access-2"
    assert isolated_credentials.get_credentials(api_url)["refresh_token"] == "refresh-2"
    env_text = (tmp_path / ".env").read_text()
    assert "BIFROST_ACCESS_TOKEN=access-2" in env_text
    assert "BIFROST_REFRESH_TOKEN=refresh-2" in env_text


@pytest.mark.asyncio
async def test_client_token_update_is_shared_with_async_and_sync_transports(
    isolated_credentials, monkeypatch
) -> None:
    from bifrost import client as client_mod

    client = client_mod.BifrostClient(
        "https://api.example.com", "stale-access-token"
    )
    async_http = client._get_async_client()

    async def refresh_connection(api_url, observed_access_token):
        assert api_url == "https://api.example.com"
        assert observed_access_token == "stale-access-token"
        return "fresh-access-token"

    monkeypatch.setattr(
        client_mod, "refresh_connection_access_token", refresh_connection
    )

    token = await client.refresh_access_token("stale-access-token")

    assert token == "fresh-access-token"
    assert client._access_token == "fresh-access-token"
    assert client._sync_http.headers["Authorization"] == "Bearer fresh-access-token"
    assert client._http is None
    assert async_http.headers["Authorization"] == "Bearer stale-access-token"
    await async_http.aclose()
    client._sync_http.close()


def test_sync_context_refreshes_expired_token_and_retries(
    isolated_credentials, monkeypatch
) -> None:
    from bifrost import client as client_mod

    seen_authorization: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorization = request.headers.get("Authorization")
        seen_authorization.append(authorization)
        if authorization == "Bearer stale-access-token":
            return httpx.Response(401, request=request)
        return httpx.Response(
            200,
            json={"organization": {"id": "org-1"}},
            request=request,
        )

    client = client_mod.BifrostClient(
        "https://api.example.com", "stale-access-token"
    )
    client._sync_http.close()
    client._sync_http = httpx.Client(
        base_url=client.api_url,
        headers={"Authorization": "Bearer stale-access-token"},
        transport=httpx.MockTransport(handler),
    )

    def refresh_connection(api_url, observed_access_token):
        assert api_url == "https://api.example.com"
        assert observed_access_token == "stale-access-token"
        return "fresh-access-token"

    monkeypatch.setattr(
        client_mod, "_refresh_connection_access_token_sync", refresh_connection
    )

    try:
        assert client.organization == {"id": "org-1"}
        assert seen_authorization == [
            "Bearer stale-access-token",
            "Bearer fresh-access-token",
        ]
    finally:
        client._sync_http.close()
