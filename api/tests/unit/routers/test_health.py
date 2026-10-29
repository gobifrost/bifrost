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
        "s3": {"status": "healthy", "type": "s3"},
    }


class FakeRedisClient:
    def __init__(self, ping_error: Exception | None = None):
        self.ping_error = ping_error
        self.ping_count = 0
        self.close_count = 0

    async def ping(self):
        self.ping_count += 1
        if self.ping_error:
            raise self.ping_error

    async def aclose(self):
        self.close_count += 1


class FakeS3Client:
    def __init__(self, head_bucket_error: Exception | None = None):
        self.head_bucket_error = head_bucket_error
        self.head_bucket_count = 0

    async def head_bucket(self, Bucket):
        self.head_bucket_count += 1
        if self.head_bucket_error:
            raise self.head_bucket_error


class FakeS3ClientContext:
    def __init__(self, client: FakeS3Client):
        self.client = client
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self):
        self.enter_count += 1
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        self.exit_count += 1
        return None


class FakeS3Transport(FakeS3ClientContext):
    def __init__(self, head_bucket_error: Exception | None = None):
        super().__init__(FakeS3Client(head_bucket_error))


@pytest.mark.asyncio
async def test_health_is_liveness_and_does_not_check_dependencies(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("dependency checks should not run")

    monkeypatch.setattr(health, "build_health_components", fail_if_called)
    monkeypatch.setattr(health, "get_settings", _settings)

    result = await health.health_check()

    assert result.status == "healthy"
    assert result.environment == "test"


@pytest.mark.asyncio
async def test_live_is_liveness_and_does_not_check_dependencies(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("dependency checks should not run")

    monkeypatch.setattr(health, "build_health_components", fail_if_called)
    monkeypatch.setattr(health, "get_settings", _settings)

    result = await health.live_health_check()

    assert result.status == "healthy"
    assert result.environment == "test"


@pytest.mark.asyncio
async def test_ready_returns_healthy_when_core_dependencies_pass(monkeypatch):
    monkeypatch.setattr(health, "get_settings", _settings)
    monkeypatch.setattr(health, "build_health_components", _build_components)

    response = Response()
    result = await health.ready_health_check(response, DummyDB())

    assert response.status_code == 200
    assert result.status == "healthy"
    assert set(result.components) == {"database", "redis", "s3"}


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["database", "redis", "s3"])
async def test_ready_returns_503_when_required_dependency_fails(monkeypatch, component):
    monkeypatch.setattr(health, "get_settings", _settings)

    components = {
        "database": {"status": "healthy", "type": "postgresql"},
        "redis": {"status": "healthy", "type": "redis"},
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

    response = Response()
    result = await health.ready_health_check(response, DummyDB())

    assert response.status_code == 200
    assert result.status == "healthy"
    # Readiness checks exactly these dependencies; there is no message broker.
    assert set(result.components) == {"database", "redis", "s3"}
    assert result.components["s3"] == {"status": "not_configured", "type": "s3"}


@pytest.mark.asyncio
async def test_redis_check_reuses_cached_client(monkeypatch):
    await health.close_health_check_clients()
    clients: list[FakeRedisClient] = []

    def from_url(*args, **kwargs):
        client = FakeRedisClient()
        clients.append(client)
        return client

    monkeypatch.setattr(health.redis, "from_url", from_url)

    try:
        first_name, first_component = await health.check_redis(_settings())
        second_name, second_component = await health.check_redis(_settings())
    finally:
        await health.close_health_check_clients()

    assert first_name == "redis"
    assert second_name == "redis"
    assert first_component == {"status": "healthy", "type": "redis"}
    assert second_component == {"status": "healthy", "type": "redis"}
    assert len(clients) == 1
    assert clients[0].ping_count == 2
    assert clients[0].close_count == 1


@pytest.mark.asyncio
async def test_s3_check_reuses_cached_client(monkeypatch):
    await health.close_health_check_clients()
    clients: list[FakeS3Transport] = []

    def create_transport(*args, **kwargs):
        client = FakeS3Transport()
        clients.append(client)
        return client

    monkeypatch.setattr(health, "S3Transport", create_transport)

    try:
        first_name, first_component = await health.check_s3(_settings())
        second_name, second_component = await health.check_s3(_settings())
    finally:
        await health.close_health_check_clients()

    assert first_name == "s3"
    assert second_name == "s3"
    assert first_component == {"status": "healthy", "type": "s3"}
    assert second_component == {"status": "healthy", "type": "s3"}
    assert len(clients) == 1
    assert clients[0].enter_count == 1
    assert clients[0].client.head_bucket_count == 2
    assert clients[0].exit_count == 1


@pytest.mark.asyncio
async def test_s3_check_closes_cached_client_when_settings_change(monkeypatch):
    await health.close_health_check_clients()
    clients: list[FakeS3Transport] = []

    def create_transport(*args, **kwargs):
        client = FakeS3Transport()
        clients.append(client)
        return client

    monkeypatch.setattr(health, "S3Transport", create_transport)

    try:
        await health.check_s3(_settings())
        settings = _settings()
        settings.s3_bucket = "other-bucket-secret"
        await health.check_s3(settings)
    finally:
        await health.close_health_check_clients()

    assert len(clients) == 2
    assert [client.enter_count for client in clients] == [1, 1]
    assert [client.exit_count for client in clients] == [1, 1]


@pytest.mark.asyncio
async def test_failed_check_logs_component_without_secret_values(monkeypatch, caplog):
    settings = _settings()
    secret_values = [
        "redis-secret",
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
    assert caplog.messages == [
        "Readiness dependency check failed: component=database error_type=RuntimeError"
    ]
    output = str(component) + caplog.text
    for value in secret_values:
        assert value not in output


@pytest.mark.asyncio
async def test_detailed_uses_same_component_status_logic(monkeypatch):
    monkeypatch.setattr(health, "get_settings", _settings)
    async def build_components(db, settings):
        return {
            "database": {"status": "healthy", "type": "postgresql"},
            "redis": {"status": "unhealthy", "type": "redis", "error": "ConnectionError"},
                "s3": {"status": "healthy", "type": "s3"},
        }

    monkeypatch.setattr(health, "build_health_components", build_components)

    response = Response()
    result = await health.detailed_health_check(response, DummyDB())

    assert response.status_code == 503
    assert result.status == "unhealthy"
    assert isinstance(result.timestamp, datetime)
    assert result.timestamp.tzinfo == timezone.utc
