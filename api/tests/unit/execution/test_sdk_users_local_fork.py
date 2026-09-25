"""Engine-local users facade through a real forked worker.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child installs the engine-local transport at engine start, then runs
the full fixed ``bifrost.users`` facade — create, get, scoped list
(including a no-match scope), update, delete, and a missing-user read —
with fixed-operation HTTP hard-disabled, while the parent serves the
users ops from the shared ``sdk_users`` service. A second fork proves a
workflow started by a non-admin still uses the engine superuser token.
Service-token denial and forged child claims are covered by the
dispatcher tests. The parent re-reads the mutated rows over a separate
connection, proving the real commit boundary (the local session commits
where the HTTP ``get_db`` dependency commits).

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
        "execution_id": f"exec-users-fork-{uuid4().hex[:8]}",
        "name": "sdk-users-local-fork-test",
        "code": code_b64,
        "parameters": {},
        "caller": {
            "user_id": "fork-test-user",
            "email": "fork@example.com",
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
        ) = template.fork(worker_id="sdk-users-fork", with_sdk=True)
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
        work_queue.put(("exec-users-fork", context))
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
class TestForkedUsersTransport:
    async def test_crud_and_scoped_list_without_http(
        self, db_session, async_session_factory, monkeypatch
    ):
        """A real forked child runs the users facade with HTTP dead."""
        from src.models.orm.organizations import Organization as OrganizationModel

        stem = f"fork-users-{uuid4().hex[:8]}"
        org = OrganizationModel(
            name=f"{stem}-org",
            is_active=True,
            created_by="users-fork-test",
        )
        db_session.add(org)
        await db_session.flush()
        org_id = str(org.id)
        # Commit seeds so the parent can verify the child's local writes
        # over a separate connection (the real commit boundary).
        await db_session.commit()
        email = f"{stem}@example.com"
        no_match_scope = str(uuid4())

        lines = [
            "from bifrost import users",
            "from bifrost._local_transport import get as _get_transport",
            "import bifrost.users as _umod",
            "_used_local = _get_transport() is not None",
            "def _dead(*args, **kwargs):",
            "    raise AssertionError(",
            "        'fixed-operation HTTP must not be used in the engine path'",
            "    )",
            "_umod.get_client = _dead",
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
            "import os, sys",
            "result = {",
            "    'used_local': _used_local,",
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
            user_id = result["user_id"]

            # The parent sees the committed state over a separate
            # connection: the delete was really committed by the local
            # dispatcher (not just flushed on the pump session).
            from src.models import User as UserORM

            async with async_session_factory() as fresh:
                gone = await fresh.get(UserORM, user_id)
                assert gone is None

            # External HTTP behavior is unchanged: a scoped list over
            # the parent dispatcher filters to the requested org.
            principal = principal_from_context(context)
            parent_list = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {
                    "v": 1,
                    "id": "fork-parity-list",
                    "op": "users.list",
                    "scope": org_id,
                    "include_inactive": True,
                },
            )
            assert parent_list["ok"] is True, parent_list
            assert parent_list["result"]["total"] == 0
        finally:
            # Remove committed seeds so later tests see a clean slate.
            async with async_session_factory() as cleanup:
                from sqlalchemy import delete as sa_delete

                from src.models import User as UserORM
                from src.models.orm.organizations import (
                    Organization as OrganizationModel,
                )

                await cleanup.execute(
                    sa_delete(UserORM).where(UserORM.email == email)
                )
                await cleanup.execute(
                    sa_delete(OrganizationModel).where(
                        OrganizationModel.name == f"{stem}-org"
                    )
                )
                await cleanup.commit()

    async def test_non_admin_initiator_uses_engine_superuser_without_http(
        self, db_session, monkeypatch
    ):
        """A workflow uses its engine superuser token regardless of initiator."""
        lines = [
            "from bifrost import users",
            "from bifrost._local_transport import get as _get_transport",
            "import bifrost.users as _umod",
            "_used_local = _get_transport() is not None",
            "def _dead(*args, **kwargs):",
            "    raise AssertionError(",
            "        'fixed-operation HTTP must not be used in the engine path'",
            "    )",
            "_umod.get_client = _dead",
            "_listed = await users.list()",
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
