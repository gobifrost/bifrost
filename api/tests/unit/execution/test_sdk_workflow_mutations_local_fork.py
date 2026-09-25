"""Engine-local workflow mutations through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the engine-local transport at engine start and runs
``bifrost.workflows.execute`` / ``cancel`` (scheduled enqueue plus
cancel) with fixed-workflow HTTP hard-disabled, while the parent serves
both ops from the shared ``shared.sdk_workflow_execution`` service.
Envelope success proves zero API requests for the migrated operations,
and the parent re-reads the durable execution row.

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
        "execution_id": f"workflows-fork-{uuid4().hex[:8]}",
        "name": "sdk-workflows-local-fork-test",
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
class TestForkedWorkflowMutationsTransport:
    async def test_scheduled_execute_and_cancel_without_http(
        self, db_session, monkeypatch
    ):
        """A real forked child schedules and cancels with HTTP dead."""
        from sqlalchemy import select

        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution as ExecutionModel
        from src.models.orm.workflows import Workflow as WorkflowModel

        wf_name = f"fork-wf-{uuid4().hex[:8]}"
        db_session.add(
            WorkflowModel(
                name=wf_name,
                function_name=f"{wf_name}_fn",
                path=f"workflows/{wf_name}.py",
                type="workflow",
                is_active=True,
                access_level="authenticated",
                cache_ttl_seconds=0,
            )
        )
        await db_session.commit()

        source = (
            "import importlib, os, sys\n"
            "from bifrost import workflows\n"
            "_wm = importlib.import_module('bifrost.workflows')\n"
            "from bifrost._local_transport import get as _get_transport\n"
            "_used_local = _get_transport() is not None\n"
            "def _dead(*args, **kwargs):\n"
            "    raise AssertionError(\n"
            "        'fixed-operation HTTP must not be used in the engine path'\n"
            "    )\n"
            "_wm.get_client = _dead\n"
            f"_eid = await workflows.execute({wf_name!r}, {{'ticket_id': 1}}, delay_seconds=3600)\n"
            "await workflows.cancel(_eid)\n"
            "try:\n"
            "    await workflows.cancel(_eid)\n"
            "    _duplicate = 'LEAKED'\n"
            "except Exception as _e:\n"
            "    _duplicate = f'{type(_e).__name__}:{getattr(getattr(_e, \"response\", None), \"status_code\", None)}'\n"
            "result = {\n"
            "    'used_local': _used_local,\n"
            "    'execution_id': _eid,\n"
            "    'duplicate': _duplicate,\n"
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
                worker_id="sdk-workflows-fork", with_sdk=True
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
            work_queue.put(("exec-workflows-fork", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_local"] is True, result
            assert isinstance(result["execution_id"], str), result
            # Duplicate cancel races the same guarded UPDATE and loses.
            assert result["duplicate"].endswith(":409"), result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result

            # The scheduled row is durable in the parent's database.
            row = (
                await db_session.execute(
                    select(ExecutionModel).where(
                        ExecutionModel.id == result["execution_id"]
                    )
                )
            ).scalar_one()
            assert row.status == ExecutionStatus.CANCELLED
            assert row.workflow_name == wf_name

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
