"""Gate C5b: a real forked child reaches the agent-run routes over the socket.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses): the
child installs the worker's private Unix socket at engine start and runs
``bifrost.agents.enqueue`` / ``bifrost.agents.get_run`` through the normal
execution path, while the test process serves the **real** agent-run routes on
that socket via uvicorn against the worker's global database engine. The
parent owns the DB and the queue; the child's network API is dead by
environment and it receives no database credentials. Envelope success
therefore proves the migrated facade rode the shared client transport — zero
API requests for the two operations, no dedicated channel frames, and no
PostgreSQL in the child.

Marked ``slow`` like the other real-fork tests: template boot costs seconds.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import time
from uuid import uuid4

import pytest

from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

pytestmark = pytest.mark.slow


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str, engine_token: str) -> dict:
    return {
        "execution_id": f"agents-fork-{uuid4().hex[:8]}",
        "name": "sdk-agents-local-fork-test",
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
class TestForkedAgentsSocket:
    async def test_enqueue_and_get_run_over_worker_socket_without_channel(
        self, async_session_factory, monkeypatch
    ):
        """A real forked child enqueues and reads back a run with HTTP dead."""
        from sqlalchemy import delete, select

        from src.models.orm.agent_runs import AgentRun as AgentRunModel
        from src.models.orm.agents import Agent as AgentModel

        agent_name = f"fork-agent-{uuid4().hex[:8]}"
        agent_id = None
        async with async_session_factory() as session:
            agent = AgentModel(
                name=agent_name,
                system_prompt="Fork test agent.",
                is_active=True,
                created_by="sdk-agents-fork-test",
            )
            session.add(agent)
            await session.commit()
            agent_id = agent.id

        source = (
            "import os, sys\n"
            "from bifrost import agents\n"
            "from bifrost.client import get_engine_socket_path\n"
            "from bifrost import _local_transport as _lt\n"
            f"_handle = await agents.enqueue({agent_name!r}, {{'ticket_id': 1}})\n"
            "_run = await agents.get_run(_handle.run_id)\n"
            "try:\n"
            "    await agents.get_run('00000000-0000-0000-0000-000000000000')\n"
            "    _missing = 'LEAKED'\n"
            "except ValueError:\n"
            "    _missing = 'ValueError'\n"
            "except Exception as _e:\n"
            "    _missing = type(_e).__name__\n"
            "result = {\n"
            "    'used_socket': get_engine_socket_path() is not None,\n"
            "    'channel': 'installed' if _lt.get() is not None else 'absent',\n"
            "    'run_id': _handle.run_id,\n"
            "    'handle_status': _handle.status,\n"
            "    'run_status': _run.status,\n"
            "    'run_id_matches': _run.id == _handle.run_id,\n"
            "    'missing': _missing,\n"
            "    'had_db_url': (\n"
            "        'BIFROST_DATABASE_URL' in os.environ\n"
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
            "    ),\n"
            "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
            "}\n"
        )
        # Hard-disable HTTP for the child: any SDK call that reaches HTTP
        # fails with connection-refused, so success proves the socket served
        # both operations.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        from src.core.security import mint_engine_token

        engine_token, _ = mint_engine_token(
            execution_id="gate-c5b-agents-fork",
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
            child_pid, work_queue, result_queue = template.fork(
                worker_id="sdk-agents-fork",
                sdk_socket_path=server.socket_path,
            )
            try:
                work_queue.put(
                    (
                        "exec-agents-fork",
                        _context_for(_script_b64(source), engine_token),
                    )
                )
                envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            finally:
                work_queue.close()
                result_queue.close()

            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_socket"] is True
            assert result["channel"] == "absent"
            assert result["handle_status"] == "queued", result
            assert isinstance(result["run_status"], str) and result["run_status"], result
            assert result["run_id_matches"] is True, result
            assert result["missing"] == "ValueError", result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result

            # The queued run is durable in the parent's DB.
            async with async_session_factory() as session:
                row = (
                    await session.execute(
                        select(AgentRunModel).where(
                            AgentRunModel.id == result["run_id"]
                        )
                    )
                ).scalar_one_or_none()
            assert row is not None, "socket enqueue returned a run without a durable row"

            _wait_for_pid_to_die(child_pid)
        finally:
            with contextlib.suppress(Exception):
                template.shutdown()
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(AgentRunModel).where(AgentRunModel.agent_id == agent_id)
                )
                await session.execute(
                    delete(AgentModel).where(AgentModel.id == agent_id)
                )
                await session.commit()
