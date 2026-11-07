"""Replica coordination, scope partitioning and quota behavior without GitHub access."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from shared import github_app_client as module
from shared.github_app_auth import GitHubAppError


class Cache:
    def __init__(self):
        self.values = {}
        self.locks = {}
        self.health = {}

    def lock(self, key, **kwargs):
        return self.locks.setdefault(key, asyncio.Lock())

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        self.values[key] = value

    async def ttl(self, key):
        return 120 if key in self.values else -2

    async def hset(self, key, mapping):
        self.health[key] = mapping

    async def expire(self, key, ttl):
        return True


@pytest.fixture
def cache(monkeypatch):
    cache = Cache()

    @asynccontextmanager
    async def connection():
        yield cache

    monkeypatch.setattr(module, "get_redis", connection)
    return cache


def client():
    return module.GitHubAppClient(module.AppCredentials(123, "app-generation", "not-sent"))


def token_response():
    return {"token": "synthetic-installation-secret", "permissions": {"metadata": "read"},
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "https://example.com/app"), ("GET", "//example.com/app"),
    ("GET", "/app/installations/4/../../user"), ("GET", "/app/installations/%2e%2e"),
    ("GET", "/app/installations/4?other=1"), ("GET", "/app/installations/4#other"),
    ("GET", "/app/installations/4\\other"), ("GET", "/app/installations/4\n"),
    ("GET", "/app/installations/４"), ("GET", "/app/installations/-1"),
    ("GET", "/app/installations/0"), ("GET", "/user"),
    ("GET", "/app/installations/4/access_tokens"), ("POST", "/app"),
    ("PUT", "/app"), ("DELETE", "/app/installations/4"),
])
async def test_unsupported_paths_and_methods_are_rejected_before_credentials_or_network(cache, monkeypatch, method, path):
    network = Mock(side_effect=AssertionError("Unexpected network request"))
    jwt = Mock(side_effect=AssertionError("Unexpected credential use"))
    monkeypatch.setattr(module.httpx, "AsyncClient", network)
    monkeypatch.setattr(module, "issue_app_jwt", jwt)
    with pytest.raises(ValueError, match="Unsupported GitHub App request endpoint"):
        await client().request(method, path)
    jwt.assert_not_called()
    network.assert_not_called()
    assert cache.values == {} and cache.health == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/app"), ("GET", "/app/installations/4"),
    ("GET", "/app/hook/deliveries"), ("GET", "/installation/repositories"),
    ("POST", "/app/installations/4/access_tokens"), ("POST", "/app/hook/deliveries/4/attempts"),
])
async def test_supported_endpoints_keep_query_values_out_of_the_host_and_path(cache, monkeypatch, method, path):
    calls = []
    query = "../other?value=1#fragment&next=https://example.com"

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    original = httpx.AsyncClient

    def transport(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", transport)
    await client().request(method, path, params={"cursor": query}, token="synthetic-token", installation_id=4)
    assert len(calls) == 1
    request = calls[0]
    assert request.method == method
    assert request.url.scheme == "https" and request.url.host == "api.github.com"
    assert request.url.path == path and request.url.fragment == ""
    assert request.url.params["cursor"] == query


@pytest.mark.asyncio
async def test_concurrent_refresh_coalesces_across_client_instances_and_encrypts_cache(cache):
    first, second = client(), client()
    mint = AsyncMock(return_value=token_response())
    first.request = second.request = mint
    results = await asyncio.gather(*[(first if i % 2 else second).token(4, "binding-1") for i in range(20)])
    assert set(results) == {"synthetic-installation-secret"}
    assert mint.await_count == 1
    assert all("synthetic-installation-secret" not in value for value in cache.values.values())


@pytest.mark.asyncio
async def test_exact_scope_and_binding_generation_partition_the_token_cache(cache):
    api = client()
    async def mint(*args, **kwargs):
        return {**token_response(), "repositories": [{"id": identity} for identity in kwargs["data"]["repository_ids"]]}
    api.request = AsyncMock(side_effect=mint)
    await api.token(4, "binding-1", repository_ids=(10,))
    await api.token(4, "binding-1", repository_ids=(20,))
    await api.token(4, "binding-2", repository_ids=(10,))
    await api.token(5, "binding-1", repository_ids=(10,))
    assert api.request.await_count == 4
    await api.token(4, "binding-1", repository_ids=(10, 10))
    assert api.request.await_count == 4
    assert api.request.await_args_list[0].kwargs["data"] == {"permissions": {"metadata": "read"}, "repository_ids": [10]}


@pytest.mark.asyncio
async def test_ciphertext_cannot_be_moved_between_installations_or_replayed_after_expiry(cache):
    api = client()
    api.request = AsyncMock(return_value=token_response())
    await api.token(4, "binding-1")
    await api.token(5, "binding-1")
    first, second = list(cache.values)
    cache.values[second] = cache.values[first]
    with pytest.raises(GitHubAppError, match="verified"):
        await api.token(5, "binding-1")
    sealed = module.seal_cache("inventory-scope", [{"id": 1}], -1)
    assert module.open_cache("inventory-scope", sealed) is None
    with pytest.raises(GitHubAppError, match="verified"):
        module.open_cache("other-scope", sealed)


@pytest.mark.asyncio
async def test_wrong_scope_and_expired_tokens_never_enter_the_cache(cache):
    api = client()
    response = token_response()
    response["permissions"] = {"contents": "write"}
    api.request = AsyncMock(return_value=response)
    with pytest.raises(GitHubAppError, match="scoped"):
        await api.token(4, "binding-1")
    response["permissions"] = {"metadata": "read"}
    response["expires_at"] = datetime.now(timezone.utc).isoformat()
    with pytest.raises(GitHubAppError, match="expired"):
        await api.token(4, "binding-1")
    assert cache.values == {}


@pytest.mark.asyncio
async def test_repository_scope_mismatch_never_enters_the_cache(cache):
    api = client()
    api.request = AsyncMock(return_value={**token_response(), "repositories": [{"id": 20}]})
    with pytest.raises(GitHubAppError, match="repository token"):
        await api.token(4, "binding-1", repository_ids=(10,))
    assert cache.values == {}


@pytest.mark.asyncio
async def test_delivery_history_uses_encoded_link_cursor_without_following_provider_urls(cache, monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, headers={"Link": '<https://api.github.com/app/hook/deliveries?cursor=next%2Fopaque&per_page=100>; rel="next"'}, json=[])

    original = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(module, "issue_app_jwt", lambda *args: "synthetic-jwt")
    page = await client().delivery_history("first/opaque")
    assert page.next_cursor == "next/opaque"
    assert calls[0].url.params["cursor"] == "first/opaque"
    assert "page" not in calls[0].url.params


@pytest.mark.asyncio
async def test_inventory_is_coalesced_and_uses_installation_endpoint(cache):
    first, second = client(), client()
    mint = AsyncMock(return_value="synthetic-token")
    request = AsyncMock(return_value={"total_count": 1, "repositories": [{"id": 11, "full_name": "owner/repo", "private": True}]})
    first.token = second.token = mint
    first.request = second.request = request
    inventories = await asyncio.gather(first.repositories(4, "binding-1"), second.repositories(4, "binding-1"))
    assert inventories == [[{"id": 11, "full_name": "owner/repo", "private": True}]] * 2
    assert request.await_count == 1
    assert request.await_args.args[1] == "/installation/repositories"
    assert request.await_args.kwargs["params"] == {"per_page": 100, "page": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [5000, 5001])
async def test_inventory_limit_includes_exactly_five_thousand_repositories(cache, total):
    api = client()
    api.token = AsyncMock(return_value="synthetic-token")
    page = 0

    async def inventory(*args, **kwargs):
        nonlocal page
        page += 1
        return {"total_count": total, "repositories": [
            {"id": identity, "full_name": f"owner/repo-{identity}"} for identity in range((page - 1) * 100, page * 100)]}

    api.request = AsyncMock(side_effect=inventory)
    if total == 5000:
        assert len(await api.repositories(4, "binding-1")) == total
        assert page == 50
    else:
        with pytest.raises(GitHubAppError) as error:
            await api.repositories(4, "binding-1")
        assert error.value.code == "inventory_limit"
        assert page == 1
        assert cache.values == {}


@pytest.mark.asyncio
async def test_quota_cooldown_is_installation_scoped_across_tokens(cache, monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "120"}, json={"message": "rate limited"})

    original = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    api = client()
    for token in ("first-token", "different-token"):
        with pytest.raises(GitHubAppError) as error:
            await api.request("GET", "/installation/repositories", token=token, installation_id=4)
        assert error.value.code == "rate_limit"
    assert len(calls) == 1
    assert cache.health["github-app:budget:123:4:health"] == {"status": "429"}


@pytest.mark.asyncio
async def test_inflight_revocation_is_checked_before_response_can_be_reused(cache, monkeypatch):
    allowed = True

    def handle(request):
        nonlocal allowed
        allowed = False
        return httpx.Response(200, json={"repositories": []})

    async def authorize():
        if not allowed:
            raise GitHubAppError("binding_changed", "Binding changed", 409)

    original = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    api = module.GitHubAppClient(module.AppCredentials(123, "generation", "unused"), authorize)
    with pytest.raises(GitHubAppError, match="Binding changed"):
        await api.request("GET", "/installation/repositories", token="synthetic-token", installation_id=4)
    assert cache.values == {}
