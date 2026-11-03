"""Rendered SDK coverage for per-thread integration client pooling."""

from __future__ import annotations

import asyncio
import gc
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from src.services.sdk_generator import generate_sdk


_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Example", "version": "1.0.0"},
    "paths": {
        "/widgets": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {"schema": {"type": "object"}}
                        },
                    }
                }
            }
        }
    },
}


class _FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}
    content = b'{"id": "widget-1"}'
    text = content.decode()

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"id": "widget-1"}


class _FakeSession:
    instances: list["_FakeSession"] = []

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.closed = False
        self.requests: list[tuple[str, str, dict[str, object]]] = []
        self.instances.append(self)

    def close(self) -> None:
        self.closed = True

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.requests.append((method, url, kwargs))
        return _FakeResponse()


class _Integrations:
    def __init__(self, integration: SimpleNamespace | None) -> None:
        self.integration = integration
        self.calls = 0

    async def get(self, name: str) -> SimpleNamespace | None:
        self.calls += 1
        assert name == "Example Integration"
        return self.integration


@pytest.fixture(autouse=True)
def _clear_fake_sessions() -> None:
    _FakeSession.instances.clear()


def _integration(
    *,
    config: dict[str, object],
    entity_id: str = "tenant-a",
    access_token: str | None = "oauth-token",
) -> SimpleNamespace:
    oauth = (
        None
        if access_token is None
        else SimpleNamespace(access_token=access_token)
    )
    return SimpleNamespace(
        integration_id="integration-1",
        entity_id=entity_id,
        config=config,
        oauth=oauth,
    )


def _render_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth_type: str,
    integration: SimpleNamespace | None,
):
    code, _ = generate_sdk(_SPEC, "Example Integration", auth_type)  # type: ignore[arg-type]
    module = types.ModuleType("rendered_example_sdk")
    exec(code, module.__dict__)
    monkeypatch.setattr(module.requests, "Session", _FakeSession)

    integrations = _Integrations(integration)
    bifrost = types.ModuleType("bifrost")
    bifrost.integrations = integrations
    monkeypatch.setitem(sys.modules, "bifrost", bifrost)
    return module, integrations


@pytest.mark.parametrize(
    ("auth_type", "config", "expected_header"),
    [
        (
            "api_key",
            {"base_url": "https://api.example.test", "header_name": "X-Api-Key", "api_key": "key"},
            ("X-Api-Key", "key"),
        ),
        (
            "basic",
            {"base_url": "https://api.example.test", "username": "user", "password": "pass"},
            ("Authorization", "Basic dXNlcjpwYXNz"),
        ),
        (
            "oauth",
            {"base_url": "https://api.example.test"},
            ("Authorization", "Bearer oauth-token"),
        ),
    ],
)
def test_rendered_sdk_reuses_current_thread_client_for_unchanged_integration(
    monkeypatch: pytest.MonkeyPatch,
    auth_type: str,
    config: dict[str, object],
    expected_header: tuple[str, str],
) -> None:
    module, integrations = _render_sdk(
        monkeypatch,
        auth_type=auth_type,
        integration=_integration(config=config),
    )
    lazy = module._LazyClient()

    first = asyncio.run(lazy._ensure_client())
    second = asyncio.run(lazy._ensure_client())

    assert first is second
    assert integrations.calls == 2
    assert len(_FakeSession.instances) == 1
    assert first.session.headers[expected_header[0]] == expected_header[1]
    lazy._close()
    assert first.session.closed


def test_rendered_sdk_preserves_generated_method_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, _ = _render_sdk(
        monkeypatch,
        auth_type="api_key",
        integration=_integration(
            config={"base_url": "https://api.example.test", "api_key": "key"}
        ),
    )

    response = asyncio.run(module.list_widgets())

    assert response.id == "widget-1"
    assert _FakeSession.instances[0].requests == [
        ("GET", "https://api.example.test/widgets", {"params": {}, "timeout": 30.0})
    ]


def test_rendered_sdk_replaces_and_closes_client_when_tenant_or_config_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        "base_url": "https://api.example.test",
        "username": "user",
        "password": "first",
    }
    integration = _integration(config=config)
    module, _ = _render_sdk(monkeypatch, auth_type="basic", integration=integration)
    lazy = module._LazyClient()

    first = asyncio.run(lazy._ensure_client())
    integration.entity_id = "tenant-b"
    second = asyncio.run(lazy._ensure_client())
    config["password"] = "second"
    third = asyncio.run(lazy._ensure_client())

    assert second is not first
    assert third is not second
    assert first.session.closed
    assert second.session.closed
    assert not third.session.closed


def test_rendered_oauth_sdk_replaces_client_after_access_token_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration = _integration(config={"base_url": "https://api.example.test"})
    module, _ = _render_sdk(monkeypatch, auth_type="oauth", integration=integration)
    lazy = module._LazyClient()

    first = asyncio.run(lazy._ensure_client())
    integration.oauth.access_token = "refreshed-token"
    second = asyncio.run(lazy._ensure_client())

    assert second is not first
    assert first.session.closed
    assert second.session.headers["Authorization"] == "Bearer refreshed-token"


def test_rendered_oauth_sdk_never_returns_cached_client_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration = _integration(config={"base_url": "https://api.example.test"})
    module, _ = _render_sdk(monkeypatch, auth_type="oauth", integration=integration)
    lazy = module._LazyClient()

    cached_client = asyncio.run(lazy._ensure_client())
    integration.oauth = None
    with pytest.raises(RuntimeError, match="OAuth not configured"):
        asyncio.run(lazy._ensure_client())

    assert len(_FakeSession.instances) == 1
    assert not cached_client.session.closed


def test_rendered_sdk_thread_exit_closes_its_thread_local_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, _ = _render_sdk(
        monkeypatch,
        auth_type="api_key",
        integration=_integration(
            config={"base_url": "https://api.example.test", "api_key": "key"}
        ),
    )
    lazy = module._LazyClient()
    main_thread_client = asyncio.run(lazy._ensure_client())
    worker_client: list[object] = []

    def create_worker_client() -> None:
        worker_client.append(asyncio.run(lazy._ensure_client()))

    worker = threading.Thread(target=create_worker_client)
    worker.start()
    worker.join()
    gc.collect()

    assert len(_FakeSession.instances) == 2
    assert worker_client[0] is not main_thread_client
    assert worker_client[0].session.closed
    assert not main_thread_client.session.closed
    lazy._close()


def test_rendered_sdk_closes_new_session_when_client_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, _ = _render_sdk(
        monkeypatch,
        auth_type="api_key",
        integration=_integration(
            config={"base_url": "https://api.example.test", "api_key": "key"}
        ),
    )

    def raise_during_construction(*args, **kwargs):
        raise RuntimeError("construction failed")

    module._ExampleClient = raise_during_construction
    with pytest.raises(RuntimeError, match="construction failed"):
        asyncio.run(module._LazyClient()._ensure_client())

    assert len(_FakeSession.instances) == 1
    assert _FakeSession.instances[0].closed
