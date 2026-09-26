"""Gate C5a: a real forked child reaches the execution-read routes over the socket.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses): the
child installs the worker's private Unix socket at engine start and runs
``bifrost.workflows.list``, ``bifrost.executions.list`` (filtered plus
keyset-paged), and ``bifrost.executions.get`` through the normal execution
path, while the test process serves the **real** workflow/execution routes on
that socket via uvicorn against the worker's global database engine. The
parent owns the DB; the child's network API is dead by environment and it
receives no database credentials. Envelope success therefore proves the
migrated facades rode the shared client transport — zero API requests for the
fixed calls, no dedicated channel frames, and no PostgreSQL in the child.

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
        "execution_id": f"exec-reads-fork-{uuid4().hex[:8]}",
        "name": "sdk-exec-reads-local-fork-test",
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
class TestForkedExecutionReadsSocket:
    async def test_concurrent_reads_over_worker_socket_without_channel(
        self, async_session_factory, monkeypatch
    ):
        """A real forked child reads workflows/executions over the socket, HTTP dead."""
        from sqlalchemy import delete

        from src.models import User as UserORM
        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution as ExecutionModel
        from src.models.orm.workflows import Workflow as WorkflowModel

        stem = f"fork-reads-{uuid4().hex[:8]}"
        wf_name = f"{stem}-wf"
        exec_ids: list[str] = []
        async with async_session_factory() as session:
            wf = WorkflowModel(
                name=wf_name,
                function_name=f"{stem}_fn",
                path=f"workflows/{wf_name}.py",
                type="workflow",
                is_active=True,
                access_level="authenticated",
                cache_ttl_seconds=0,
            )
            reader = UserORM(
                email=f"{stem}@example.com",
                name="Fork Reads",
                is_superuser=True,
            )
            session.add_all([wf, reader])
            await session.flush()
            wf_id, reader_id = wf.id, reader.id
            for _ in range(3):
                row = ExecutionModel(
                    workflow_name=wf_name,
                    status=ExecutionStatus.SUCCESS,
                    parameters={},
                    executed_by=reader.id,
                    executed_by_name="Fork Reads",
                )
                session.add(row)
                await session.flush()
                exec_ids.append(str(row.id))
            await session.commit()

        source = (
            "import asyncio, importlib, os, sys\n"
            "from bifrost import workflows, executions\n"
            "from bifrost.client import get_engine_socket_path\n"
            "from bifrost import _local_transport as _lt\n"
            "_wf = importlib.import_module('bifrost.workflows')\n"
            "_ex = importlib.import_module('bifrost.executions')\n"
            "_wf_list = await workflows.list()\n"
            "_saw_workflow = any(_w.name == " + repr(wf_name) + " for _w in _wf_list)\n"
            "_named, _detail = await asyncio.gather(\n"
            f"    executions.list(workflow_name={wf_name!r}),\n"
            f"    executions.get({exec_ids[0]!r}),\n"
            ")\n"
            f"_page1 = await executions.list(workflow_name={wf_name!r}, limit=2)\n"
            f"_page2 = await executions.list(workflow_name={wf_name!r}, limit=2, "
            "continuation_token=_page1.continuation_token)\n"
            "try:\n"
            f"    await executions.get({str(uuid4())!r})\n"
            "    _missing = 'LEAKED'\n"
            "except ValueError:\n"
            "    _missing = 'ValueError'\n"
            "except Exception as _e:\n"
            "    _missing = f'{type(_e).__name__}'\n"
            "result = {\n"
            "    'used_socket': get_engine_socket_path() is not None,\n"
            "    'channel': 'installed' if _lt.get() is not None else 'absent',\n"
            "    'saw_workflow': _saw_workflow,\n"
            "    'named_count': len(_named),\n"
            "    'named_ids': sorted(_e.execution_id for _e in _named),\n"
            "    'page1_count': len(_page1),\n"
            "    'page2_count': len(_page2),\n"
            "    'detail_id': _detail.execution_id,\n"
            "    'detail_name': _detail.workflow_name,\n"
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
        # every read operation.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        from src.core.security import mint_engine_token

        engine_token, _ = mint_engine_token(
            execution_id="gate-c5a-reads-fork",
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
                worker_id="sdk-exec-reads-fork",
                sdk_socket_path=server.socket_path,
            )
            try:
                work_queue.put(
                    (
                        "exec-exec-reads-fork",
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
            assert result["saw_workflow"] is True, result
            assert result["named_count"] == 3, result
            assert result["named_ids"] == sorted(exec_ids), result
            assert result["page1_count"] == 2, result
            assert result["page2_count"] >= 1, result
            assert result["detail_id"] == exec_ids[0], result
            assert result["detail_name"] == wf_name, result
            assert result["missing"] == "ValueError", result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result

            _wait_for_pid_to_die(child_pid)
        finally:
            with contextlib.suppress(Exception):
                template.shutdown()
            await server.stop()
            async with async_session_factory() as session:
                await session.execute(
                    delete(ExecutionModel).where(
                        ExecutionModel.workflow_name == wf_name
                    )
                )
                await session.execute(
                    delete(WorkflowModel).where(WorkflowModel.id == wf_id)
                )
                await session.execute(
                    delete(UserORM).where(UserORM.id == reader_id)
                )
                await session.commit()
