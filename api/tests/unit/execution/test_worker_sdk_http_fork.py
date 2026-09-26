"""Gate A/C1: a real forked child reaches config CRUD over the worker socket.

This is the end-to-end proof behind Gate A and the config slice of Gate C. A
real ``TemplateProcess`` forks a one-shot child and injects the worker's Unix
socket path exactly as the pool does. The test process serves the **real**
``/api/sdk/config/get|set|list|delete`` routes on that socket via uvicorn,
against the real database engine. The child's network API is dead by
environment, and it receives no database or provider credentials, so a correct
value proves:

- the existing routes are reused (not copied) and resolve the value;
- the child used the socket transport, not the network API (zero
  API-container requests: ``BIFROST_API_URL`` points at a dead port);
- the parent owns DB access; the child holds no DB credential.

Marked ``slow`` like the other real-fork tests: template boot costs seconds.
"""

import asyncio
import base64
import contextlib
import os
import time
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

pytestmark = pytest.mark.slow


def _no_cache():
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    return patch(
        "src.core.cache.redis_client.get_shared_redis",
        new=AsyncMock(return_value=redis),
    )


@pytest_asyncio.fixture
async def committed_config(async_session_factory):
    """Seed a globally-scoped config row in a committed session."""
    from sqlalchemy import delete

    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm.config import Config as ConfigModel

    created: list[str] = []

    async def _seed(key: str, value: object) -> None:
        async with async_session_factory() as session:
            session.add(
                ConfigModel(
                    key=key,
                    value={"value": value},
                    config_type=ConfigTypeEnum("string"),
                    organization_id=None,
                    updated_by="worker-sdk-http-fork-test",
                )
            )
            await session.commit()
        created.append(key)

    yield _seed

    async with async_session_factory() as session:
        for key in created:
            await session.execute(
                delete(ConfigModel).where(
                    ConfigModel.key == key,
                    ConfigModel.organization_id.is_(None),
                )
            )
        await session.commit()


def _script_for(key: str) -> str:
    source = (
        "import os, sys\n"
        "from bifrost import config\n"
        "from bifrost.client import get_engine_socket_path\n"
        f"await config.set({key!r}, 'fork-set-value')\n"
        f"_get = await config.get({key!r})\n"
        "_list = await config.list()\n"
        f"_deleted = await config.delete({key!r})\n"
        f"_after = await config.get({key!r}, default='missing')\n"
        "result = {\n"
        "    'value': _get,\n"
        f"    'listed': _list.data.get({key!r}),\n"
        "    'deleted': _deleted,\n"
        "    'after_delete': _after,\n"
        "    'socket_path': get_engine_socket_path(),\n"
        "    'had_db_url': (\n"
        "        'BIFROST_DATABASE_URL' in os.environ\n"
        "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
        "    ),\n"
        "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
        "}\n"
    )
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str, engine_token: str) -> dict[str, Any]:
    return {
        "execution_id": f"wsdk-fork-{uuid4().hex[:8]}",
        "name": "worker-sdk-http-fork-test",
        "code": code_b64,
        "parameters": {},
        "caller": {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "email": "engine@bifrost.internal",
            "name": "Bifrost Engine",
        },
        "organization": None,
        "tags": [],
        "timeout_seconds": 120,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": True,
        "engine_token": engine_token,
    }


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


@pytest.mark.asyncio
async def test_forked_child_config_crud_over_worker_socket(
    committed_config, monkeypatch
):
    from src.core.security import mint_engine_token

    key = f"wsdk-fork-{uuid4().hex[:8]}"
    await committed_config(key, "fork-socket-value")

    # Any attempt to reach the network API fails loudly; the socket must
    # serve the call.
    monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

    engine_token, _ = mint_engine_token(
        execution_id="gate-a-fork",
        solution_id=None,
        global_repo_access=True,
        timeout_seconds=120,
    )

    server = WorkerSdkHttpServer()
    await server.start()
    assert server.socket_path is not None

    template = TemplateProcess()
    template.start()
    try:
        with _no_cache():
            child_pid, work_queue, result_queue = template.fork(
                worker_id="wsdk-socket-fork",
                sdk_socket_path=server.socket_path,
            )
            try:
                work_queue.put(
                    (
                        "exec-wsdk-socket",
                        _context_for(_script_for(key), engine_token),
                    )
                )
                envelope = await asyncio.to_thread(result_queue.get, True, 90.0)
            finally:
                work_queue.close()
                result_queue.close()

        assert envelope["success"] is True, envelope
        result = envelope["result"]
        assert result["value"] == "fork-set-value"
        assert result["listed"] == "fork-set-value"
        assert result["deleted"] is True
        assert result["after_delete"] == "missing"
        assert result["socket_path"] == server.socket_path
        assert result["had_db_url"] is False
        assert result["had_sqlalchemy"] is False

        _wait_for_pid_to_die(child_pid)
    finally:
        with contextlib.suppress(Exception):
            template.shutdown()
        await server.stop()
