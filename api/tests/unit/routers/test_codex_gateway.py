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
        self.key_id = uuid4()
        self.plaintext_key = VALID_GATEWAY_KEY

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
