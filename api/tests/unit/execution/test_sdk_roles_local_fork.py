"""Engine-local roles facade through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the engine-local transport at engine start, then runs
the full fixed ``bifrost.roles`` facade — one mutation (create/update),
one assignment of each kind, reads, and a missing-role error — with
fixed-operation HTTP hard-disabled, while the parent serves the roles
ops from the shared ``sdk_roles`` service. A second fork proves a workflow
started by a non-admin still uses the engine superuser token. Service-token
denial and forged child claims are covered by the dispatcher tests. The
parent re-reads the mutated rows over
a separate connection, proving the real commit boundary (the local
session commits where the HTTP ``get_db`` dependency commits).

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
    SDK_CHANNEL_ALLOWED_OPS,
    LocalDispatchPrincipal,
    dispatch_frame,
    principal_from_context,
    serve_channel,
)
from src.services.execution.template_process import TemplateProcess

pytestmark = pytest.mark.slow


def _script_b64(source: str) -> str:
    return base64.b64encode(source.encode("utf-8")).decode("utf-8")


def _context_for(code_b64: str, *, admin: bool) -> dict:
    return {
        "execution_id": f"exec-roles-fork-{uuid4().hex[:8]}",
        "name": "sdk-roles-local-fork-test",
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
        "is_platform_admin": admin,
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


async def _run_fork(context, db_session):
    """Boot one template child, pump its SDK channel, return its envelope."""
    template = TemplateProcess()
    template.start()
    sdk_pump = None
    conns = []
    try:
        (
            child_pid,
            work_queue,
            result_queue,
            sdk_req,
            sdk_resp,
        ) = template.fork(worker_id="sdk-roles-fork", with_sdk=True)
        conns = [sdk_req, sdk_resp]
        principal = principal_from_context(context)
        assert isinstance(principal, LocalDispatchPrincipal)
        sdk_pump = asyncio.create_task(
            serve_channel(
                recv_conn=sdk_req,
                send_conn=sdk_resp,
                session_factory=lambda: _factory(db_session),
                principal=principal,
                allowed_ops=SDK_CHANNEL_ALLOWED_OPS,
            )
        )
        work_queue.put(("exec-roles-fork", context))
        envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
        _wait_for_pid_to_die(child_pid)
        assert await asyncio.wait_for(sdk_pump, timeout=15.0) == "eof"
        sdk_pump = None
        return envelope
    finally:
        if sdk_pump is not None:
            sdk_pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sdk_pump
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()
        template.shutdown()


@pytest.mark.asyncio
class TestForkedRolesTransport:
    async def test_mutation_and_assignment_without_http(
        self, db_session, async_session_factory, monkeypatch
    ):
        """A real forked child runs the roles facade with HTTP dead."""
        from src.models.enums import FormAccessLevel
        from src.models.orm.forms import Form as FormModel
        from src.models.orm.users import User as UserModel

        stem = f"fork-roles-{uuid4().hex[:8]}"
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
        db_session.add_all([user, form])
        await db_session.flush()
        user_id, form_id = str(user.id), str(form.id)
        # Commit seeds so the parent can verify the child's local writes
        # over a separate connection (the real commit boundary).
        await db_session.commit()
        role_name = f"{stem}-role"

        lines = [
            "from bifrost import roles",
            "from bifrost._local_transport import get as _get_transport",
            "import bifrost.roles as _rmod",
            "_used_local = _get_transport() is not None",
            "def _dead(*args, **kwargs):",
            "    raise AssertionError(",
            "        'fixed-operation HTTP must not be used in the engine path'",
            "    )",
            "_rmod.get_client = _dead",
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
            "import os, sys",
            "result = {",
            "    'used_local': _used_local,",
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
        context = _context_for(_script_b64("\n".join(lines) + "\n"), admin=True)
        # Hard-disable HTTP for every forked child: any SDK call that
        # reaches HTTP fails, so success proves the local transport
        # served every migrated operation.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        try:
            envelope = await _run_fork(context, db_session)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["used_local"] is True, result
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

            # The parent sees the committed state over a separate
            # connection: create, update, and both assignments were
            # really committed by the local dispatcher (not just
            # flushed on the pump session).
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

            # External HTTP behavior is unchanged: the same rows read
            # back through the parent dispatcher like an HTTP handler.
            principal = principal_from_context(context)
            parent_get = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {
                    "v": 1,
                    "id": "fork-parity-get",
                    "op": "roles.get",
                    "role_id": role_id,
                },
            )
            assert parent_get["ok"] is True, parent_get
            assert parent_get["result"]["description"] == "fork-2"
        finally:
            # Remove committed seeds so later tests see a clean slate.
            # Delete through the shared service in a fresh session to cover
            # the HTTP/local deletion path with existing assignments.
            async with async_session_factory() as cleanup:
                from sqlalchemy import delete as sa_delete

                from shared.sdk_roles import delete_role
                from src.models import Role as RoleORM
                from src.models.orm.forms import Form as FormModel
                from src.models.orm.users import User as UserModel

                role_ids = (
                    await cleanup.execute(
                        select(RoleORM.id).where(RoleORM.name == role_name)
                    )
                ).scalars().all()
                for rid in role_ids:
                    await delete_role(cleanup, role_id=rid)
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

    async def test_non_admin_initiator_uses_engine_superuser_without_http(
        self, db_session, monkeypatch
    ):
        """A workflow uses its engine superuser token regardless of initiator."""
        lines = [
            "from bifrost import roles",
            "from bifrost._local_transport import get as _get_transport",
            "import bifrost.roles as _rmod",
            "_used_local = _get_transport() is not None",
            "def _dead(*args, **kwargs):",
            "    raise AssertionError(",
            "        'fixed-operation HTTP must not be used in the engine path'",
            "    )",
            "_rmod.get_client = _dead",
            "_listed = await roles.list()",
            "result = {'used_local': _used_local, 'listed': isinstance(_listed, list)}",
        ]
        context = _context_for(
            _script_b64("\n".join(lines) + "\n"), admin=False
        )
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        envelope = await _run_fork(context, db_session)
        assert envelope["success"] is True, envelope
        result = envelope["result"]
        assert result["used_local"] is True, result
        assert result["listed"] is True, result
