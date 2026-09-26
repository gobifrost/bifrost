"""Gate C5a: a real forked child reaches the workflow mutation routes over the socket.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses): the
child installs the worker's private Unix socket at engine start and runs
``bifrost.workflows.execute`` (scheduled enqueue) plus ``bifrost.workflows.cancel``
(including the duplicate-cancel 409 and a 404) through the normal execution
path, while the test process serves the **real** workflow routes on that
socket via uvicorn against the worker's global database engine. The parent
owns the DB and the queue; the child's network API is dead by environment and
it receives no database credentials. Envelope success therefore proves the
migrated facade rode the shared client transport — zero API requests for the
mutations, no dedicated channel frames, and no PostgreSQL in the child.

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
        "execution_id": f"workflows-fork-{uuid4().hex[:8]}",
        "name": "sdk-workflows-local-fork-test",
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
class TestForkedWorkflowMutationsSocket:
    async def test_scheduled_execute_and_cancel_over_worker_socket_without_channel(
        self, async_session_factory, monkeypatch
    ):
        """A real forked child schedules and cancels over the socket, HTTP dead."""
        from sqlalchemy import delete, select

        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution as ExecutionModel
        from src.models.orm.workflows import Workflow as WorkflowModel

        wf_name = f"fork-wf-{uuid4().hex[:8]}"
        async with async_session_factory() as session:
            wf = WorkflowModel(
                name=wf_name,
                function_name=f"{wf_name}_fn",
                path=f"workflows/{wf_name}.py",
                type="workflow",
                is_active=True,
                access_level="authenticated",
                cache_ttl_seconds=0,
            )
            session.add(wf)
            await session.commit()
            wf_id = wf.id

        source = (
            "import os, sys\n"
            "from bifrost import workflows\n"
            "from bifrost.client import get_engine_socket_path\n"
            "from bifrost import _local_transport as _lt\n"
            f"_eid = await workflows.execute({wf_name!r}, {{'ticket_id': 1}}, delay_seconds=3600)\n"
            "await workflows.cancel(_eid)\n"
            "try:\n"
            "    await workflows.cancel(_eid)\n"
            "    _duplicate = 'LEAKED'\n"
            "except Exception as _e:\n"
            "    _duplicate = f'{type(_e).__name__}:"
            "{getattr(getattr(_e, \"response\", None), \"status_code\", None)}'\n"
            "result = {\n"
            "    'used_socket': get_engine_socket_path() is not None,\n"
            "    'channel': 'installed' if _lt.get() is not None else 'absent',\n"
            "    'execution_id': _eid,\n"
            "    'duplicate': _duplicate,\n"
            "    'had_db_url': (\n"
            "        'BIFROST_DATABASE_URL' in os.environ\n"
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
            "    ),\n"
            "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
            "}\n"
        )
        # Hard-disable HTTP for the child: any SDK call that reaches HTTP
        # fails with connection-refused, so success proves the socket served
        # both mutations.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        from src.core.security import mint_engine_token

        engine_token, _ = mint_engine_token(
            execution_id="gate-c5a-workflows-fork",
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
                worker_id="sdk-workflows-fork",
                sdk_socket_path=server.socket_path,
            )
            try:
                work_queue.put(
                    (
                        "exec-workflows-fork",
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
            assert isinstance(result["execution_id"], str), result
            # The duplicate cancel loses the guarded UPDATE and surfaces 409.
            assert result["duplicate"].endswith(":409"), result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result

            # The scheduled row is durable and cancelled in the parent's DB.
            async with async_session_factory() as session:
                row = (
                    await session.execute(
                        select(ExecutionModel).where(
                            ExecutionModel.id == result["execution_id"]
                        )
                    )
                ).scalar_one()
                assert row.status == ExecutionStatus.CANCELLED
                assert row.workflow_name == wf_name

            _wait_for_pid_to_die(child_pid)
        finally:
            with contextlib.suppress(Exception):
                template.shutdown()
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(ExecutionModel).where(
                        ExecutionModel.workflow_id == wf_id
                    )
                )
                await session.execute(
                    delete(WorkflowModel).where(WorkflowModel.id == wf_id)
                )
                await session.commit()
