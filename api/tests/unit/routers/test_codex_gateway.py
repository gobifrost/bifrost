from asyncio import sleep

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routers.codex_gateway import get_codex_gateway_runtime, router
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
