from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from shared import github_git_app as app
from src.services.github_config import GitHubConfig, _get_fernet


@pytest.mark.parametrize("mismatch", ["user", "scope", "step", "expired"])
def test_callback_state_rejects_another_actor_scope_step_or_expiry(mismatch):
    scope = uuid4()
    claims = {"setup_id": str(uuid4()), "user_id": "actor", "scope": str(scope), "step": "oauth"}
    token, _ = app.encode_state_jwt(audience=app.STATE_AUDIENCE, claims=claims,
                                  ttl=timedelta(seconds=-1) if mismatch == "expired" else timedelta(minutes=1))
    with pytest.raises(HTTPException) as error:
        app._decode_state("oauth." + token, "other" if mismatch == "user" else "actor",
                          uuid4() if mismatch == "scope" else scope, "manifest" if mismatch == "step" else "oauth")
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_disconnect_prevents_even_a_cached_app_token_from_being_used(monkeypatch):
    monkeypatch.setattr(app, "get_github_config", AsyncMock(return_value=None))
    redis = AsyncMock()
    monkeypatch.setattr(app, "get_shared_redis", redis)
    config = GitHubConfig("https://github.com/owner/repo", None, "main", "connected", auth_kind="app", generation="old")
    with pytest.raises(HTTPException) as error:
        await app.GitAppCredentials(None, config).get_token()
    assert error.value.status_code == 409
    redis.assert_not_awaited()


@pytest.mark.asyncio
async def test_httpx_logs_redact_manifest_conversion_codes(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(201, json={}))) as client:
        await client.post("https://api.github.com/app-manifests/one-use-private-code/conversions")
    assert "app-manifests/[redacted]/conversions" in caplog.text
    assert "one-use-private-code" not in caplog.text


@pytest.fixture
def key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return key, pem


