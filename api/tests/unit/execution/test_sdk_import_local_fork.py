"""Stage: real forked children served by the parent import dispatcher.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the engine-local import transport at engine start and
loads its entry workflow plus a dynamic import through the parent, while
the parent serves ``modules.resolve``/``modules.fetch`` from the shared
service. The child's HTTP route is hard-disabled (dead
``BIFROST_API_URL``) and its environment carries no database or storage
credentials, so success proves the local import channel — zero API
requests and zero child DB/S3 connections for cold imports.

Seeding is S3-only with unique paths (never through the Redis cache), so
every first load is guaranteed cold in the child.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from uuid import uuid4

import pytest
import pytest_asyncio

from src.services.execution.sdk_local_dispatch import principal_from_context
from src.services.execution.template_process import TemplateProcess

pytestmark = pytest.mark.slow


@pytest_asyncio.fixture(autouse=True)
async def _fresh_shared_redis():
    """Rebind the shared async Redis singleton to each test's loop.

    pytest-asyncio runs every test on a fresh event loop, but
    ``src.core.redis_client``'s singleton persists across tests; reusing a
    connection minted on a previous test's loop raises "attached to a
    different loop". Dispose around each test (mirrors the global
    ``isolate_global_db_engine`` fixture) so the parent import pump mints
    connections on the current loop.
    """
    from src.core.redis_client import close_redis_client

    await close_redis_client()
    yield
    await close_redis_client()


@contextlib.asynccontextmanager
async def _null_factory():
    yield None


def _context_for(function_name: str, file_path: str) -> dict:
    return {
        "execution_id": f"fork-import-{uuid4().hex[:8]}",
        "name": function_name,
        "function_name": function_name,
        "file_path": file_path,
        "parameters": {},
        "caller": {
            "user_id": "fork-import-user",
            "email": "fork-import@test.local",
            "name": "Fork Import Test",
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
        "engine_token": "fork-import-dead-token",
    }


def _entry_source(function_name: str, dep_module: str) -> str:
    return (
        "from bifrost import workflow\n"
        "\n"
        "\n"
        f'@workflow(name="{function_name}", description="import-local fork test")\n'
        f"async def {function_name}():\n"
        "    import os\n"
        "    from bifrost._import_transport import get as _get_import_transport\n"
        f"    import {dep_module} as _dep\n"
        "    return {\n"
        "        'value': _dep.VALUE,\n"
        "        'used_import': _get_import_transport() is not None,\n"
        "        'had_db_url': (\n"
        "            'BIFROST_DATABASE_URL' in os.environ\n"
        "            or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
        "        ),\n"
        "        'had_s3': any(\n"
        "            k in os.environ for k in (\n"
        "                'BIFROST_S3_ACCESS_KEY',\n"
        "                'BIFROST_S3_SECRET_KEY',\n"
        "                'BIFROST_S3_ENDPOINT_URL',\n"
        "                'BIFROST_S3_BUCKET',\n"
        "            )\n"
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


async def _serve_both(
    imp_req: object,
    imp_resp: object,
    sdk_req: object,
    sdk_resp: object,
    principal: object,
) -> tuple[asyncio.Task, asyncio.Task]:
    from src.services.execution.sdk_local_dispatch import (
        IMPORT_CHANNEL_ALLOWED_OPS,
        SDK_CHANNEL_ALLOWED_OPS,
        serve_channel,
    )

    imp_pump = asyncio.create_task(
        serve_channel(
            recv_conn=imp_req,
            send_conn=imp_resp,
            session_factory=lambda: _null_factory(),
            principal=principal,  # type: ignore[arg-type]
            allowed_ops=IMPORT_CHANNEL_ALLOWED_OPS,
        )
    )
    sdk_pump = asyncio.create_task(
        serve_channel(
            recv_conn=sdk_req,
            send_conn=sdk_resp,
            session_factory=lambda: _null_factory(),
            principal=principal,  # type: ignore[arg-type]
            allowed_ops=SDK_CHANNEL_ALLOWED_OPS,
        )
    )
    return imp_pump, sdk_pump


@pytest.mark.asyncio
class TestForkedColdImport:
    async def test_cold_entry_and_dynamic_import_without_http_or_s3(
        self, monkeypatch
    ):
        """Entry load + dynamic import ride the import channel, cold."""
        tag = uuid4().hex[:8]
        dep_module = f"cold_fork_dep_{tag}"
        dep_path = f"{dep_module}.py"
        function_name = f"fork_imp_{tag}"
        entry_path = f"workflows/fork_imp_{tag}.py"
        await _seed_s3_only(dep_path, f"VALUE = 'dep-{tag}'\n")
        await _seed_s3_only(entry_path, _entry_source(function_name, dep_module))
        # Hard-disable HTTP for every forked child of this test: any module
        # fetch that reaches HTTP fails with connection-refused, so success
        # proves the local import channel served the operation.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pumps: list = []
        conns = []
        try:
            (
                child_pid,
                work_queue,
                result_queue,
                sdk_req,
                sdk_resp,
                imp_req,
                imp_resp,
            ) = template.fork(
                worker_id="sdk-import-fork-oneshot",
                with_sdk=True,
                with_import=True,
            )
            conns = [sdk_req, sdk_resp, imp_req, imp_resp]
            context = _context_for(function_name, entry_path)
            principal = principal_from_context(context)
            imp_pump, sdk_pump = await _serve_both(
                imp_req, imp_resp, sdk_req, sdk_resp, principal
            )
            pumps = [imp_pump, sdk_pump]
            work_queue.put(("exec-fork-import", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 90.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["value"] == f"dep-{tag}"
            assert result["used_import"] is True
            assert result["had_db_url"] is False
            assert result["had_s3"] is False
            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(imp_pump, timeout=15.0) == "eof"
            assert await asyncio.wait_for(sdk_pump, timeout=15.0) == "eof"
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

    async def test_persistent_child_reuses_import_channel(self, monkeypatch):
        """Two cold executions share one import channel, then EOF."""
        tag = uuid4().hex[:8]
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        entries: list[tuple[str, str, str, str]] = []
        for i in (1, 2):
            dep_module = f"cold_fork_long_{tag}_{i}"
            dep_path = f"{dep_module}.py"
            function_name = f"fork_imp_long_{tag}_{i}"
            entry_path = f"workflows/fork_imp_long_{tag}_{i}.py"
            await _seed_s3_only(dep_path, f"VALUE = 'long-{tag}-{i}'\n")
            await _seed_s3_only(
                entry_path, _entry_source(function_name, dep_module)
            )
            entries.append((dep_module, dep_path, function_name, entry_path))

        template = TemplateProcess()
        template.start()
        pumps: list = []
        conns = []
        try:
            (
                child_pid,
                work_queue,
                result_queue,
                sdk_req,
                sdk_resp,
                imp_req,
                imp_resp,
            ) = template.fork(
                worker_id="sdk-import-fork-long",
                persistent=True,
                with_sdk=True,
                with_import=True,
            )
            conns = [sdk_req, sdk_resp, imp_req, imp_resp]
            context = _context_for(entries[0][2], entries[0][3])
            principal = principal_from_context(context)
            imp_pump, sdk_pump = await _serve_both(
                imp_req, imp_resp, sdk_req, sdk_resp, principal
            )
            pumps = [imp_pump, sdk_pump]

            work_queue.put(("exec-fork-import-1", context))
            first = await asyncio.to_thread(result_queue.get, True, 90.0)
            assert first["success"] is True, first
            assert first["result"]["value"] == f"long-{tag}-1"
            assert first["result"]["used_import"] is True

            # Second cold execution on the SAME child and SAME channels.
            context2 = _context_for(entries[1][2], entries[1][3])
            work_queue.put(("exec-fork-import-2", context2))
            second = await asyncio.to_thread(result_queue.get, True, 90.0)
            assert second["success"] is True, second
            assert second["result"]["value"] == f"long-{tag}-2"
            assert second["result"]["used_import"] is True

            work_queue.close()
            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(imp_pump, timeout=15.0) == "eof"
            assert await asyncio.wait_for(sdk_pump, timeout=15.0) == "eof"
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
            for _, dep_path, _, entry_path in entries:
                await _drop_s3(dep_path)
                await _drop_s3(entry_path)
