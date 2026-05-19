from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import Response

from src.routers import health


class DummyDB:
    async def execute(self, statement):
        return None


def _settings(s3_configured: bool = True):
    return SimpleNamespace(
        environment="test",
        redis_url="redis://redis-secret@example:6379/0",
        rabbitmq_url="amqp://rabbit-secret@example:5672/",
        s3_configured=s3_configured,
        s3_bucket="bucket-secret" if s3_configured else None,
        s3_endpoint_url="http://object-store-secret:8333" if s3_configured else None,
        s3_access_key="access-secret" if s3_configured else None,
        s3_secret_key="secret-secret" if s3_configured else None,
        s3_region="us-east-1",
    )


async def _healthy_component(name: str, type_: str):
    return name, {"status": "healthy", "type": type_}


async def _build_components(db, settings):
    return {
        "database": {"status": "healthy", "type": "postgresql"},
        "redis": {"status": "healthy", "type": "redis"},
        "rabbitmq": {"status": "healthy", "type": "rabbitmq"},
        "s3": {"status": "healthy", "type": "s3"},
    }


@pytest.mark.asyncio
async def test_health_is_liveness_and_does_not_check_dependencies(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("dependency checks should not run")

    monkeypatch.setattr(health, "build_health_components", fail_if_called)
    monkeypatch.setattr(health, "get_settings", lambda: _settings())

    result = await health.health_check()

    assert result.status == "healthy"
    assert result.environment == "test"


@pytest.mark.asyncio
async def test_live_is_liveness_and_does_not_check_dependencies(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("dependency checks should not run")

    monkeypatch.setattr(health, "build_health_components", fail_if_called)
    monkeypatch.setattr(health, "get_settings", lambda: _settings())

    result = await health.live_health_check()

    assert result.status == "healthy"
    assert result.environment == "test"


@pytest.mark.asyncio
async def test_ready_returns_healthy_when_core_dependencies_pass(monkeypatch):
    monkeypatch.setattr(health, "get_settings", lambda: _settings())
    monkeypatch.setattr(health, "build_health_components", _build_components)

    response = Response()
    result = await health.ready_health_check(response, DummyDB())

    assert response.status_code == 200
    assert result.status == "healthy"
    assert set(result.components) == {"database", "redis", "rabbitmq", "s3"}


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["database", "redis", "rabbitmq", "s3"])
async def test_ready_returns_503_when_required_dependency_fails(monkeypatch, component):
    monkeypatch.setattr(health, "get_settings", lambda: _settings())

    components = {
        "database": {"status": "healthy", "type": "postgresql"},
        "redis": {"status": "healthy", "type": "redis"},
        "rabbitmq": {"status": "healthy", "type": "rabbitmq"},
        "s3": {"status": "healthy", "type": "s3"},
    }
    components[component] = {
        "status": "unhealthy",
        "type": components[component]["type"],
        "error": "RuntimeError",
    }
    async def build_components(db, settings):
        return components

    monkeypatch.setattr(health, "build_health_components", build_components)

    response = Response()
    result = await health.ready_health_check(response, DummyDB())

    assert response.status_code == 503
    assert result.status == "unhealthy"


@pytest.mark.asyncio
async def test_s3_not_configured_does_not_fail_readiness(monkeypatch):
    monkeypatch.setattr(health, "get_settings", lambda: _settings(s3_configured=False))
    monkeypatch.setattr(health, "check_database", lambda db: _healthy_component("database", "postgresql"))
    monkeypatch.setattr(health, "check_redis", lambda settings: _healthy_component("redis", "redis"))
    monkeypatch.setattr(
        health,
        "check_rabbitmq",
        lambda settings: _healthy_component("rabbitmq", "rabbitmq"),
    )

    response = Response()
    result = await health.ready_health_check(response, DummyDB())

    assert response.status_code == 200
    assert result.status == "healthy"
    assert result.components["s3"] == {"status": "not_configured", "type": "s3"}


@pytest.mark.asyncio
async def test_error_output_does_not_include_secret_values(monkeypatch):
    settings = _settings()
    secret_values = [
        "redis-secret",
        "rabbit-secret",
        "bucket-secret",
        "object-store-secret",
        "access-secret",
        "secret-secret",
    ]

    monkeypatch.setattr(health, "get_settings", lambda: settings)

    async def failing_check():
        raise RuntimeError(" ".join(secret_values))

    component_name, component = await health._checked_component(
        "database",
        "postgresql",
        failing_check(),
    )

    assert component_name == "database"
    assert component["status"] == "unhealthy"
    output = str(component)
    for value in secret_values:
        assert value not in output


@pytest.mark.asyncio
async def test_detailed_uses_same_component_status_logic(monkeypatch):
    monkeypatch.setattr(health, "get_settings", lambda: _settings())
    async def build_components(db, settings):
        return {
            "database": {"status": "healthy", "type": "postgresql"},
            "redis": {"status": "unhealthy", "type": "redis", "error": "ConnectionError"},
            "rabbitmq": {"status": "healthy", "type": "rabbitmq"},
            "s3": {"status": "healthy", "type": "s3"},
        }

    monkeypatch.setattr(health, "build_health_components", build_components)

    response = Response()
    result = await health.detailed_health_check(response, DummyDB())

    assert response.status_code == 503
    assert result.status == "unhealthy"
    assert isinstance(result.timestamp, datetime)
    assert result.timestamp.tzinfo == timezone.utc
