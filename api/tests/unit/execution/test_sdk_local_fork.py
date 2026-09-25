"""Stage 1: real forked children served by the parent dispatcher.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the engine-local transport at engine start and runs an
inline script through the normal execution path, while the parent serves
``config.get`` from a real database session. The child's HTTP route is
hard-disabled (dead ``BIFROST_API_URL``) and its environment carries no
database credentials, so a correct value proves the local transport —
zero API requests for ``config.get``.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

import asyncio
import base64
import contextlib
import os
import signal
import time
from uuid import uuid4

import pytest

from src.services.execution.sdk_local_dispatch import (
    LocalDispatchPrincipal,
    serve_channel,
)
from src.services.execution.template_process import TemplateProcess

pytestmark = pytest.mark.slow


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _script_for(key: str) -> str:
    return _script_b64(
        "import os, sys\n"
        "from bifrost import config\n"
        f"_value = await config.get({key!r})\n"
        "result = {\n"
        "    'value': _value,\n"
        "    'had_db_url': (\n"
        "        'BIFROST_DATABASE_URL' in os.environ\n"
        "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
        "    ),\n"
        "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
        "}\n"
    )


def _context_for(code_b64: str, org_id=None) -> dict:
    return {
        "execution_id": f"fork-test-{uuid4().hex[:8]}",
        "name": "sdk-local-fork-test",
        "code": code_b64,
        "parameters": {},
        "caller": {
            "user_id": "fork-test-user",
            "email": "fork@test.local",
            "name": "Fork Test",
        },
        "organization": None if org_id is None else {"id": str(org_id)},
        "tags": [],
        "timeout_seconds": 120,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": False,
        # One-shot token; the child's HTTP route is dead by env design, so
        # any HTTP attempt fails loudly instead of succeeding silently.
        "engine_token": "fork-test-dead-token",
    }


@contextlib.asynccontextmanager
async def _factory(db_session):
    yield db_session


async def _seed_global(db_session, key, value):
    from shared.sdk_config import set_sdk_config_value

    await set_sdk_config_value(
        db_session,
        key=key,
        value=value,
        is_secret=False,
        org_id=None,
        actor_email="sdk-local-fork-test",
    )


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


@pytest.mark.asyncio
class TestForkedLocalTransport:
    async def test_one_shot_workflow_config_get_without_http(
        self, db_session, monkeypatch
    ):
        """A real forked child resolves config.get with HTTP disabled."""
        key = f"fork-local-{uuid4().hex[:8]}"
        await _seed_global(db_session, key, "fork-value")
        # Hard-disable HTTP for every forked child of this test: any SDK
        # call that reaches HTTP fails with connection-refused, so success
        # proves the local transport served the operation.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-fork-oneshot", with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=lambda: _factory(db_session),
                    principal=LocalDispatchPrincipal(caller_org_id=None),
                )
            )
            work_queue.put(("exec-fork-oneshot", _context_for(_script_for(key))))
            envelope = await asyncio.to_thread(result_queue.get, True, 90.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["value"] == "fork-value"
            assert result["had_db_url"] is False
            assert result["had_sqlalchemy"] is False
            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
            pump = None
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()

    async def test_long_lived_child_reuses_channel(
        self, db_session, monkeypatch
    ):
        """A persistent (service-like) child serves many calls, then EOFs."""
        key_a = f"fork-long-a-{uuid4().hex[:8]}"
        key_b = f"fork-long-b-{uuid4().hex[:8]}"
        await _seed_global(db_session, key_a, "value-a")
        await _seed_global(db_session, key_b, "value-b")
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-fork-long", persistent=True, with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=lambda: _factory(db_session),
                    principal=LocalDispatchPrincipal(caller_org_id=None),
                )
            )
            work_queue.put(("exec-fork-long-1", _context_for(_script_for(key_a))))
            first = await asyncio.to_thread(result_queue.get, True, 90.0)
            assert first["success"] is True, first
            assert first["result"]["value"] == "value-a"

            # Second execution on the SAME child and SAME channel.
            work_queue.put(("exec-fork-long-2", _context_for(_script_for(key_b))))
            second = await asyncio.to_thread(result_queue.get, True, 90.0)
            assert second["success"] is True, second
            assert second["result"]["value"] == "value-b"

            work_queue.close()
            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
            pump = None
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()

    async def test_child_crash_ends_pump(self, monkeypatch):
        """SIGKILL mid-channel ends the parent pump without hanging."""
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, _wq, _rq, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-fork-crash", persistent=True, with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=None,
                    principal=LocalDispatchPrincipal(caller_org_id=None),
                )
            )
            await asyncio.sleep(0.5)
            os.kill(child_pid, signal.SIGKILL)
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
            pump = None
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()


@pytest.mark.asyncio
class TestForkedLargeValues:
    async def test_large_value_real_fork(self, db_session, monkeypatch):
        """A real forked child resolves a >64KiB value byte-identical."""
        import base64 as _b64

        key = f"fork-big-{uuid4().hex[:8]}"
        big = "z" * 100_000
        await _seed_global(db_session, key, big)
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        source = (
            "import os, sys\n"
            "from bifrost import config\n"
            f"_value = await config.get({key!r})\n"
            "result = {\n"
            "    'length': len(_value),\n"
            "    'prefix': _value[:16],\n"
            "    'suffix': _value[-16:],\n"
            "    'had_db_url': (\n"
            "        'BIFROST_DATABASE_URL' in os.environ\n"
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
            "    ),\n"
            "}\n"
        )
        code = _b64.b64encode(source.encode("utf-8")).decode("utf-8")

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-fork-big", with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=lambda: _factory(db_session),
                    principal=LocalDispatchPrincipal(caller_org_id=None),
                )
            )
            work_queue.put(("exec-fork-big", _context_for(code)))
            envelope = await asyncio.to_thread(result_queue.get, True, 90.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["length"] == 100_000
            assert result["prefix"] == "z" * 16
            assert result["suffix"] == "z" * 16
            assert result["had_db_url"] is False
            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
            pump = None
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()


@pytest.mark.asyncio
class TestForkedConfigMutations:
    async def test_forked_child_writes_and_reads_without_http(
        self, db_session, monkeypatch
    ):
        """A real forked child runs set/get/list/delete with HTTP disabled.

        The child's HTTP route is dead (connection-refused), so envelope
        success proves every fixed call rode the local transport with zero
        API requests. The big value forces chunked request frames.
        """
        from src.services.execution.sdk_local_dispatch import (
            principal_from_context,
        )

        tag = uuid4().hex[:8]
        key_small = f"fork-mut-s-{tag}"
        key_big = f"fork-mut-b-{tag}"
        source = (
            "import os, sys\n"
            "from bifrost import config\n"
            f"await config.set({key_small!r}, 'small-value')\n"
            f"await config.set({key_big!r}, 'z' * 100000)\n"
            f"_v1 = await config.get({key_small!r})\n"
            f"_vbig = await config.get({key_big!r})\n"
            "_listed = await config.list()\n"
            f"_d1 = await config.delete({key_small!r})\n"
            f"_d1_again = await config.delete({key_small!r})\n"
            f"_missing = await config.get({key_small!r}, default='gone')\n"
            "result = {\n"
            "    'v1': _v1,\n"
            "    'big_len': len(_vbig),\n"
            "    'big_affix': _vbig[:8] + _vbig[-8:],\n"
            f"    'listed_ok': _listed[{key_big!r}][:8] == 'zzzzzzzz',\n"
            "    'd1': _d1,\n"
            "    'd1_again': _d1_again,\n"
            "    'missing': _missing,\n"
            "    'had_db_url': (\n"
            "        'BIFROST_DATABASE_URL' in os.environ\n"
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
            "    ),\n"
            "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
            "}\n"
        )
        context = _context_for(_script_b64(source))
        principal = principal_from_context(context)
        from src.core.security import ENGINE_SDK_ACTOR_EMAIL

        assert principal.actor_email == ENGINE_SDK_ACTOR_EMAIL
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-fork-mutations", with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=lambda: _factory(db_session),
                    principal=principal,
                )
            )
            work_queue.put(("exec-fork-mutations", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 90.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["v1"] == "small-value"
            assert result["big_len"] == 100_000
            assert result["big_affix"] == "z" * 16
            assert result["listed_ok"] is True
            assert result["d1"] is True
            assert result["d1_again"] is False
            assert result["missing"] == "gone"
            assert result["had_db_url"] is False
            assert result["had_sqlalchemy"] is False
            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
            pump = None
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()


@pytest.mark.asyncio
class TestLiveServiceLocalTransport:
    async def test_live_service_config_get_without_http(
        self, db_session, monkeypatch
    ):
        """A real @service child resolves config.get with HTTP disabled."""
        import hashlib

        tag = uuid4().hex[:8]
        key = f"fork-svc-{tag}"
        await _seed_global(db_session, key, "service-value")
        function_name = f"svc_{tag}"
        file_path = f"workflows/svc_{tag}.py"
        source = (
            "from bifrost import config, service\n"
            "\n"
            "\n"
            "@service\n"
            f"async def {function_name}():\n"
            "    import os\n"
            "    import sys\n"
            f"    value = await config.get({key!r})\n"
            "    return {\n"
            "        'value': value,\n"
            "        'had_db_url': (\n"
            "            'BIFROST_DATABASE_URL' in os.environ\n"
            "            or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
            "        ),\n"
            "        'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
            "    }\n"
        )
        from src.core.module_cache import set_module

        await set_module(
            file_path, source, hashlib.sha256(source.encode()).hexdigest()
        )
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        attempt_id = f"attempt-svc-{tag}"
        context = _context_for("")
        context.update(
            {
                "function_name": function_name,
                "file_path": file_path,
                "service": {
                    "service_id": f"service-{tag}",
                    "attempt_id": attempt_id,
                    "lease_token": f"lease-{tag}",
                    "graceful_shutdown_seconds": 5,
                },
            }
        )
        context.pop("code", None)

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-fork-service", with_sdk=True
            )
            conns = [sdk_req, sdk_resp]
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=lambda: _factory(db_session),
                    principal=LocalDispatchPrincipal(caller_org_id=None),
                )
            )
            work_queue.put((attempt_id, context))
            envelope = await asyncio.to_thread(result_queue.get, True, 90.0)
            assert envelope.get("service", {}).get("attempt_id") == attempt_id
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["value"] == "service-value"
            assert result["had_db_url"] is False
            assert result["had_sqlalchemy"] is False
            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
            pump = None
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()
