"""Engine-local form reads and client context through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the worker's private Unix socket at engine start, then
runs ``bifrost.forms.list`` / ``bifrost.forms.get`` plus the synchronous
``BifrostClient.context`` property and the async ``_fetch_context`` over
that socket, with the network API dead and fixed-operation HTTP
hard-disabled. The parent serves the **real** form routes and the original
``GET /api/sdk/context`` route on that socket via uvicorn against the
worker's global database engine. Envelope success proves zero API requests
for the migrated operations, and the parent re-reads the same rows and the
same context payload over its own session.

The child installs the worker's private Unix socket: context and forms both
ride it, and a synchronous context read while async form calls are in flight
proves the separate sync/async socket connections cannot deadlock.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

pytestmark = pytest.mark.slow

# The signed subject of a minted engine token (``mint_engine_token``), used
# to build the parent's token-equivalent principal for parity.
_ENGINE_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


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
    async def test_forms_and_context_over_socket_without_http(
        self, db_session, monkeypatch
    ):
        """A real forked child reads forms and context over the socket."""
        from src.core.principal import UserPrincipal
        from src.core.security import ENGINE_SDK_ACTOR_EMAIL, mint_engine_token
        from src.models.enums import FormAccessLevel
        from src.models.orm.forms import Form as FormModel
        from src.models.orm.organizations import Organization as OrganizationModel
        from shared.sdk_context import get_sdk_context
        from shared.sdk_forms import list_sdk_forms

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
            "from bifrost.client import (",
            "    BifrostClient,",
            "    get_client,",
            "    get_engine_socket_path,",
            ")",
            "_used_socket = get_engine_socket_path() is not None",
            "try:",
            "    asyncio.get_running_loop()",
            "    _loop_running = True",
            "except RuntimeError:",
            "    _loop_running = False",
            # Sync property from inside the active event loop: its own
            # synchronous socket connection must not deadlock the loop.
            "_client = get_client()",
            "_ctx_direct = _client.context",
            "_used_sync_socket = _client._engine_sync_http is not None",
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
            # The deadlock probe: a synchronous context read on a worker
            # thread while async form calls are in flight over the socket.
            # Separate sync/async HTTPX connections share the socket, not a
            # single channel lock.
            "_fresh = BifrostClient(_client.api_url, _client._access_token)",
            "_concurrent_forms, _concurrent_ctx = await asyncio.gather(",
            "    forms.list(),",
            "    asyncio.to_thread(lambda: _fresh.context),",
            ")",
            # Async fetch rides the local socket too.
            "_async_client = BifrostClient(_client.api_url, _client._access_token)",
            "_ctx_async = await _async_client._fetch_context()",
            "_used_async_socket = _async_client._engine_http is not None",
            "import os, sys",
            "result = {",
            "    'used_socket': _used_socket,",
            "    'loop_running': _loop_running,",
            "    'used_sync_socket': _used_sync_socket,",
            "    'used_async_socket': _used_async_socket,",
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
                execution_id="gate-c5h-forms-fork",
                solution_id=None,
                global_repo_access=True,
                timeout_seconds=120,
            )[0],
        )
        # Hard-disable HTTP for the forked child: any SDK call that reaches
        # HTTP fails with connection-refused, so success proves the socket
        # served every migrated operation.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        server = WorkerSdkHttpServer()
        await server.start()
        assert server.socket_path is not None

        template = TemplateProcess()
        template.start()
        try:
            child_pid, work_queue, result_queue = template.fork(
                worker_id="sdk-forms-context-fork",
                sdk_socket_path=server.socket_path,
            )
            work_queue.put(("exec-forms-context-fork", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_socket"] is True, result
            assert result["loop_running"] is True, result
            assert result["used_sync_socket"] is True, result
            assert result["used_async_socket"] is True, result
            assert result["saw_global"] is True, result
            assert result["saw_org"] is True, result
            assert result["saw_inactive"] is True, result
            assert result["detail_id"] == str(org_form_id), result
            assert result["detail_name"] == org_name, result
            assert result["missing"] == "ValueError", result
            assert result["ctx_user"].get("email") == ENGINE_SDK_ACTOR_EMAIL, result
            assert result["ctx_org"] is None, result
            assert result["ctx_params"] == {}, result
            assert result["ctx_track"] is True, result
            assert result["concurrent_count"] >= 3, result
            assert result["concurrent_ctx_ok"] is True, result
            assert result["async_ctx_ok"] is True, result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result

            # External parity: the child's context equals the shared service
            # output for the token-equivalent engine principal, and the
            # parent sees the same seeded rows over its own session.
            engine_user = UserPrincipal(
                user_id=_ENGINE_USER_ID,
                email=ENGINE_SDK_ACTOR_EMAIL,
                name="Bifrost Engine",
                organization_id=None,
                is_superuser=True,
            )
            expected_ctx = await get_sdk_context(db_session, engine_user, org_id=None)
            child_ctx = {
                "user": result["ctx_user"],
                "organization": result["ctx_org"],
                "default_parameters": result["ctx_params"],
                "track_executions": result["ctx_track"],
            }
            assert child_ctx == expected_ctx, (child_ctx, expected_ctx)

            parent_forms = await list_sdk_forms(db_session, engine_user, scope=None)
            assert {global_name, org_name, inactive_name} <= {
                f.name for f in parent_forms
            }

            _wait_for_pid_to_die(child_pid)
        finally:
            template.shutdown()
            await server.stop()
