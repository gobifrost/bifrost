"""Engine-local agent runs through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the engine-local transport at engine start and runs
``bifrost.agents.enqueue`` / ``get_run`` with fixed-agent HTTP
hard-disabled, while the parent serves both ops from the shared
``shared.sdk_agent_runs`` service. Envelope success proves zero API
requests for the migrated operations.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
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


def _context_for(code_b64: str) -> dict:
    return {
        "execution_id": f"agents-fork-{uuid4().hex[:8]}",
        "name": "sdk-agents-local-fork-test",
        "code": code_b64,
        "parameters": {},
        "caller": {
            "user_id": "fork-test-user",
            "email": "fork@test.local",
            "name": "Fork Test",
        },
        "organization": None,
        "tags": [],
        "timeout_seconds": 120,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": False,
        "engine_token": "fork-test-dead-token",
    }


@contextlib.asynccontextmanager
async def _factory(db_session):
    yield db_session


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


@pytest.mark.asyncio
class TestForkedAgentsTransport:
    async def test_enqueue_and_get_run_without_http(
        self, db_session, monkeypatch
    ):
        """A real forked child enqueues and reads back a run with HTTP dead."""
        from src.models.orm.agents import Agent as AgentModel

        agent_name = f"fork-agent-{uuid4().hex[:8]}"
        db_session.add(
            AgentModel(
                name=agent_name,
                system_prompt="Fork test agent.",
                is_active=True,
                created_by="sdk-agents-fork-test",
            )
        )
        await db_session.commit()

        source = (
            "import importlib, os, sys\n"
            "from bifrost import agents\n"
            "_am = importlib.import_module('bifrost.agents')\n"
            "from bifrost._local_transport import get as _get_transport\n"
            "_used_local = _get_transport() is not None\n"
            "def _dead(*args, **kwargs):\n"
            "    raise AssertionError(\n"
            "        'fixed-operation HTTP must not be used in the engine path'\n"
            "    )\n"
            "_am.get_client = _dead\n"
            f"_handle = await agents.enqueue({agent_name!r}, {{'ticket_id': 1}})\n"
            "_run = await agents.get_run(_handle.run_id)\n"
            "result = {\n"
            "    'used_local': _used_local,\n"
            "    'run_id': _handle.run_id,\n"
            "    'handle_status': _handle.status,\n"
            "    'run_status': _run.status,\n"
            "    'run_id_matches': _run.id == _handle.run_id,\n"
            "    'had_db_url': (\n"
            "        'BIFROST_DATABASE_URL' in os.environ\n"
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
            "    ),\n"
            "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
            "}\n"
        )
        context = _context_for(_script_b64(source))
        # Hard-disable HTTP for every forked child: any SDK call that
        # reaches HTTP fails with connection-refused, so success proves
        # the local transport served both operations.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-agents-fork", with_sdk=True
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
            work_queue.put(("exec-agents-fork", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_local"] is True, result
            assert result["handle_status"] == "queued", result
            assert isinstance(result["run_status"], str) and result["run_status"], result
            assert result["run_id_matches"] is True, result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result
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
