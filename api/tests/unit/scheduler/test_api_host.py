from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.scheduler import api_host
from src.scheduler.api_host import ApiControlHost


class FakeControl:
    owner_id = "api-host:7"

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.started = asyncio.Event()

    async def start(self) -> None:
        self.starts += 1
        self.started.set()

    async def stop(self) -> None:
        self.stops += 1


@pytest.fixture
def gated(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(gate=asyncio.Event(), failures=[], heartbeats=[])

    async def wait_for_schema(_engine: object) -> None:
        if state.failures:
            raise state.failures.pop(0)
        await state.gate.wait()

    async def heartbeat(**kwargs) -> None:  # type: ignore[no-untyped-def]
        state.heartbeats.append(kwargs)
        await kwargs["stop_event"].wait()

    monkeypatch.setattr(api_host, "wait_for_schema", wait_for_schema)
    monkeypatch.setattr(api_host, "get_engine", object)
    monkeypatch.setattr(api_host, "replica_heartbeat_loop", heartbeat)
    state.removed = AsyncMock()
    monkeypatch.setattr(api_host, "remove_scheduler_replica", state.removed)
    return state


def _host(control: FakeControl) -> ApiControlHost:
    return ApiControlHost(control_plane=control, restart_backoff_seconds=0.01)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_api_serves_before_control_joins_after_schema_gate(
    gated: SimpleNamespace,
) -> None:
    control = FakeControl()
    host = _host(control)

    await host.start()  # returns without waiting for the schema gate
    await asyncio.sleep(0.01)
    assert control.starts == 0

    gated.gate.set()
    await asyncio.wait_for(control.started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert gated.heartbeats[0]["replica_id"] == "api-host:7"
    assert gated.heartbeats[0]["runs_platform_jobs"] is False

    await host.stop()
    assert control.stops == 1
    gated.removed.assert_awaited_once_with("api-host:7")


@pytest.mark.asyncio
async def test_shutdown_while_gated_never_starts_control(gated: SimpleNamespace) -> None:
    control = FakeControl()
    host = _host(control)
    await host.start()
    await asyncio.sleep(0)
    stopping = asyncio.create_task(host.stop())
    await asyncio.sleep(0.01)
    gated.gate.set()
    await asyncio.wait_for(stopping, timeout=1)

    assert control.starts == 0


@pytest.mark.asyncio
async def test_failed_startup_is_retried(gated: SimpleNamespace) -> None:
    """A database blip at boot must not leave the replica without control."""
    gated.failures.append(ConnectionError("database unavailable"))
    gated.gate.set()
    control = FakeControl()
    host = _host(control)

    await host.start()
    await asyncio.wait_for(control.started.wait(), timeout=1)

    assert control.starts == 1
    await host.stop()


@pytest.mark.asyncio
async def test_control_failure_restarts_control_without_stopping_http(
    gated: SimpleNamespace,
) -> None:
    gated.gate.set()
    control = FakeControl()
    host = _host(control)
    await host.start()
    await asyncio.wait_for(control.started.wait(), timeout=1)

    control.started.clear()
    failed = asyncio.create_task(asyncio.sleep(0), name="control-leadership")
    await asyncio.wait_for(failed, timeout=1)
    host._control_failed(failed)
    await asyncio.wait_for(control.started.wait(), timeout=1)

    # The failed generation was stopped (lease released) before restarting.
    assert control.stops == 1
    assert control.starts == 2
    await host.stop()
    assert control.stops == 2


@pytest.mark.asyncio
async def test_shutdown_waits_for_lease_release(gated: SimpleNamespace) -> None:
    gated.gate.set()
    released = asyncio.Event()

    class SlowStop(FakeControl):
        async def stop(self) -> None:
            await asyncio.sleep(0.05)
            released.set()

    host = _host(SlowStop())
    await host.start()
    await asyncio.sleep(0.01)
    await host.stop()

    # The lifespan closes the database only after this returns.
    assert released.is_set()


@pytest.mark.asyncio
async def test_lifespan_hosts_control_and_remote_launchers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI

    from src import main as api_main

    hosts: list[MagicMock] = []
    shutdown: list[str] = []

    def make_host() -> MagicMock:
        host = MagicMock()
        host.start = AsyncMock()
        host.stop = AsyncMock(side_effect=lambda: shutdown.append("control"))
        hosts.append(host)
        return host

    settings = MagicMock(
        default_user_email=None,
        default_user_password=None,
        environment="testing",
    )
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "init_db", AsyncMock())
    monkeypatch.setattr(api_main, "close_db", AsyncMock())
    monkeypatch.setattr(api_main, "register_dynamic_workflow_endpoints", AsyncMock())
    monkeypatch.setattr(api_main, "get_session_factory", MagicMock(side_effect=RuntimeError))
    shared_redis_start = AsyncMock()
    shared_redis_close = AsyncMock(side_effect=lambda: shutdown.append("redis"))
    monkeypatch.setattr(api_main, "initialize_shared_redis", shared_redis_start)
    monkeypatch.setattr(api_main, "close_shared_redis", shared_redis_close)
    monkeypatch.setattr(
        api_main.pubsub_manager,
        "close",
        AsyncMock(side_effect=lambda: shutdown.append("pubsub")),
    )
    monkeypatch.setattr(api_main, "close_health_check_clients", AsyncMock())
    monkeypatch.setattr(api_host, "ApiControlHost", make_host)
    remote_start = AsyncMock()
    remote_stop = AsyncMock(side_effect=lambda: shutdown.append("remote"))
    monkeypatch.setattr(api_main, "start_remote_control_host", remote_start)
    monkeypatch.setattr(api_main, "stop_remote_control_host", remote_stop)

    application = FastAPI()
    async with api_main.app_lifespan(application):
        shared_redis_start.assert_awaited_once()
        [host] = hosts
        host.start.assert_awaited_once()
        host.stop.assert_not_awaited()
        remote_start.assert_awaited_once_with(application)
        remote_stop.assert_not_awaited()
    host.stop.assert_awaited_once()
    remote_stop.assert_awaited_once()
    shared_redis_close.assert_awaited_once()
    assert shutdown.index("remote") < shutdown.index("control") < shutdown.index("pubsub")
    assert shutdown.index("pubsub") < shutdown.index("redis")

@pytest.mark.asyncio
async def test_lifespan_imports_kubernetes_settings_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build enqueued right after an upgrade must not read the defaults."""
    import os
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from src import main as api_main
    from src.services import kubernetes_execution

    session = object()

    @asynccontextmanager
    async def open_session():  # type: ignore[no-untyped-def]
        yield session

    settings = MagicMock(
        default_user_email=None,
        default_user_password=None,
        environment="testing",
    )
    imported = AsyncMock()
    monkeypatch.setattr(kubernetes_execution, "import_kubernetes_policy_from_env", imported)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "init_db", AsyncMock())
    monkeypatch.setattr(api_main, "close_db", AsyncMock())
    monkeypatch.setattr(api_main, "register_dynamic_workflow_endpoints", AsyncMock())
    monkeypatch.setattr(api_main, "get_session_factory", lambda: open_session)
    monkeypatch.setattr(api_main, "initialize_shared_redis", AsyncMock())
    monkeypatch.setattr(api_main, "close_shared_redis", AsyncMock())
    monkeypatch.setattr(api_main.pubsub_manager, "close", AsyncMock())
    monkeypatch.setattr(api_main, "close_health_check_clients", AsyncMock())
    monkeypatch.setattr(api_host, "ApiControlHost", lambda: MagicMock(start=AsyncMock(), stop=AsyncMock()))
    monkeypatch.setattr(api_main, "start_remote_control_host", AsyncMock())
    monkeypatch.setattr(api_main, "stop_remote_control_host", AsyncMock())

    async with api_main.app_lifespan(FastAPI()):
        imported.assert_awaited_once_with(session, os.environ)
