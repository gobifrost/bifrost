"""Engine-local form reads and client context through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the worker's private Unix socket and the synchronous
import transport at engine start, then runs ``bifrost.forms.list`` /
``bifrost.forms.get`` (now over the socket) plus the synchronous
``BifrostClient.context`` property (on the import channel) with the
network API dead and fixed-operation HTTP hard-disabled. The parent serves
the **real** form routes on that socket via uvicorn against the worker's
global database engine, and ``sdk.context`` on the import channel from the
shared service. Envelope success proves zero API requests for the migrated
operations, and the parent re-reads the same rows over its own session.

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
from sqlalchemy import select

from src.services.execution.sdk_local_dispatch import (
    IMPORT_CHANNEL_ALLOWED_OPS,
    LocalDispatchPrincipal,
    dispatch_frame,
    principal_from_context,
    serve_channel,
)
from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

pytestmark = pytest.mark.slow


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str, engine_token: str) -> dict:
    return {
        "execution_id": f"exec-forms-fork-{uuid4().hex[:8]}",
        "name": "sdk-forms-context-local-fork-test",
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
        "engine_token": engine_token,
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
class TestForkedFormsContextTransport:
    async def test_forms_over_socket_and_context_without_http(
        self, db_session, monkeypatch
    ):
        """A real forked child reads forms over the socket and context."""
        from src.core.security import mint_engine_token
        from src.models.enums import FormAccessLevel
        from src.models.orm.forms import Form as FormModel
        from src.models.orm.organizations import Organization as OrganizationModel

        stem = f"fork-forms-{uuid4().hex[:8]}"
        org = OrganizationModel(
            name=f"{stem}-org", is_active=True, created_by="fork-forms-test"
        )
        db_session.add(org)
        await db_session.flush()
        global_name = f"{stem}-global"
        org_name = f"{stem}-org"
        inactive_name = f"{stem}-inactive"
        for name, org_id, active in (
            (global_name, None, True),
            (org_name, org.id, True),
            (inactive_name, org.id, False),
        ):
            db_session.add(
                FormModel(
                    name=name,
                    access_level=FormAccessLevel.AUTHENTICATED,
                    organization_id=org_id,
                    is_active=active,
                    created_by="fork-forms-test",
                )
            )
        await db_session.flush()
        org_form_id = (
            await db_session.execute(
                select(FormModel.id).where(FormModel.name == org_name)
            )
        ).scalar_one()
        await db_session.commit()

        lines = [
            "import asyncio",
            "from bifrost import forms",
            "from bifrost.client import BifrostClient, get_engine_socket_path",
            "from bifrost._local_transport import get as _get_transport",
            "from bifrost._import_transport import get as _get_import_transport",
            "_used_socket = get_engine_socket_path() is not None",
            "_used_channel = _get_transport() is not None",
            "_used_import = _get_import_transport() is not None",
            "try:",
            "    asyncio.get_running_loop()",
            "    _loop_running = True",
            "except RuntimeError:",
            "    _loop_running = False",
            "def _dead(*args, **kwargs):",
            "    raise AssertionError(",
            "        'fixed-operation HTTP must not be used in the engine path'",
            "    )",
            "async def _dead_async(*args, **kwargs):",
            "    raise AssertionError(",
            "        'fixed-operation HTTP must not be used in the engine path'",
            "    )",
            "_client = BifrostClient('http://127.0.0.1:9', 'fork-dead-token')",
            "_client.get_sync = _dead",
            "_client.get = _dead_async",
            # Sync property from inside the active event loop: the
            # independent sync pipe must not deadlock the loop.
            "_ctx_direct = _client.context",
            "_wf_list = await forms.list()",
            "_saw = {_f.name for _f in _wf_list}",
            f"_saw_global = {global_name!r} in _saw",
            f"_saw_org = {org_name!r} in _saw",
            f"_saw_inactive = {inactive_name!r} in _saw",
            f"_detail = await forms.get({str(org_form_id)!r})",
            "try:",
            f"    await forms.get({str(uuid4())!r})",
            "    _missing = 'LEAKED'",
            "except ValueError:",
            "    _missing = 'ValueError'",
            "except Exception as _e:",
            "    _missing = f'{type(_e).__name__}'",
            # Sync context from a worker thread while async form calls
            # hold the socket: separate transports must not deadlock.
            "_fresh = BifrostClient('http://127.0.0.1:9', 'fork-dead-token')",
            "_fresh.get_sync = _dead",
            "_fresh.get = _dead_async",
            "_concurrent_forms, _concurrent_ctx = await asyncio.gather(",
            "    forms.list(),",
            "    asyncio.to_thread(lambda: _fresh.context),",
            ")",
            # Async fetch rides the local path too (instance HTTP dead).
            "_async_client = BifrostClient('http://127.0.0.1:9', 'fork-dead-token')",
            "_async_client.get_sync = _dead",
            "_async_client.get = _dead_async",
            "_ctx_async = await _async_client._fetch_context()",
            "import os, sys",
            "result = {",
            "    'used_socket': _used_socket,",
            "    'used_channel': _used_channel,",
            "    'used_import': _used_import,",
            "    'loop_running': _loop_running,",
            "    'saw_global': _saw_global,",
            "    'saw_org': _saw_org,",
            "    'saw_inactive': _saw_inactive,",
            "    'detail_id': _detail.id,",
            "    'detail_name': _detail.name,",
            "    'missing': _missing,",
            "    'ctx_user': _ctx_direct.get('user', {}),",
            "    'ctx_org': _ctx_direct.get('organization'),",
            "    'ctx_params': _ctx_direct.get('default_parameters'),",
            "    'ctx_track': _ctx_direct.get('track_executions'),",
            "    'concurrent_count': len(_concurrent_forms),",
            "    'concurrent_ctx_ok': _concurrent_ctx == _ctx_direct,",
            "    'async_ctx_ok': _ctx_async == _ctx_direct,",
            "    'had_db_url': (",
            "        'BIFROST_DATABASE_URL' in os.environ",
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ",
            "    ),",
            "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,",
            "}",
        ]
        context = _context_for(
            _script_b64("\n".join(lines) + "\n"),
            mint_engine_token(
                execution_id="gate-c5c-forms-fork",
                solution_id=None,
                global_repo_access=True,
                timeout_seconds=120,
            )[0],
        )
        # Hard-disable HTTP for every forked child: any SDK call that
        # reaches HTTP fails with connection-refused, so success proves
        # the socket and import transports served every migrated operation.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        server = WorkerSdkHttpServer()
        await server.start()
        assert server.socket_path is not None

        template = TemplateProcess()
        template.start()
        imp_pump = None
        conns = []
        try:
            (
                child_pid,
                work_queue,
                result_queue,
                imp_req,
                imp_resp,
            ) = template.fork(
                worker_id="sdk-forms-context-fork",
                with_import=True,
                sdk_socket_path=server.socket_path,
            )
            conns = [imp_req, imp_resp]
            principal = principal_from_context(context)
            assert isinstance(principal, LocalDispatchPrincipal)
            imp_pump = asyncio.create_task(
                serve_channel(
                    recv_conn=imp_req,
                    send_conn=imp_resp,
                    session_factory=lambda: _factory(db_session),
                    principal=principal,
                    allowed_ops=IMPORT_CHANNEL_ALLOWED_OPS,
                )
            )
            work_queue.put(("exec-forms-context-fork", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_socket"] is True, result
            assert result["used_channel"] is False, result
            assert result["used_import"] is True, result
            assert result["loop_running"] is True, result
            assert result["saw_global"] is True, result
            assert result["saw_org"] is True, result
            assert result["saw_inactive"] is True, result
            assert result["detail_id"] == str(org_form_id), result
            assert result["detail_name"] == org_name, result
            assert result["missing"] == "ValueError", result
            assert (
                result["ctx_user"].get("email") == "engine@bifrost.internal"
            ), result
            assert result["ctx_org"] is None, result
            assert result["ctx_params"] == {}, result
            assert result["ctx_track"] is True, result
            assert result["concurrent_count"] >= 3, result
            assert result["concurrent_ctx_ok"] is True, result
            assert result["async_ctx_ok"] is True, result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result

            # The parent sees the same rows over its own session, and
            # the context payload agrees with the shared service.
            parent_list = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {"v": 1, "id": "fork-parity-list", "op": "forms.list"},
            )
            assert parent_list["ok"] is True, parent_list
            assert {global_name, org_name, inactive_name} <= {
                f["name"] for f in parent_list["result"]["items"]
            }
            parent_get = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {
                    "v": 1,
                    "id": "fork-parity-get",
                    "op": "forms.get",
                    "form_id": str(org_form_id),
                },
            )
            assert parent_get["ok"] is True, parent_get
            assert parent_get["result"]["id"] == str(org_form_id)
            parent_ctx = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {"v": 1, "id": "fork-parity-ctx", "op": "sdk.context"},
            )
            assert parent_ctx["ok"] is True, parent_ctx
            assert (
                parent_ctx["result"]["user"].get("email")
                == "engine@bifrost.internal"
            )
            assert parent_ctx["result"] == {
                "user": result["ctx_user"],
                "organization": result["ctx_org"],
                "default_parameters": result["ctx_params"],
                "track_executions": result["ctx_track"],
            }, (parent_ctx, result)

            _wait_for_pid_to_die(child_pid)
            assert await asyncio.wait_for(imp_pump, timeout=15.0) == "eof"
            imp_pump = None
        finally:
            if imp_pump is not None:
                imp_pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await imp_pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()
            await server.stop()
