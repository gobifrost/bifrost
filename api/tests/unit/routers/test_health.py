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


class FakeRabbitMQChannel:
    def __init__(self):
        self.close_count = 0

    async def close(self):
        self.close_count += 1


class FakeRabbitMQConnection:
    def __init__(self, channel_error: Exception | None = None):
        self.channel_error = channel_error
        self.channels: list[FakeRabbitMQChannel] = []
        self.close_count = 0
        self.is_closed = False

    async def channel(self):
        if self.channel_error:
            raise self.channel_error
        channel = FakeRabbitMQChannel()
        self.channels.append(channel)
        return channel

    async def close(self):
        self.close_count += 1
        self.is_closed = True


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


class FakeS3Session:
    def __init__(self, head_bucket_error: Exception | None = None):
        self.client = FakeS3Client(head_bucket_error)
        self.context = FakeS3ClientContext(self.client)
        self.create_client_count = 0

    def create_client(self, *args, **kwargs):
        self.create_client_count += 1
        return self.context


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
    assert set(result.components) == {"database", "redis", "rabbitmq", "s3"}


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["database", "redis", "rabbitmq", "s3"])
async def test_ready_returns_503_when_required_dependency_fails(monkeypatch, component):
    monkeypatch.setattr(health, "get_settings", _settings)

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
async def test_rabbitmq_check_reuses_cached_connection(monkeypatch):
    await health.close_rabbitmq_health_connection()
    connections: list[FakeRabbitMQConnection] = []

    async def connect_robust(url):
        connection = FakeRabbitMQConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(health.aio_pika, "connect_robust", connect_robust)

    try:
        first_name, first_component = await health.check_rabbitmq(_settings())
        second_name, second_component = await health.check_rabbitmq(_settings())
    finally:
        await health.close_rabbitmq_health_connection()

    assert first_name == "rabbitmq"
    assert second_name == "rabbitmq"
    assert first_component == {"status": "healthy", "type": "rabbitmq"}
    assert second_component == {"status": "healthy", "type": "rabbitmq"}
    assert len(connections) == 1
    assert len(connections[0].channels) == 2
    assert [channel.close_count for channel in connections[0].channels] == [1, 1]


@pytest.mark.asyncio
async def test_rabbitmq_check_reports_unhealthy_when_channel_fails(monkeypatch):
    await health.close_rabbitmq_health_connection()
    failed_connection = FakeRabbitMQConnection(channel_error=RuntimeError("channel failed"))

    async def connect_robust(url):
        return failed_connection

    monkeypatch.setattr(health.aio_pika, "connect_robust", connect_robust)

    try:
        component_name, component = await health.check_rabbitmq(_settings())
    finally:
        await health.close_rabbitmq_health_connection()

    assert component_name == "rabbitmq"
    assert component == {
        "status": "unhealthy",
        "type": "rabbitmq",
        "error": "RuntimeError",
    }
    assert failed_connection.close_count == 1


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
    sessions: list[FakeS3Session] = []

    def get_session():
        session = FakeS3Session()
        sessions.append(session)
        return session

    monkeypatch.setattr(health, "get_session", get_session)

    try:
        first_name, first_component = await health.check_s3(_settings())
        second_name, second_component = await health.check_s3(_settings())
    finally:
        await health.close_health_check_clients()

    assert first_name == "s3"
    assert second_name == "s3"
    assert first_component == {"status": "healthy", "type": "s3"}
    assert second_component == {"status": "healthy", "type": "s3"}
    assert len(sessions) == 1
    assert sessions[0].create_client_count == 1
    assert sessions[0].context.enter_count == 1
    assert sessions[0].client.head_bucket_count == 2
    assert sessions[0].context.exit_count == 1


@pytest.mark.asyncio
async def test_s3_check_closes_cached_client_when_settings_change(monkeypatch):
    await health.close_health_check_clients()
    sessions: list[FakeS3Session] = []

    def get_session():
        session = FakeS3Session()
        sessions.append(session)
        return session

    monkeypatch.setattr(health, "get_session", get_session)

    try:
        await health.check_s3(_settings())
        settings = _settings()
        settings.s3_bucket = "other-bucket-secret"
        await health.check_s3(settings)
    finally:
        await health.close_health_check_clients()

    assert len(sessions) == 2
    assert [session.create_client_count for session in sessions] == [1, 1]
    assert sessions[0].context.exit_count == 1
    assert sessions[1].context.exit_count == 1


@pytest.mark.asyncio
async def test_rabbitmq_check_reports_unhealthy_when_connect_fails(monkeypatch):
    await health.close_rabbitmq_health_connection()

    async def connect_robust(url):
        raise ConnectionError("cannot connect")

    monkeypatch.setattr(health.aio_pika, "connect_robust", connect_robust)

    component_name, component = await health.check_rabbitmq(_settings())

    assert component_name == "rabbitmq"
    assert component == {
        "status": "unhealthy",
        "type": "rabbitmq",
        "error": "ConnectionError",
    }


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
    monkeypatch.setattr(health, "get_settings", _settings)
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
