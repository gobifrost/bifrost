"""Engine-local streaming transport over a real forked child.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the stream transport at engine start and consumes one
fake operation stream through the parent pump, while an ordinary unary
``config.get`` and a synchronous cold import run while the stream is still
active. The child's HTTP route is hard-disabled (dead
``BIFROST_API_URL``) and its environment carries no database credentials,
so success proves the local channels — the stream delivers incrementally
on its own descriptors while the SDK and import pumps stay independent.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio

from src.services.execution.sdk_local_dispatch import principal_from_context
from src.services.execution.sdk_stream_dispatch import serve_stream_channel
from src.services.execution.template_process import TemplateProcess

pytestmark = pytest.mark.slow


@pytest_asyncio.fixture(autouse=True)
async def _fresh_shared_redis():
    """Rebind the shared async Redis singleton to each test's loop.

    pytest-asyncio runs every test on a fresh event loop, but
    ``src.core.redis_client``'s singleton persists across tests; reusing a
    connection minted on a previous test's loop raises "attached to a
    different loop". Dispose around each test so pumps mint connections on
    the current loop.
    """
    from src.core.redis_client import close_redis_client

    await close_redis_client()
    yield
    await close_redis_client()


@contextlib.asynccontextmanager
async def _factory(db_session):
    yield db_session


@contextlib.asynccontextmanager
async def _null_factory():
    yield None


async def _seed_global(db_session, key, value):
    from shared.sdk_config import set_sdk_config_value

    await set_sdk_config_value(
        db_session,
        key=key,
        value=value,
        is_secret=False,
        org_id=None,
        actor_email="sdk-stream-fork-test",
    )


def _context_for(function_name: str, file_path: str) -> dict:
    return {
        "execution_id": f"fork-stream-{uuid4().hex[:8]}",
        "name": function_name,
        "function_name": function_name,
        "file_path": file_path,
        "parameters": {},
        "caller": {
            "user_id": "fork-stream-user",
            "email": "fork-stream@test.local",
            "name": "Fork Stream Test",
        },
        "organization": None,
        "tags": [],
        "timeout_seconds": 120,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": False,
        # One-shot token; the child's HTTP route is dead by env design, so
        # any HTTP attempt fails loudly instead of succeeding silently.
        "engine_token": "fork-stream-dead-token",
    }


def _entry_source(function_name: str, dep_module: str, key: str) -> str:
    return (
        "from bifrost import workflow\n"
        "\n"
        "\n"
        f'@workflow(name="{function_name}", description="stream-local fork test")\n'
        f"async def {function_name}():\n"
        "    import os\n"
        "    from bifrost import config\n"
        "    from bifrost._stream_transport import get as _get_stream_transport\n"
        "    _transport = _get_stream_transport()\n"
        "    _stream_ok = _transport is not None\n"
        '    _stream = await _transport.open_stream_async("test.stream")\n'
        "    _events = [await _stream.__anext__()]\n"
        f"    import {dep_module} as _dep\n"
        "    _import_while_active = not _stream.closed\n"
        f"    _value = await config.get({key!r})\n"
        "    _sdk_while_active = not _stream.closed\n"
        "    async for _event in _stream:\n"
        "        _events.append(_event)\n"
        "    return {\n"
        "        'events': _events,\n"
        "        'value': _value,\n"
        "        'dep': _dep.VALUE,\n"
        "        'stream_ok': _stream_ok,\n"
        "        'import_while_active': _import_while_active,\n"
        "        'sdk_while_active': _sdk_while_active,\n"
        "        'had_db_url': (\n"
        "            'BIFROST_DATABASE_URL' in os.environ\n"
        "            or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
        "        ),\n"
        "    }\n"
    )


async def _seed_s3_only(relpath: str, content: str) -> None:
    """Write a workspace file to S3 without warming the Redis cache."""
    from src.services.repo_storage import RepoStorage

    await RepoStorage().write(relpath, content.encode("utf-8"))


async def _drop_s3(relpath: str) -> None:
    from src.services.repo_storage import RepoStorage

    with contextlib.suppress(Exception):
        await RepoStorage().delete(relpath)


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


def _stream_registry(seen: list[Any]) -> dict[str, Any]:
    """Fake ``test.stream`` source: one fast event, a beat, then the rest."""

    async def _source(params: dict[str, Any], principal: Any):
        seen.append(principal)
        yield {"i": 0}
        # Hold the stream open so the child's SDK call and cold import
        # provably run while it is still active.
        await asyncio.sleep(1.0)
        yield {"i": 1}
        yield {"done": True}

    return {"test.stream": _source}


@pytest.mark.asyncio
class TestForkedStreamTransport:
    async def test_stream_sdk_and_import_share_no_descriptors(
        self, db_session, monkeypatch
    ):
        """A real child streams while SDK and import channels serve it."""
        tag = uuid4().hex[:8]
        key = f"fork-stream-{tag}"
        await _seed_global(db_session, key, "stream-value")
        dep_module = f"cold_fork_stream_dep_{tag}"
        dep_path = f"{dep_module}.py"
        function_name = f"fork_stream_{tag}"
        entry_path = f"workflows/fork_stream_{tag}.py"
        await _seed_s3_only(dep_path, f"VALUE = 'dep-{tag}'\n")
        await _seed_s3_only(entry_path, _entry_source(function_name, dep_module, key))
        # Hard-disable HTTP for every forked child of this test: any SDK
        # call that reaches HTTP fails with connection-refused, so success
        # proves the local transports served every operation.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pumps: list = []
        conns = []
        seen: list[Any] = []
        try:
            (
                child_pid,
                work_queue,
                result_queue,
                sdk_req,
                sdk_resp,
                imp_req,
                imp_resp,
                stream_req,
                stream_resp,
            ) = template.fork(
                worker_id="sdk-stream-fork",
                with_sdk=True,
                with_import=True,
                with_stream=True,
            )
            conns = [sdk_req, sdk_resp, imp_req, imp_resp, stream_req, stream_resp]
            context = _context_for(function_name, entry_path)
            principal = principal_from_context(context)

            from src.services.execution.sdk_local_dispatch import (
                IMPORT_CHANNEL_ALLOWED_OPS,
                SDK_CHANNEL_ALLOWED_OPS,
                serve_channel,
            )

            sdk_pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=lambda: _factory(db_session),
                    principal=principal,
                    allowed_ops=SDK_CHANNEL_ALLOWED_OPS,
                )
            )
            imp_pump = asyncio.create_task(
                serve_channel(
                    recv_conn=imp_req,
                    send_conn=imp_resp,
                    session_factory=lambda: _null_factory(),
                    principal=principal,
                    allowed_ops=IMPORT_CHANNEL_ALLOWED_OPS,
                )
            )
            stream_pump = asyncio.create_task(
                serve_stream_channel(
                    recv_conn=stream_req,
                    send_conn=stream_resp,
                    principal=principal,
                    registry=_stream_registry(seen),
                )
            )
            pumps = [sdk_pump, imp_pump, stream_pump]

            work_queue.put(("exec-fork-stream", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["stream_ok"] is True
            assert result["events"] == [{"i": 0}, {"i": 1}, {"done": True}]
            assert result["value"] == "stream-value"
            assert result["dep"] == f"dep-{tag}"
            assert result["import_while_active"] is True
            assert result["sdk_while_active"] is True
            assert result["had_db_url"] is False
            # The stream source saw the parent-derived principal.
            assert len(seen) == 1
            assert seen[0] == principal

            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(sdk_pump, timeout=15.0) == "eof"
            assert await asyncio.wait_for(imp_pump, timeout=15.0) == "eof"
            assert await asyncio.wait_for(stream_pump, timeout=15.0) == "eof"
            pumps = []
        finally:
            for pump in pumps:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()
            await _drop_s3(dep_path)
            await _drop_s3(entry_path)
