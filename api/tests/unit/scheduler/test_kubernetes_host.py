"""Legacy Kubernetes-role control remains separate from remote execution."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import pytest

from src.scheduler import kubernetes_host


@pytest.mark.asyncio
async def test_control_host_waits_for_schema_then_stops_existing_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy scheduler role still runs the canonical Kubernetes loops."""
    stop = asyncio.Event()
    schema = AsyncMock()
    imported = AsyncMock()
    closed = AsyncMock()
    loop_stopped = asyncio.Event()

    @asynccontextmanager
    async def session():
        yield object()

    async def loop() -> None:
        await stop.wait()
        loop_stopped.set()

    started = AsyncMock(return_value=[asyncio.create_task(loop())])
    monkeypatch.setattr("src.config.get_settings", SimpleNamespace)
    monkeypatch.setattr("src.core.database.init_db", AsyncMock())
    monkeypatch.setattr("src.core.database.close_db", closed)
    monkeypatch.setattr("src.core.database.get_engine", object)
    monkeypatch.setattr("shared.schema_gate.wait_for_schema", schema)
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: session)
    monkeypatch.setattr("src.services.kubernetes_execution.import_kubernetes_policy_from_env", imported)
    monkeypatch.setattr("src.services.kubernetes_control.start_kubernetes_loops", started)

    stop.set()

    assert await kubernetes_host.run_control_host(stop) == 0
    schema.assert_awaited_once()
    imported.assert_awaited_once()
    started.assert_awaited_once_with(stop, ANY)
    assert loop_stopped.is_set()
    closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_launcher_wrapper_starts_no_control_helper_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = AsyncMock()
    monkeypatch.setattr(kubernetes_host, "has_service_account_token", lambda: False)
    monkeypatch.setattr(kubernetes_host.asyncio, "create_subprocess_exec", created)

    async with kubernetes_host.legacy_control_process():
        pass

    created.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_host_exits_when_the_role_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = AsyncMock()

    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr("src.config.get_settings", SimpleNamespace)
    monkeypatch.setattr("src.core.database.init_db", AsyncMock())
    monkeypatch.setattr("src.core.database.close_db", closed)
    monkeypatch.setattr("src.core.database.get_engine", object)
    monkeypatch.setattr("shared.schema_gate.wait_for_schema", AsyncMock())
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: session)
    monkeypatch.setattr("src.services.kubernetes_execution.import_kubernetes_policy_from_env", AsyncMock())
    loops = AsyncMock(return_value=[])
    monkeypatch.setattr("src.services.kubernetes_control.start_kubernetes_loops", loops)

    stop = asyncio.Event()
    assert await kubernetes_host.run_control_host(stop) == 0
    loops.assert_awaited_once()
    assert stop.is_set()
    closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_launcher_wrapper_stops_token_gated_control_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(returncode=None, terminate=Mock(), wait=AsyncMock(return_value=0))
    created = AsyncMock(return_value=process)
    monkeypatch.setattr(kubernetes_host, "has_service_account_token", lambda: True)
    monkeypatch.setattr(kubernetes_host.asyncio, "create_subprocess_exec", created)

    async with kubernetes_host.legacy_control_process():
        pass

    created.assert_awaited_once()
    assert created.await_args.args[1:] == ("-m", "src.scheduler.kubernetes_host")
    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once()


def test_launcher_control_wrapper_imports_no_application_runtime() -> None:
    """A token-mounted launcher must remain lean beside its controller helper."""
    source = """
import sys
import src.scheduler.kubernetes_host
assert not {
    'sqlalchemy', 'pydantic', 'httpx', 'src.config',
    'src.core.database', 'src.services.kubernetes_control',
} & sys.modules.keys()
"""
    subprocess.run(
        [sys.executable, "-c", source],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
