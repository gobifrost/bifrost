from asyncio import sleep
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserPrincipal, get_current_active_user
from src.routers.codex_gateway import (
    get_codex_gateway_repository,
    get_codex_gateway_runtime,
    router,
)
from src.repositories.codex_gateway import CodexGatewayKeyMaterial
from src.services.codex_gateway.runtime import (
    CODEX_GATEWAY_KEY_HEADER,
    CodexGatewayResponse,
)

VALID_GATEWAY_KEY = f"bfck_{'a' * 43}"


class FakeRuntime:
    def __init__(self):
        self.calls = []

    async def create_response(self, **kwargs):
        await sleep(0)
        self.calls.append(kwargs)
        return CodexGatewayResponse(
            status_code=200,
            body={"id": "resp_route_test", "output": []},
        )


class FakeRepository:
    def __init__(self):
        self.created = []
        self.listed_for = []
        self.revoked = []
        self.oauth_upserts = []
        self.oauth_revoked_for = []
        self.oauth_status_lookups = []
        self.key_id = uuid4()
        self.oauth_account_id = uuid4()
        self.plaintext_key = VALID_GATEWAY_KEY
        self.upstream_account = None

    async def create_gateway_key(self, **kwargs):
        await sleep(0)
        self.created.append(kwargs)
        record = type(
            "GatewayKey",
            (),
            {
                "id": self.key_id,
                "user_id": kwargs["user_id"],
                "project_id": kwargs.get("project_id"),
                "name": kwargs["name"],
                "allowed_models": kwargs.get("allowed_models") or [],
                "denied_models": kwargs.get("denied_models") or [],
                "daily_limit": kwargs.get("daily_limit"),
                "monthly_limit": kwargs.get("monthly_limit"),
                "status": "active",
                "created_at": None,
                "revoked_at": None,
                "last_used_at": None,
            },
        )()
        return CodexGatewayKeyMaterial(
            record=record,
            plaintext_key=self.plaintext_key,
        )

    async def list_gateway_keys_for_user(self, user_id):
        await sleep(0)
        self.listed_for.append(user_id)
        return [
            type(
                "GatewayKey",
                (),
                {
                    "id": self.key_id,
                    "user_id": user_id,
                    "project_id": None,
                    "name": "developer workstation",
                    "allowed_models": ["gpt-5.1-codex"],
                    "denied_models": [],
                    "daily_limit": 100,
                    "monthly_limit": None,
                    "status": "active",
                    "created_at": None,
                    "revoked_at": None,
                    "last_used_at": None,
                },
            )()
        ]

    async def revoke_gateway_key_for_user(self, *, key_id, user_id):
        await sleep(0)
        self.revoked.append({"key_id": key_id, "user_id": user_id})
        return type(
            "GatewayKey",
            (),
            {
                "id": key_id,
                "user_id": user_id,
                "project_id": None,
                "name": "developer workstation",
                "allowed_models": ["gpt-5.1-codex"],
                "denied_models": [],
                "daily_limit": 100,
                "monthly_limit": None,
                "status": "revoked",
                "created_at": None,
                "revoked_at": None,
                "last_used_at": None,
            },
        )()

    async def get_active_upstream_account_for_user(self, user_id, provider="chatgpt_codex"):
        await sleep(0)
        self.oauth_status_lookups.append({"user_id": user_id, "provider": provider})
        if self.upstream_account is None:
            return None
        if self.upstream_account.user_id != user_id:
            return None
        if self.upstream_account.provider != provider:
            return None
        return self.upstream_account

    async def upsert_upstream_account_for_user(self, **kwargs):
        await sleep(0)
        self.oauth_upserts.append(kwargs)
        account = type(
            "UpstreamAccount",
            (),
            {
                "id": self.oauth_account_id,
                "user_id": kwargs["user_id"],
                "provider": kwargs.get("provider", "chatgpt_codex"),
                "upstream_subject": kwargs["upstream_subject"],
                "upstream_email": kwargs.get("upstream_email"),
                "upstream_workspace_id": kwargs.get("upstream_workspace_id"),
                "access_token_expires_at": kwargs.get("access_token_expires_at"),
                "scopes": kwargs.get("scopes") or [],
                "last_refresh_at": None,
                "last_used_at": None,
                "revoked_at": None,
                "created_at": None,
                "updated_at": None,
            },
        )()
        self.upstream_account = account
        return account

    async def revoke_upstream_account_for_user(self, *, user_id, provider="chatgpt_codex"):
        await sleep(0)
        self.oauth_revoked_for.append({"user_id": user_id, "provider": provider})
        if self.upstream_account is None:
            return None
        self.upstream_account.revoked_at = "now"
        return self.upstream_account


