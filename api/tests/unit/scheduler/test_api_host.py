from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.scheduler import api_host
from src.scheduler.api_host import API_CONTROL_JOB_SLOTS, ApiControlHost


class FakeControl:
    owner_id = "api-host:7"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stop_calls = 0

    async def start(self) -> None:
        self.started.set()

    async def stop(self) -> None:
        self.stop_calls += 1


@pytest.fixture
def gated(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    gate = asyncio.Event()
    heartbeats: list[dict] = []

    async def wait_for_schema(_engine: object) -> None:
        await gate.wait()

    async def heartbeat(**kwargs) -> None:  # type: ignore[no-untyped-def]
        heartbeats.append(kwargs)
        await kwargs["stop_event"].wait()

    monkeypatch.setattr(api_host, "wait_for_schema", wait_for_schema)
    monkeypatch.setattr(api_host, "get_engine", lambda: object())
    monkeypatch.setattr(api_host, "replica_heartbeat_loop", heartbeat)
    removed = AsyncMock()
    monkeypatch.setattr(api_host, "remove_scheduler_replica", removed)
    return SimpleNamespace(gate=gate, heartbeats=heartbeats, removed=removed)


@pytest.mark.asyncio
async def test_api_serves_before_control_joins_after_schema_gate(
    gated: SimpleNamespace,
) -> None:
    control = FakeControl()
    host = ApiControlHost(control_plane=control)  # type: ignore[arg-type]

    await host.start()  # returns without waiting for the schema gate
    await asyncio.sleep(0.01)
    assert not control.started.is_set()

    gated.gate.set()
    await asyncio.wait_for(control.started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert gated.heartbeats[0]["replica_id"] == "api-host:7"
    assert gated.heartbeats[0]["job_slots"] == API_CONTROL_JOB_SLOTS == 0

    await host.stop()
    assert control.stop_calls == 1
    gated.removed.assert_awaited_once_with("api-host:7")


@pytest.mark.asyncio
async def test_shutdown_while_gated_never_starts_control(gated: SimpleNamespace) -> None:
    control = FakeControl()
    host = ApiControlHost(control_plane=control)  # type: ignore[arg-type]
    await host.start()
    await host.stop()
    gated.gate.set()
    await asyncio.sleep(0.01)

    assert not control.started.is_set()
    gated.removed.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_failure_gives_up_control_without_stopping_http(
    gated: SimpleNamespace,
) -> None:
    host = ApiControlHost(control_plane=FakeControl())  # type: ignore[arg-type]
    control = host.control
    gated.gate.set()
    await host.start()
    await asyncio.sleep(0.01)

    failed = asyncio.create_task(asyncio.sleep(0), name="control-leadership")
    await failed
    host._control_failed(failed)
    assert host._failure_task is not None
    await host._failure_task

    assert control.stop_calls == 1  # type: ignore[attr-defined]
    await host.stop()  # lifespan shutdown afterwards is a no-op
    assert control.stop_calls == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_lifespan_hosts_control_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI

    from src import main as api_main

    for setting, expected in (("scheduler", 0), ("api", 1)):
        hosts: list[MagicMock] = []

        def make_host() -> MagicMock:
            host = MagicMock()
            host.start = AsyncMock()
            host.stop = AsyncMock()
            hosts.append(host)
            return host

        settings = MagicMock(
            control_plane_host=setting,
            default_user_email=None,
            default_user_password=None,
            environment="testing",
        )
        monkeypatch.setattr(api_main, "get_settings", lambda: settings)
        monkeypatch.setattr(api_main, "init_db", AsyncMock())
        monkeypatch.setattr(api_main, "close_db", AsyncMock())
        monkeypatch.setattr(api_main, "register_dynamic_workflow_endpoints", AsyncMock())
        monkeypatch.setattr(api_main, "get_session_factory", MagicMock(side_effect=RuntimeError))
        monkeypatch.setattr(api_main.pubsub_manager, "close", AsyncMock())
        monkeypatch.setattr(api_main, "close_health_check_clients", AsyncMock())
        monkeypatch.setattr(api_host, "ApiControlHost", make_host)

        async with api_main.app_lifespan(FastAPI()):
            assert len(hosts) == expected
            if hosts:
                hosts[0].start.assert_awaited_once()
                hosts[0].stop.assert_not_awaited()
        if hosts:
            hosts[0].stop.assert_awaited_once()
