"""Engine-local users facade through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child is forked with the worker's private Unix socket injected exactly
as the pool does, then runs the full fixed ``bifrost.users`` facade —
create, get, scoped list (including a no-match scope), update, delete, and
a missing-user read. The test process serves the **real** user routes on
that socket via uvicorn against the worker's global database engine, with
the child's network API dead and fixed-operation HTTP unavailable. Success
proves every migrated call reached the parent-served routes, and the parent
re-reads the mutated rows over its own session to prove the real commit
boundary (the socket route commits where the HTTP ``get_db`` dependency
commits). A second fork with a non-admin initiating context proves the
socket authority comes from the engine token, never the child's claims.

Marked ``slow`` like the other real-fork tests: template boot costs
seconds. Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from uuid import uuid4

import pytest

from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

pytestmark = pytest.mark.slow


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(
    code_b64: str,
    engine_token: str,
    *,
    is_platform_admin: bool,
) -> dict:
    return {
        "execution_id": f"exec-users-fork-{uuid4().hex[:8]}",
        "name": "sdk-users-local-fork-test",
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
        "is_platform_admin": is_platform_admin,
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


async def _run_socket_fork(server: WorkerSdkHttpServer, context: dict) -> dict:
    """Fork one child against the worker socket and return its envelope."""
    template = TemplateProcess()
    template.start()
    try:
        child_pid, work_queue, result_queue = template.fork(
            worker_id="sdk-users-fork",
            sdk_socket_path=server.socket_path,
        )
        try:
            work_queue.put(("exec-users-fork", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
        finally:
            work_queue.close()
            result_queue.close()
        _wait_for_pid_to_die(child_pid)
        return envelope
    finally:
        template.shutdown()


@pytest.mark.asyncio
class TestForkedUsersSocket:
    async def test_crud_and_scoped_list_over_socket(
        self, async_session_factory, monkeypatch
    ):
        """A real forked child runs the users facade on the worker socket."""
        from src.core.security import mint_engine_token
        from src.models.orm.organizations import Organization as OrganizationModel

        stem = f"fork-users-{uuid4().hex[:8]}"
        async with async_session_factory() as seed:
            org = OrganizationModel(
                name=f"{stem}-org",
                is_active=True,
                created_by="users-fork-test",
            )
            seed.add(org)
            await seed.commit()
            org_id = str(org.id)
        email = f"{stem}@example.com"
        no_match_scope = str(uuid4())

        lines = [
            "import os, sys",
            "from bifrost import users",
            "from bifrost.client import get_engine_socket_path",
            "_used_socket = get_engine_socket_path() is not None",
            f"_created = await users.create({email!r}, 'Fork User', org_id={org_id!r})",
            "_user_id = _created.id",
            "_pending = _created.invite_status",
            "_has_url = _created.registration_url is not None",
            "_fetched = await users.get(_user_id)",
            f"_scoped = await users.list(org_id={org_id!r})",
            "_saw = _user_id in {u.id for u in _scoped}",
            f"_empty = await users.list(org_id={no_match_scope!r})",
            "_updated = await users.update(_user_id, name='Fork Renamed')",
            "_missing = await users.get('ghost-nobody@example.com')",
            "_deleted = await users.delete(_user_id)",
            "_gone = await users.get(_user_id)",
            "result = {",
            "    'used_socket': _used_socket,",
            "    'user_id': _user_id,",
            "    'pending': _pending,",
            "    'has_url': _has_url,",
            "    'fetched_email': _fetched.email,",
            "    'saw_in_scoped_list': _saw,",
            "    'empty_scope_total': len(_empty),",
            "    'updated_name': _updated.name,",
            "    'missing': _missing,",
            "    'deleted': _deleted,",
            "    'gone': _gone,",
            "    'had_db_url': (",
            "        'BIFROST_DATABASE_URL' in os.environ",
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ",
            "    ),",
            "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,",
            "}",
        ]
        engine_token, _ = mint_engine_token(
            execution_id="gate-c5d-users-fork",
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=120,
        )
        context = _context_for(
            _script_b64("\n".join(lines) + "\n"),
            engine_token,
            is_platform_admin=True,
        )
        # Hard-disable HTTP for the forked child: any SDK call that reaches
        # the network API fails with connection-refused, so success proves
        # the socket served every migrated operation.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        server = WorkerSdkHttpServer()
        await server.start()
        assert server.socket_path is not None
        try:
            envelope = await _run_socket_fork(server, context)
        finally:
            await server.stop()

        try:
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_socket"] is True, result
            assert result["pending"] == "pending", result
            assert result["has_url"] is True, result
            assert result["fetched_email"] == email, result
            assert result["saw_in_scoped_list"] is True, result
            assert result["empty_scope_total"] == 0, result
            assert result["updated_name"] == "Fork Renamed", result
            assert result["missing"] is None, result
            assert result["deleted"] is True, result
            assert result["gone"] is None, result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result

            # The parent sees the committed state over a separate connection:
            # the delete was really committed by the socket route (not just
            # flushed).
            from src.models import User as UserORM

            async with async_session_factory() as fresh:
                gone = await fresh.get(UserORM, result["user_id"])
                assert gone is None
        finally:
            # Remove committed seeds so later tests see a clean slate.
            from sqlalchemy import delete as sa_delete

            from src.models import User as UserORM
            from src.models.orm.organizations import Organization as OrganizationModel

            async with async_session_factory() as cleanup:
                await cleanup.execute(
                    sa_delete(UserORM).where(UserORM.email == email)
                )
                await cleanup.execute(
                    sa_delete(OrganizationModel).where(
                        OrganizationModel.name == f"{stem}-org"
                    )
                )
                await cleanup.commit()

    async def test_non_admin_initiator_uses_engine_superuser_over_socket(
        self, async_session_factory, monkeypatch
    ):
        """The socket authority is the engine token, not the child's claims."""
        from src.core.security import mint_engine_token

        lines = [
            "from bifrost import users",
            "from bifrost.client import get_engine_socket_path",
            "_used_socket = get_engine_socket_path() is not None",
            "_listed = await users.list()",
            "result = {",
            "    'used_socket': _used_socket,",
            "    'listed': isinstance(_listed, list),",
            "}",
        ]
        engine_token, _ = mint_engine_token(
            execution_id="gate-c5d-users-fork-nonadmin",
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=120,
        )
        context = _context_for(
            _script_b64("\n".join(lines) + "\n"),
            engine_token,
            is_platform_admin=False,
        )
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        server = WorkerSdkHttpServer()
        await server.start()
        assert server.socket_path is not None
        try:
            envelope = await _run_socket_fork(server, context)
        finally:
            await server.stop()

        assert envelope["success"] is True, envelope
        result = envelope["result"]
        assert result["used_socket"] is True, result
        assert result["listed"] is True, result