def _principal(user_id):
    return UserPrincipal(
        user_id=user_id,
        email="dev@example.test",
        organization_id=uuid4(),
        is_active=True,
        is_superuser=False,
    )


def test_v1_responses_uses_openai_compatible_bearer_key():
    app = FastAPI()
    app.include_router(router)
    runtime = FakeRuntime()
    app.dependency_overrides[get_codex_gateway_runtime] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {VALID_GATEWAY_KEY}"},
        json={"model": "gpt-5.1-codex", "input": "do not log me"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_route_test"
    [runtime_call] = runtime.calls
    assert runtime_call["gateway_key"] == VALID_GATEWAY_KEY
    assert runtime_call["payload"] == {
        "model": "gpt-5.1-codex",
        "input": "do not log me",
    }


def test_api_v1_responses_uses_same_gateway_facade():
    app = FastAPI()
    app.include_router(router)
    runtime = FakeRuntime()
    app.dependency_overrides[get_codex_gateway_runtime] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {VALID_GATEWAY_KEY}"},
        json={"model": "gpt-5.1-codex", "input": "api routed path"},
    )

    assert response.status_code == 200
    [runtime_call] = runtime.calls
    assert runtime_call["gateway_key"] == VALID_GATEWAY_KEY
    assert runtime_call["payload"] == {
        "model": "gpt-5.1-codex",
        "input": "api routed path",
    }


def test_v1_responses_rejects_non_object_payload_before_runtime():
    app = FastAPI()
    app.include_router(router)
    runtime = FakeRuntime()
    app.dependency_overrides[get_codex_gateway_runtime] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {VALID_GATEWAY_KEY}"},
        json=["not", "an", "object"],
    )

    assert 400 <= response.status_code < 500
    assert runtime.calls == []


def test_v1_responses_uses_fallback_gateway_key_header():
    app = FastAPI()
    app.include_router(router)
    runtime = FakeRuntime()
    app.dependency_overrides[get_codex_gateway_runtime] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        headers={CODEX_GATEWAY_KEY_HEADER: VALID_GATEWAY_KEY},
        json={"model": "gpt-5.1-codex", "input": "fallback header"},
    )

    assert response.status_code == 200
    [runtime_call] = runtime.calls
    assert runtime_call["gateway_key"] == VALID_GATEWAY_KEY
    assert runtime_call["payload"] == {
        "model": "gpt-5.1-codex",
        "input": "fallback header",
    }