@pytest.mark.asyncio
async def test_readonly_tokens_are_coalesced_and_separate_from_workspace_writes(monkeypatch, key_pair):
    redis = Redis()
    monkeypatch.setattr(app, "get_shared_redis", AsyncMock(return_value=redis))
    async def mint(*args, data):
        return {"token": data["permissions"]["contents"], "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    provider = AsyncMock(side_effect=mint)
    monkeypatch.setattr(app, "_request", provider)
    config = GitHubConfig("https://github.com/owner/repo", None, "main", "connected", auth_kind="app", generation="generation",
                         app={"id": 42, "private_key": key_pair[1], "installation_id": 7, "repository_id": 91, "workflows": True})
    reader = app.GitAppCredentials(None, config, contents="read")
    writer = app.GitAppCredentials(None, config)
    monkeypatch.setattr(reader, "_current", AsyncMock())
    monkeypatch.setattr(writer, "_current", AsyncMock())
    assert await asyncio.gather(*(reader.get_token() for _ in range(8))) == ["read"] * 8
    assert await writer.get_token() == "write"
    assert provider.await_count == 2
    assert provider.await_args_list[0].kwargs["data"] == {"repository_ids": [91], "permissions": {"contents": "read"}}
    assert provider.await_args_list[1].kwargs["data"]["permissions"] == {"contents": "write", "workflows": "write"}


def test_app_jwt_is_short_lived_rsa_and_has_app_identity(key_pair):
    key, pem = key_pair
    token = app.app_jwt({"id": 42, "private_key": pem})
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"])
    assert claims["iss"] == "42"
    assert claims["exp"] - claims["iat"] == 600


@pytest.mark.parametrize("pem", ["not a key", "-----BEGIN PRIVATE KEY-----\ninvalid"])
def test_bad_private_key_error_does_not_echo_material(pem):
    with pytest.raises(HTTPException) as error:
        app.app_jwt({"id": 42, "private_key": pem})
    assert pem not in error.value.detail


@pytest.mark.parametrize("url", ["https://safe.example/path", "https://user:password@safe.example", "https://safe.example?other=host", "http://public.example", "javascript:alert(1)"])
def test_callback_origin_must_be_an_explicit_https_origin(monkeypatch, url):
    monkeypatch.setattr(app, "get_settings", lambda: SimpleNamespace(public_url=url))
    with pytest.raises(HTTPException):
        app.external_origin()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "https://evil.example/app", "//evil.example/app", "/app/../user",
    "/app/installations/0/access_tokens", "/app/installations/1/access_tokens?next=evil",
    "/user/installations/1/repositories/extra", "/app/installations/1/../../user",
    "/app/installations/1%2f..%2fuser", "/app/installations/1\\user",
    "/app/installations/1#fragment", "/app/installations/1\r\nInjected: true",
])
async def test_app_transport_rejects_bad_paths_before_network(monkeypatch, path):
    client = AsyncMock()
    monkeypatch.setattr(app.httpx, "AsyncClient", client)
    with pytest.raises(HTTPException):
        await app._request("GET", path, "private-token")
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "DELETE", "TRACE"])
async def test_app_transport_rejects_unapproved_methods_before_network(monkeypatch, method):
    client = AsyncMock()
    monkeypatch.setattr(app, "http_client", client)
    with pytest.raises(HTTPException):
        await app._request(method, "/user/installations", "private-token")
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("GET", "/app"), ("GET", "/user/installations"),
    ("GET", "/installation/repositories"), ("GET", "/app/installations/7"),
    ("GET", "/user/installations/7/repositories"),
    ("POST", "/app/installations/7/access_tokens"),
])
async def test_app_transport_keeps_allowed_requests_on_the_exact_provider_endpoint(monkeypatch, method, path):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})
    monkeypatch.setattr(app, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    result = await app._request(method, path, "private-token", params={"page": 2})
    assert result == {"ok": True}
    assert len(requests) == 1
    request = requests[0]
    assert request.method == method
    assert str(request.url) == f"https://api.github.com{path}?page=2"
    assert request.headers["Authorization"] == "Bearer private-token"


@pytest.mark.asyncio
async def test_app_transport_never_exposes_provider_body(monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def request(self, *args, **kwargs):
            return httpx.Response(403, json={"message": "private-token or private key"})
    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **_: Client())
    with pytest.raises(HTTPException) as error:
        await app._request("GET", "/app", "private-token")
    assert "private-token" not in error.value.detail


class Redis:
    def __init__(self):
        self.values = {}
        self.locks = {}

    def lock(self, name, **_):
        return self.locks.setdefault(name, asyncio.Lock())

    async def get(self, key): return self.values.get(key)

    async def setex(self, key, ttl, value):
        assert ttl > 0
        self.values[key] = value


@pytest.mark.asyncio
async def test_tokens_are_repository_scoped_encrypted_and_coalesced(monkeypatch, key_pair):
    redis = Redis()
    monkeypatch.setattr(app, "get_shared_redis", AsyncMock(return_value=redis))
    provider = AsyncMock(return_value={"token": "installation-secret", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    monkeypatch.setattr(app, "_request", provider)
    config = GitHubConfig("https://github.com/owner/repo", None, "main", "connected", auth_kind="app", generation="generation",
                         app={"id": 42, "private_key": key_pair[1], "installation_id": 7, "repository_id": 91, "workflows": False})
    resolver = app.GitAppCredentials(None, config)
    monkeypatch.setattr(resolver, "_current", AsyncMock())
    assert await asyncio.gather(*(resolver.get_token() for _ in range(8))) == ["installation-secret"] * 8
    assert provider.await_count == 1
    assert provider.call_args.kwargs["data"] == {"repository_ids": [91], "permissions": {"contents": "write"}}
    assert "installation-secret" not in json.dumps(redis.values)
    assert _get_fernet().decrypt(next(iter(redis.values.values())).encode()).decode() == "installation-secret"


@pytest.mark.asyncio
async def test_token_expiry_and_configuration_change_do_not_reuse_credentials(monkeypatch, key_pair):
    redis = Redis()
    monkeypatch.setattr(app, "get_shared_redis", AsyncMock(return_value=redis))
    provider = AsyncMock(side_effect=[
        {"token": token, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        for token in ["first", "refreshed"]
    ])
    monkeypatch.setattr(app, "_request", provider)
    config = GitHubConfig("https://github.com/owner/repo", None, "main", "connected", auth_kind="app", generation="generation",
                         app={"id": 42, "private_key": key_pair[1], "installation_id": 7, "repository_id": 91})
    resolver = app.GitAppCredentials(None, config)
    current = AsyncMock()
    monkeypatch.setattr(resolver, "_current", current)
    assert await resolver.get_token() == "first"
    redis.values.clear()  # Simulate Redis TTL expiry, not a timing sleep.
    assert await resolver.get_token() == "refreshed"
    current.side_effect = HTTPException(409, "connection changed")
    with pytest.raises(HTTPException):
        await resolver.get_token()
    assert provider.await_count == 2


@pytest.mark.asyncio
async def test_rest_client_resolves_credentials_for_every_request(monkeypatch):
    from src.services.github_api import GitHubAPIClient
    seen = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def request(self, *args, **kwargs):
            seen.append(kwargs["headers"]["Authorization"])
            return httpx.Response(200, json={"repositories": []}, request=httpx.Request("GET", "https://api.github.com/installation/repositories"))
    monkeypatch.setattr("src.services.github_api.httpx.AsyncClient", lambda **_: Client())
    tokens = AsyncMock(side_effect=["first", "refreshed"])
    client = GitHubAPIClient(token_provider=tokens, installation_auth=True)
    await client.list_repositories()
    await client.list_repositories()
    assert seen == ["Bearer first", "Bearer refreshed"]


@pytest.mark.asyncio
async def test_installation_cooldown_is_shared_across_repository_tokens(monkeypatch, key_pair):
    redis = Redis()
    monkeypatch.setattr(app, "get_shared_redis", AsyncMock(return_value=redis))
    provider = AsyncMock(side_effect=HTTPException(429, "limited", headers={"Retry-After": "20"}))
    monkeypatch.setattr(app, "_request", provider)
    for repository_id in [91, 92]:
        config = GitHubConfig("https://github.com/owner/repo", None, "main", "connected", auth_kind="app", generation=str(repository_id),
                             app={"id": 42, "private_key": key_pair[1], "installation_id": 7, "repository_id": repository_id})
        resolver = app.GitAppCredentials(None, config)
        monkeypatch.setattr(resolver, "_current", AsyncMock())
        with pytest.raises(HTTPException) as error:
            await resolver.get_token()
        assert error.value.status_code == 429
    assert provider.await_count == 1


@pytest.mark.asyncio
async def test_removed_app_repository_is_not_treated_as_an_empty_remote(tmp_path):
    from git import GitCommandError
    from unittest.mock import Mock
    from src.services.github_sync import GitHubSyncService, SyncError
    repo = Mock()
    repo.remotes.origin.fetch.side_effect = GitCommandError(["git", "fetch"], 128, "remote: Repository not found")
    service = GitHubSyncService(None, "https://github.com/owner/repo", token_provider=AsyncMock(return_value="installation-secret"), app_auth=True)
    with pytest.raises(SyncError, match="repository access was denied"):
        await service._do_fetch(tmp_path, repo)


@pytest.mark.asyncio
async def test_revoked_installation_never_uses_a_personal_token(monkeypatch, key_pair):
    redis = Redis()
    monkeypatch.setattr(app, "get_shared_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(app, "_request", AsyncMock(side_effect=HTTPException(403, "revoked")))
    config = GitHubConfig("https://github.com/owner/repo", "personal-token", "main", "connected", auth_kind="app", generation="generation",
                         app={"id": 42, "private_key": key_pair[1], "installation_id": 7, "repository_id": 91})
    resolver = app.GitAppCredentials(None, config)
    monkeypatch.setattr(resolver, "_current", AsyncMock())
    with pytest.raises(HTTPException) as error:
        await resolver.get_token()
    assert error.value.status_code == 403
    assert not redis.values


@pytest.mark.asyncio
async def test_a_running_pat_operation_cannot_reuse_credentials_after_migration(monkeypatch):
    previous = GitHubConfig("https://github.com/owner/repo", "personal-token", "main", "connected", generation="pat")
    current = GitHubConfig("https://github.com/owner/repo", None, "main", "connected", auth_kind="app", generation="app", app={"id": 42})
    monkeypatch.setattr(app, "get_github_config", AsyncMock(return_value=current))
    provider = app.credential_provider(None, previous)
    with pytest.raises(HTTPException) as error:
        await provider()
    assert error.value.status_code == 409


@pytest.fixture
def pending_setup(key_pair):
    return uuid4(), {
        "stage": "ready", "prior_generation": None,
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "app": {"id": 42, "private_key": key_pair[1]},
        "installation_id": 7, "workflows": False,
        "repository": {"id": 91, "full_name": "owner/repo", "default_branch": "main"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["workspace", "solution"])
async def test_repository_selection_respects_workspace_and_solution_boundaries(monkeypatch, pending_setup, purpose):
    setup_id, payload = pending_setup
    payload.update(purpose=purpose, stage="repository", expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    payload["repositories"] = [payload.pop("repository")]
    monkeypatch.setattr(app, "setup_repositories", AsyncMock())
    monkeypatch.setattr(app, "_row", AsyncMock(return_value=(SimpleNamespace(), payload)))
    current = GitHubConfig("https://github.com/owner/connected", "personal", "main", "connected", generation="changed")
    workspace = AsyncMock(return_value=current)
    monkeypatch.setattr(app, "get_github_config", workspace)
    save = AsyncMock()
    monkeypatch.setattr(app, "_save", save)
    monkeypatch.setattr(app, "get_shared_redis", AsyncMock(return_value=Redis()))
    provider = AsyncMock(return_value={"token": "installation-secret", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    monkeypatch.setattr(app, "_request", provider)

    if purpose == "workspace":
        with pytest.raises(HTTPException) as error:
            await app.selected_config(None, setup_id, 7, 91, "actor", None)
        assert error.value.status_code == 409
        assert payload["stage"] == "repository"
        save.assert_not_awaited()
        provider.assert_not_awaited()
    else:
        config = await app.selected_config(None, setup_id, 7, 91, "actor", None)
        assert config.repo_url == "https://github.com/owner/repo"
        assert payload["stage"] == "ready"
        workspace.assert_not_awaited()
        assert provider.await_args.kwargs["data"] == {"repository_ids": [91], "permissions": {"contents": "read"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("executing", [False, True])
async def test_solution_setup_cannot_be_used_for_workspace_connection(monkeypatch, pending_setup, executing):
    setup_id, payload = pending_setup
    payload["purpose"] = "solution"
    monkeypatch.setattr(app, "_row", AsyncMock(return_value=(SimpleNamespace(), payload)))
    provider = AsyncMock()
    monkeypatch.setattr(app.GitAppCredentials, "get_token", provider)
    with pytest.raises(HTTPException) as error:
        await app.pending_config(None, setup_id, "actor", None, executing=executing)
    assert error.value.status_code == 409
    provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["credentials", "activation"])
async def test_ready_setup_cannot_replace_another_connected_repository(monkeypatch, pending_setup, operation):
    setup_id, payload = pending_setup
    payload["prior_generation"] = "original"
    monkeypatch.setattr(app, "_row", AsyncMock(return_value=(SimpleNamespace(), payload)))
    current = GitHubConfig("https://github.com/owner/connected", "personal", "main", "connected", generation="original")
    monkeypatch.setattr(app, "get_github_config", AsyncMock(return_value=current))
    monkeypatch.setattr(app, "lock_github_config", AsyncMock())
    save = AsyncMock()
    monkeypatch.setattr("src.services.github_config.save_app_github_config", save)
    redis = AsyncMock()
    monkeypatch.setattr(app, "get_shared_redis", redis)

    with pytest.raises(HTTPException) as error:
        if operation == "credentials":
            await app.pending_config(None, setup_id, "actor", None)
        else:
            await app.activate(None, app._selected_config(setup_id, payload, "actor", None), "main", "actor@example.com")

    assert error.value.status_code == 409
    assert "disconnect before changing repositories" in error.value.detail
    save.assert_not_awaited()
    redis.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("executing", [False, True])
@pytest.mark.parametrize("replaced", [False, True])
async def test_expired_setup_credentials_require_admitted_execution_and_unchanged_connection(monkeypatch, pending_setup, executing, replaced):
    setup_id, payload = pending_setup
    row = SimpleNamespace(created_by="actor", value_bytes=_get_fernet().encrypt(json.dumps(payload).encode()))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)))
    current = GitHubConfig("https://github.com/owner/repo", "personal", "main", "connected", generation="new") if replaced else None
    monkeypatch.setattr(app, "get_github_config", AsyncMock(return_value=current))
    monkeypatch.setattr(app, "get_shared_redis", AsyncMock(return_value=Redis()))
    provider = AsyncMock(return_value={"token": "installation-secret", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    monkeypatch.setattr(app, "_request", provider)

    if executing and not replaced:
        config = await app.pending_config(db, setup_id, "actor", None, executing=True)
        assert config.setup_executing
        assert await app.GitAppCredentials(db, config).get_token() == "installation-secret"
        provider.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as error:
            await app.pending_config(db, setup_id, "actor", None, executing=executing)
        assert error.value.status_code == (409 if executing else 410)
        provider.assert_not_awaited()
