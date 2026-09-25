"""Engine-local execution reads through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the engine-local transport at engine start and runs
``bifrost.workflows.list``, ``bifrost.executions.list`` (filtered plus
keyset-paged), and ``bifrost.executions.get`` concurrently with
fixed-operation HTTP hard-disabled, while the parent serves all three
ops from the shared ``shared.sdk_execution_reads`` service. Envelope
success proves zero API requests for the migrated operations, and the
parent re-reads the same rows over its own session.

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
    dispatch_frame,
    serve_channel,
)
from src.services.execution.template_process import TemplateProcess

pytestmark = pytest.mark.slow


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str) -> dict:
    return {
        "execution_id": f"exec-reads-fork-{uuid4().hex[:8]}",
        "name": "sdk-exec-reads-local-fork-test",
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
class TestForkedExecutionReadsTransport:
    async def test_concurrent_reads_without_http(self, db_session, monkeypatch):
        """A real forked child reads workflows/executions with HTTP dead."""
        from sqlalchemy import select

        from src.models import User as UserORM
        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution as ExecutionModel
        from src.models.orm.workflows import Workflow as WorkflowModel

        stem = f"fork-reads-{uuid4().hex[:8]}"
        wf_name = f"{stem}-wf"
        db_session.add(
            WorkflowModel(
                name=wf_name,
                function_name=f"{stem}_fn",
                path=f"workflows/{wf_name}.py",
                type="workflow",
                is_active=True,
                access_level="authenticated",
                cache_ttl_seconds=0,
            )
        )
        reader = UserORM(
            email=f"{stem}@example.com",
            name="Fork Reads",
            is_superuser=True,
        )
        db_session.add(reader)
        await db_session.flush()
        exec_ids = []
        for i in range(3):
            row = ExecutionModel(
                workflow_name=wf_name,
                status=ExecutionStatus.SUCCESS,
                parameters={},
                executed_by=reader.id,
                executed_by_name="Fork Reads",
            )
            db_session.add(row)
            await db_session.flush()
            exec_ids.append(str(row.id))
        await db_session.commit()

        source = (
            "import asyncio, importlib, os, sys\n"
            "from bifrost import workflows, executions\n"
            "_wf = importlib.import_module('bifrost.workflows')\n"
            "_ex = importlib.import_module('bifrost.executions')\n"
            "from bifrost._local_transport import get as _get_transport\n"
            "_used_local = _get_transport() is not None\n"
            "def _dead(*args, **kwargs):\n"
            "    raise AssertionError(\n"
            "        'fixed-operation HTTP must not be used in the engine path'\n"
            "    )\n"
            "_wf.get_client = _dead\n"
            "_ex.get_client = _dead\n"
            "_wf_list = await workflows.list()\n"
            "_saw_workflow = any(_w.name == " + repr(wf_name) + " for _w in _wf_list)\n"
            "_page1, _named, _detail = await asyncio.gather(\n"
            "    executions.list(limit=2),\n"
            f"    executions.list(workflow_name={wf_name!r}),\n"
            f"    executions.get({exec_ids[0]!r}),\n"
            ")\n"
            "_page2 = await executions.list(limit=2, continuation_token=_page1.continuation_token)\n"
            "try:\n"
            f"    await executions.get({str(uuid4())!r})\n"
            "    _missing = 'LEAKED'\n"
            "except ValueError:\n"
            "    _missing = 'ValueError'\n"
            "except Exception as _e:\n"
            "    _missing = f'{type(_e).__name__}'\n"
            "result = {\n"
            "    'used_local': _used_local,\n"
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
        context = _context_for(_script_b64(source))
        # Hard-disable HTTP for every forked child: any SDK call that
        # reaches HTTP fails with connection-refused, so success proves
        # the local transport served all three read operations.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = template.fork(
                worker_id="sdk-exec-reads-fork", with_sdk=True
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
            work_queue.put(("exec-exec-reads-fork", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_local"] is True, result
            assert result["saw_workflow"] is True, result
            parent_list = await dispatch_frame(
                lambda: _factory(db_session),
                LocalDispatchPrincipal(caller_org_id=None),
                {"v": 1, "id": "fork-parity", "op": "workflows.list"},
            )
            assert parent_list["ok"] is True, parent_list
            from bifrost.models import WorkflowMetadata as SdkWorkflowMetadata

            for item in parent_list["result"]["items"]:
                SdkWorkflowMetadata.model_validate(item)
            assert any(
                item["name"] == wf_name
                for item in parent_list["result"]["items"]
            ), parent_list
            assert result["named_count"] == 3, result
            assert result["named_ids"] == sorted(exec_ids), result
            assert result["page1_count"] == 2, result
            assert result["page2_count"] >= 1, result
            assert result["detail_id"] == exec_ids[0], result
            assert result["detail_name"] == wf_name, result
            assert result["missing"] == "ValueError", result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result

            # The parent sees the same rows over its own session.
            rows = (
                await db_session.execute(
                    select(ExecutionModel).where(
                        ExecutionModel.workflow_name == wf_name
                    )
                )
            ).scalars().all()
            assert {str(r.id) for r in rows} == set(exec_ids)

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