def test_v1_responses_rejects_missing_gateway_key_before_runtime():
    app = FastAPI()
    app.include_router(router)
    runtime = FakeRuntime()
    app.dependency_overrides[get_codex_gateway_runtime] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        json={"model": "gpt-5.1-codex", "input": "missing key"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_gateway_key"
    assert runtime.calls == []


def test_create_gateway_key_returns_plaintext_once_and_audits(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    repository = FakeRepository()
    user_id = uuid4()
    audit = AsyncMock()
    app.dependency_overrides[get_codex_gateway_repository] = lambda: repository
    app.dependency_overrides[get_current_active_user] = lambda: _principal(user_id)
    monkeypatch.setattr("src.routers.codex_gateway.emit_audit", audit)
    client = TestClient(app)

    response = client.post(
        "/api/codex-gateway/keys",
        json={
            "name": "developer workstation",
            "allowed_models": ["gpt-5.1-codex"],
            "daily_limit": 100,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["key"] == VALID_GATEWAY_KEY
    assert body["record"]["name"] == "developer workstation"
    assert "key_hash" not in body["record"]
    assert repository.created == [
        {
            "user_id": user_id,
            "project_id": None,
            "name": "developer workstation",
            "allowed_models": ["gpt-5.1-codex"],
            "denied_models": [],
            "daily_limit": 100,
            "monthly_limit": None,
        }
    ]
    audit.assert_awaited_once()


def test_list_gateway_keys_never_exposes_plaintext_or_hash():
    app = FastAPI()
    app.include_router(router)
    repository = FakeRepository()
    user_id = uuid4()
    app.dependency_overrides[get_codex_gateway_repository] = lambda: repository
    app.dependency_overrides[get_current_active_user] = lambda: _principal(user_id)
    client = TestClient(app)

    response = client.get("/api/codex-gateway/keys")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["name"] == "developer workstation"
    assert "key" not in body["items"][0]
    assert "key_hash" not in body["items"][0]
    assert repository.listed_for == [user_id]


def test_revoke_gateway_key_is_user_scoped_and_audited(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    repository = FakeRepository()
    user_id = uuid4()
    key_id = uuid4()
    audit = AsyncMock()
    app.dependency_overrides[get_codex_gateway_repository] = lambda: repository
    app.dependency_overrides[get_current_active_user] = lambda: _principal(user_id)
    monkeypatch.setattr("src.routers.codex_gateway.emit_audit", audit)
    client = TestClient(app)

    response = client.delete(f"/api/codex-gateway/keys/{key_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
    assert repository.revoked == [{"key_id": key_id, "user_id": user_id}]
    audit.assert_awaited_once()


def test_v1_responses_rejects_malformed_gateway_key_before_runtime():
    app = FastAPI()
    app.include_router(router)
    runtime = FakeRuntime()
    app.dependency_overrides[get_codex_gateway_runtime] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer not-a-bifrost-key"},
        json={"model": "gpt-5.1-codex", "input": "bad key"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_gateway_key"
    assert runtime.calls == []


def test_codex_gateway_oauth_status_reports_disconnected_without_tokens():
    app = FastAPI()
    app.include_router(router)
    repository = FakeRepository()
    user_id = uuid4()
    app.dependency_overrides[get_codex_gateway_repository] = lambda: repository
    app.dependency_overrides[get_current_active_user] = lambda: _principal(user_id)
    client = TestClient(app)

    response = client.get("/api/codex-gateway/oauth/status")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "connected": False,
        "provider": "chatgpt_codex",
        "account": None,
        "supported_connect_methods": ["device_code", "auth_cache_import"],
    }
    assert repository.oauth_status_lookups == [
        {"user_id": user_id, "provider": "chatgpt_codex"}
    ]


def test_start_codex_oauth_connect_prefers_device_code_with_import_fallback():
    app = FastAPI()
    app.include_router(router)
    user_id = uuid4()
    app.dependency_overrides[get_current_active_user] = lambda: _principal(user_id)
    client = TestClient(app)

    response = client.post("/api/codex-gateway/oauth/connect")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "chatgpt_codex",
        "preferred_method": "device_code",
        "device_code_enabled": True,
        "client_command": "codex login --device-auth",
        "fallback_import_endpoint": "/api/codex-gateway/oauth/import-auth-cache",
    }


def test_import_codex_auth_cache_stores_tokens_without_returning_them(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    repository = FakeRepository()
    user_id = uuid4()
    audit = AsyncMock()
    app.dependency_overrides[get_codex_gateway_repository] = lambda: repository
    app.dependency_overrides[get_current_active_user] = lambda: _principal(user_id)
    monkeypatch.setattr("src.routers.codex_gateway.emit_audit", audit)
    client = TestClient(app)

    response = client.post(
        "/api/codex-gateway/oauth/import-auth-cache",
        json={
            "auth_cache": {
                "tokens": {
                    "access_token": "access-token-secret",
                    "refresh_token": "refresh-token-secret",
                    "id_token": "id-token",
                    "expires_at": "2026-05-25T12:00:00Z",
                    "scope": "openid profile offline_access",
                },
                "account": {
                    "sub": "chatgpt-user-123",
                    "email": "dev@example.test",
                    "workspace_id": "workspace-midtown",
                },
            }
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["connected"] is True
    assert body["account"]["upstream_subject"] == "chatgpt-user-123"
    assert "access-token-secret" not in response.text
    assert "refresh-token-secret" not in response.text
    assert "refresh_token" not in response.text
    assert len(repository.oauth_upserts) == 1
    [oauth_upsert] = repository.oauth_upserts
    assert oauth_upsert["user_id"] == user_id
    assert oauth_upsert["access_token"] == "access-token-secret"
    assert oauth_upsert["refresh_token"] == "refresh-token-secret"
    audit.assert_awaited_once()


def test_disconnect_codex_oauth_revokes_user_account_and_audits(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    repository = FakeRepository()
    user_id = uuid4()
    repository.upstream_account = type(
        "UpstreamAccount",
        (),
        {
            "id": repository.oauth_account_id,
            "user_id": user_id,
            "provider": "chatgpt_codex",
            "upstream_subject": "chatgpt-user-123",
            "upstream_email": "dev@example.test",
            "upstream_workspace_id": "workspace-midtown",
            "access_token_expires_at": None,
            "scopes": [],
            "last_refresh_at": None,
            "last_used_at": None,
            "revoked_at": None,
        },
    )()
    audit = AsyncMock()
    app.dependency_overrides[get_codex_gateway_repository] = lambda: repository
    app.dependency_overrides[get_current_active_user] = lambda: _principal(user_id)
    monkeypatch.setattr("src.routers.codex_gateway.emit_audit", audit)
    client = TestClient(app)

    response = client.delete("/api/codex-gateway/oauth")

    assert response.status_code == 200
    assert response.json() == {"connected": False, "revoked": True}
    assert repository.oauth_revoked_for == [
        {"user_id": user_id, "provider": "chatgpt_codex"}
    ]
    audit.assert_awaited_once()
