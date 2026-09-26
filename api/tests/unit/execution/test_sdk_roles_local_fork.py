"""Engine-local roles facade through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child is forked with the worker's private Unix socket injected exactly
as the pool does, then runs the full fixed ``bifrost.roles`` facade —
create, get, list, update, one assignment of each kind (users/forms), the
assignment reads, and a missing-role error. The test process serves the
**real** role routes on that socket via uvicorn against the worker's global
database engine, with the child's network API dead. Success proves every
migrated call reached the
parent-served routes, and the parent re-reads the mutated rows over its own
session to prove the real commit boundary (the socket route commits where
the HTTP ``get_db`` dependency commits). A second fork with a non-admin
initiating context proves the socket authority comes from the engine token,
never the child's claims.

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
from sqlalchemy import select

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
        "execution_id": f"exec-roles-fork-{uuid4().hex[:8]}",
        "name": "sdk-roles-local-fork-test",
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
            worker_id="sdk-roles-fork",
            sdk_socket_path=server.socket_path,
        )
        try:
            work_queue.put(("exec-roles-fork", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
        finally:
            work_queue.close()
            result_queue.close()
        _wait_for_pid_to_die(child_pid)
        return envelope
    finally:
        template.shutdown()


@pytest.mark.asyncio
class TestForkedRolesSocket:
    async def test_crud_and_assignments_over_socket(
        self, async_session_factory, monkeypatch
    ):
        """A real forked child runs the roles facade on the worker socket."""
        from src.core.security import mint_engine_token
        from src.models.enums import FormAccessLevel
        from src.models.orm.forms import Form as FormModel
        from src.models.orm.users import User as UserModel

        stem = f"fork-roles-{uuid4().hex[:8]}"
        async with async_session_factory() as seed:
            user = UserModel(
                email=f"{stem}@test.local",
                name="Fork Roles User",
                is_active=True,
                # NULL-org users must be superuser
                # (``ck_users_org_requires_superuser``).
                is_superuser=True,
            )
            form = FormModel(
                name=f"{stem}-form",
                access_level=FormAccessLevel.AUTHENTICATED,
                organization_id=None,
                is_active=True,
                created_by="roles-fork-test",
            )
            seed.add_all([user, form])
            await seed.commit()
            user_id, form_id = str(user.id), str(form.id)
        role_name = f"{stem}-role"

        lines = [
            "import os, sys",
            "from bifrost import roles",
            "from bifrost.client import get_engine_socket_path",
            "_used_socket = get_engine_socket_path() is not None",
            f"_created = await roles.create({role_name!r}, description='fork')",
            "_role_id = _created.id",
            "_fetched = await roles.get(_role_id)",
            "_listed = await roles.list()",
            "_saw = _role_id in {r.id for r in _listed}",
            "_updated = await roles.update(_role_id, description='fork-2')",
            f"_assigned_u = await roles.assign_users(_role_id, [{user_id!r}])",
            f"_assigned_f = await roles.assign_forms(_role_id, [{form_id!r}])",
            "_user_ids = await roles.list_users(_role_id)",
            "_form_ids = await roles.list_forms(_role_id)",
            "try:",
            f"    await roles.get({str(uuid4())!r})",
            "    _missing = 'LEAKED'",
            "except ValueError:",
            "    _missing = 'ValueError'",
            "except Exception as _e:",
            "    _missing = f'{type(_e).__name__}'",
            "result = {",
            "    'used_socket': _used_socket,",
            "    'role_id': _role_id,",
            "    'created_name': _created.name,",
            "    'fetched_name': _fetched.name,",
            "    'saw_in_list': _saw,",
            "    'updated_description': _updated.description,",
            "    'assign_users': _assigned_u,",
            "    'assign_forms': _assigned_f,",
            "    'user_ids': _user_ids,",
            "    'form_ids': _form_ids,",
            "    'missing': _missing,",
            "    'had_db_url': (",
            "        'BIFROST_DATABASE_URL' in os.environ",
            "        or 'BIFROST_DATABASE_URL_SYNC' in os.environ",
            "    ),",
            "    'had_sqlalchemy': 'sqlalchemy' in sys.modules,",
            "}",
        ]
        engine_token, _ = mint_engine_token(
            execution_id="gate-c5e-roles-fork",
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
            assert result["created_name"] == role_name, result
            assert result["fetched_name"] == role_name, result
            assert result["saw_in_list"] is True, result
            assert result["updated_description"] == "fork-2", result
            assert result["assign_users"] is None, result
            assert result["assign_forms"] is None, result
            assert result["user_ids"] == [user_id], result
            assert result["form_ids"] == [form_id], result
            assert result["missing"] == "ValueError", result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result
            role_id = result["role_id"]

            # The parent sees the committed state over a separate connection:
            # create, update, and both assignments were really committed by
            # the socket route (not just flushed on the request session).
            from src.models import FormRole as FormRoleORM
            from src.models import Role as RoleORM
            from src.models import UserRole as UserRoleORM

            async with async_session_factory() as fresh:
                role = await fresh.get(RoleORM, role_id)
                assert role is not None
                assert role.name == role_name
                assert role.description == "fork-2"
                user_rows = (
                    await fresh.execute(
                        select(UserRoleORM).where(
                            UserRoleORM.role_id == role.id
                        )
                    )
                ).scalars().all()
                assert [str(r.user_id) for r in user_rows] == [user_id]
                assert user_rows[0].assigned_by == "engine@bifrost.internal"
                form_rows = (
                    await fresh.execute(
                        select(FormRoleORM).where(
                            FormRoleORM.role_id == role.id
                        )
                    )
                ).scalars().all()
                assert [str(r.form_id) for r in form_rows] == [form_id]
        finally:
            # Remove committed seeds so later tests see a clean slate. The
            # role delete CASCADEs both assignments.
            from sqlalchemy import delete as sa_delete

            from src.models import Role as RoleORM
            from src.models.orm.forms import Form as FormModel
            from src.models.orm.users import User as UserModel

            async with async_session_factory() as cleanup:
                await cleanup.execute(
                    sa_delete(RoleORM).where(RoleORM.name == role_name)
                )
                await cleanup.execute(
                    sa_delete(FormModel).where(
                        FormModel.name == f"{stem}-form"
                    )
                )
                await cleanup.execute(
                    sa_delete(UserModel).where(
                        UserModel.email == f"{stem}@test.local"
                    )
                )
                await cleanup.commit()

    async def test_non_admin_initiator_uses_engine_superuser_over_socket(
        self, async_session_factory, monkeypatch
    ):
        """The socket authority is the engine token, not the child's claims."""
        from src.core.security import mint_engine_token

        lines = [
            "from bifrost import roles",
            "from bifrost.client import get_engine_socket_path",
            "_used_socket = get_engine_socket_path() is not None",
            "_listed = await roles.list()",
            "result = {",
            "    'used_socket': _used_socket,",
            "    'listed': isinstance(_listed, list),",
            "}",
        ]
        engine_token, _ = mint_engine_token(
            execution_id="gate-c5e-roles-fork-nonadmin",
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
