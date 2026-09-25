"""Engine-local transport for ``bifrost.workflows.execute`` / ``cancel``.

Covers the acceptance surface that does not need a forked child:

- the parent dispatcher calls the shared ``shared.sdk_workflow_execution``
  service with a parent-derived actor (engine superuser vs service
  non-superuser — never child fields) and maps normal enqueue, scheduled
  enqueue/cancel, duplicate/late cancel, cross-org and ``run_as``
  denial, sealed/inactive Solution denial, and malformed frames to
  HTTP-style statuses;
- the child transport performs real execute/cancel round trips with
  zero HTTP requests and no silent HTTP fallback;
- the SDK facade maps local results to the public surface (execution-id
  string, ``None`` on cancel) and preserves the existing exception
  mapping and client-side ``scheduled_at``/``delay_seconds`` validation;
- external callers (no transport) keep the HTTP path unchanged.
"""

from __future__ import annotations

import contextlib
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost._local_transport import OP_WORKFLOWS_CANCEL, OP_WORKFLOWS_EXECUTE
from src.models.enums import ExecutionStatus


@pytest_asyncio.fixture
async def db_session(async_engine):
    """Exercise real commits while rolling back seeded rows after each test."""
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _context_data(org_id=None, **kwargs):
    data = {
        "organization": {"id": str(org_id)} if org_id is not None else None,
        "is_platform_admin": kwargs.get("is_platform_admin", False),
        "execution_id": kwargs.get("execution_id", "exec-1"),
    }
    if kwargs.get("solution_id") is not None:
        data["solution_id"] = str(kwargs["solution_id"])
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _engine_principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(org_id, **kwargs))


def _service_principal(org_id, **kwargs):
    service_id = str(kwargs.get("service_id", uuid4()))
    attempt_id = str(kwargs.get("attempt_id", uuid4()))
    return _engine_principal(
        org_id,
        service={"service_id": service_id, "attempt_id": attempt_id},
        execution_id=attempt_id,
    )


def _workflow_user(principal):
    from src.services.execution.sdk_local_dispatch import (
        _workflow_user_for_principal,
    )

    return _workflow_user_for_principal(principal)


async def _seed_org(db_session):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-wfmut-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="sdk-wfmut-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_user(db_session, *, org_id=None):
    from src.models import User as UserORM

    row = UserORM(
        email=f"sdk-wfmut-{uuid4().hex[:8]}@example.com",
        name="SDK WfMut",
        organization_id=org_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_workflow(db_session, name, *, org_id=None, **kwargs):
    from src.models.orm.workflows import Workflow as WorkflowModel

    row = WorkflowModel(
        name=name,
        function_name=kwargs.get("function_name", f"{name}_fn"),
        path=kwargs.get("path", f"workflows/{name}.py"),
        type=kwargs.get("type", "workflow"),
        organization_id=org_id,
        is_active=kwargs.get("is_active", True),
        access_level=kwargs.get("access_level", "authenticated"),
        cache_ttl_seconds=kwargs.get("cache_ttl_seconds", 0),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_solution(db_session, *, status="active", org_id=None, **kwargs):
    from src.models.orm.solutions import Solution as SolutionModel

    row = SolutionModel(
        slug=f"sdk-wfmut-sol-{uuid4().hex[:8]}",
        name="SDK Workflow Mutations Solution",
        status=status,
        organization_id=org_id,
        allow_inbound_access=kwargs.get("allow_inbound_access", False),
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _pending_response(execution_id="exec-1", **kwargs):
    from src.models import WorkflowExecutionResponse

    return WorkflowExecutionResponse(
        execution_id=execution_id,
        workflow_id=kwargs.get("workflow_id"),
        workflow_name=kwargs.get("workflow_name", "wf"),
        status=kwargs.get("status", ExecutionStatus.PENDING),
        result=kwargs.get("result"),
    )


def _patch_dispatch(run_result=None):
    """Patch queue/cache/publish seams at their source modules."""
    run_workflow = AsyncMock(return_value=run_result or _pending_response())
    get_meta = AsyncMock(
        return_value={
            "name": "wf",
            "timeout_seconds": 60,
            "type": "workflow",
            "cache_ttl_seconds": 0,
        }
    )
    return (
        patch("src.services.execution.service.run_workflow", run_workflow),
        patch(
            "src.services.execution.service.get_workflow_for_execution", get_meta
        ),
        patch("src.core.pubsub.publish_execution_update", AsyncMock()),
        patch("src.core.pubsub.publish_history_update", AsyncMock()),
    )


def _start_patches(patches):
    return [p.start() for p in patches]


def _stop_patches(patches):
    for p in patches:
        p.stop()


def _execute_frame(workflow, **kwargs):
    frame = {
        "v": 1,
        "id": kwargs.pop("frame_id", "wf-exec-1"),
        "op": OP_WORKFLOWS_EXECUTE,
        "workflow": workflow,
        "input_data": kwargs.pop("input_data", {}),
    }
    for key in (
        "org_id",
        "run_as",
        "solution",
        "caller_solution",
        "scheduled_at",
        "delay_seconds",
    ):
        if key in kwargs:
            frame[key] = kwargs.pop(key)
    frame.update(kwargs)
    return frame


def _cancel_frame(execution_id, frame_id="wf-cancel-1"):
    return {
        "v": 1,
        "id": frame_id,
        "op": OP_WORKFLOWS_CANCEL,
        "execution_id": execution_id,
    }


async def _dispatch(frame, principal, db_session):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session), principal, frame
    )


async def _scheduled_row(db_session, execution_id):
    from src.models.orm.executions import Execution as ExecutionModel

    return (
        await db_session.execute(
            select(ExecutionModel).where(
                ExecutionModel.id == UUID(execution_id)
            )
        )
    ).scalar_one()


async def _delete_execution(db_session, execution_id):
    from src.models.orm.executions import Execution as ExecutionModel

    await db_session.execute(
        delete(ExecutionModel).where(
            ExecutionModel.id == UUID(execution_id)
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
class TestExecuteDispatch:
    async def test_normal_enqueue_returns_execution_id_and_queue_status(
        self, db_session
    ):
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _engine_principal()

        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            response = await _dispatch(
                _execute_frame(str(wf.id), input_data={"x": 1}),
                principal,
                db_session,
            )
        finally:
            _stop_patches(patches)

        assert response["ok"] is True, response
        assert response["result"]["execution_id"] == "exec-1"
        assert response["result"]["status"] == ExecutionStatus.PENDING.value
        mocks[0].assert_awaited_once()  # run_workflow

    async def test_scheduled_enqueue_inserts_durable_row(self, db_session):
        from src.models.orm.executions import Execution as ExecutionModel

        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _engine_principal(org.id)

        patches = _patch_dispatch()
        _start_patches(patches)
        try:
            response = await _dispatch(
                _execute_frame(str(wf.id), delay_seconds=3600),
                principal,
                db_session,
            )
        finally:
            _stop_patches(patches)

        assert response["ok"] is True, response
        assert response["result"]["status"] == ExecutionStatus.SCHEDULED.value
        assert response["result"]["scheduled_at"] is not None
        execution_id = response["result"]["execution_id"]

        row = await _scheduled_row(db_session, execution_id)
        assert row.status == ExecutionStatus.SCHEDULED
        assert row.workflow_id == wf.id
        assert row.scheduled_at is not None
        assert row.organization_id == org.id
        # Engine children enqueue as the engine sentinel, like HTTP.
        from src.core.constants import SYSTEM_USER_UUID

        assert row.executed_by == SYSTEM_USER_UUID

        await db_session.execute(
            delete(ExecutionModel).where(ExecutionModel.id == row.id)
        )
        await db_session.commit()

    async def test_scheduled_enqueue_with_scheduled_at(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _engine_principal(org.id)
        run_at = datetime.now(timezone.utc) + timedelta(hours=2)

        response = await _dispatch(
            _execute_frame(str(wf.id), scheduled_at=run_at.isoformat()),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        assert response["result"]["status"] == ExecutionStatus.SCHEDULED.value
        await _delete_execution(db_session, response["result"]["execution_id"])

    async def test_forged_actor_fields_are_ignored(self, db_session):
        from src.core.constants import SYSTEM_USER_UUID

        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _engine_principal(org.id)

        response = await _dispatch(
            _execute_frame(
                str(wf.id),
                delay_seconds=3600,
                caller_email="attacker@evil.local",
                caller_user_id=str(uuid4()),
                actor_email="attacker@evil.local",
            ),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        row = await _scheduled_row(db_session, response["result"]["execution_id"])
        assert row.executed_by == SYSTEM_USER_UUID
        assert row.executed_by_name != "attacker@evil.local"
        await _delete_execution(db_session, response["result"]["execution_id"])

    async def test_unknown_workflow_is_404(self, db_session):
        principal = _engine_principal()
        response = await _dispatch(
            _execute_frame(str(uuid4())), principal, db_session
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_run_as_denied_for_service_principal(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}", org_id=org.id)
        target = await _seed_user(db_session, org_id=org.id)
        principal = _service_principal(org.id)

        patches = _patch_dispatch()
        _start_patches(patches)
        try:
            response = await _dispatch(
                _execute_frame(str(wf.id), run_as=str(target.id)),
                principal,
                db_session,
            )
        finally:
            _stop_patches(patches)

        assert response["ok"] is False
        assert response["status"] == 403

    async def test_org_override_denied_for_service_principal(self, db_session):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}", org_id=org_b.id)
        principal = _service_principal(org_b.id)

        patches = _patch_dispatch()
        _start_patches(patches)
        try:
            response = await _dispatch(
                _execute_frame(str(wf.id), org_id=str(org_a.id)),
                principal,
                db_session,
            )
        finally:
            _stop_patches(patches)

        assert response["ok"] is False
        assert response["status"] == 403

    async def test_sealed_solution_target_is_404(self, db_session):
        org = await _seed_org(db_session)
        stem = f"sealed-{uuid4().hex[:8]}"
        path = f"workflows/{stem}.py"
        await _seed_workflow(db_session, stem, path=path, function_name="main")
        sealed = await _seed_solution(
            db_session, org_id=org.id, allow_inbound_access=False
        )
        principal = _engine_principal(org.id)

        # The SDK sends no own-install attestation outside a Solution
        # execution, so the sealed target must deny (not own-call bypass).
        response = await _dispatch(
            _execute_frame(
                f"{path}::main",
                solution=str(sealed.id),
                caller_solution=str(sealed.id),
            ),
            principal,
            db_session,
        )

        assert response["ok"] is False
        assert response["status"] == 404

    async def test_inactive_solution_target_is_404(self, db_session):
        org = await _seed_org(db_session)
        stem = f"inactive-{uuid4().hex[:8]}"
        path = f"workflows/{stem}.py"
        await _seed_workflow(db_session, stem, path=path, function_name="main")
        inactive = await _seed_solution(db_session, status="inactive", org_id=org.id)
        principal = _engine_principal(org.id)

        response = await _dispatch(
            _execute_frame(f"{path}::main", solution=str(inactive.id)),
            principal,
            db_session,
        )

        assert response["ok"] is False
        assert response["status"] == 404

    async def test_malformed_execute_is_422(self, db_session):
        principal = _engine_principal()

        missing_ref = await _dispatch(
            {"v": 1, "id": "bad-1", "op": OP_WORKFLOWS_EXECUTE, "input_data": {}},
            principal,
            db_session,
        )
        assert missing_ref["ok"] is False
        assert missing_ref["status"] == 422

        both = await _dispatch(
            _execute_frame(
                str(uuid4()),
                scheduled_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                delay_seconds=60,
                frame_id="bad-2",
            ),
            principal,
            db_session,
        )
        assert both["ok"] is False
        assert both["status"] == 422

        naive = await _dispatch(
            _execute_frame(
                str(uuid4()),
                scheduled_at=(datetime.now() + timedelta(hours=1)).isoformat(),
                frame_id="bad-3",
            ),
            principal,
            db_session,
        )
        assert naive["ok"] is False
        assert naive["status"] == 422

    async def test_malformed_scope_is_422(self, db_session):
        principal = _engine_principal()
        response = await _dispatch(
            _execute_frame(str(uuid4()), org_id="not-a-uuid", frame_id="bad-scope"),
            principal,
            db_session,
        )
        assert response["ok"] is False
        assert response["status"] == 422

    async def test_http_and_local_execute_agree(self, db_session):
        from shared.sdk_workflow_execution import execute_sdk_workflow
        from src.models import WorkflowExecutionRequest

        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _engine_principal()
        user = _workflow_user(principal)
        request = WorkflowExecutionRequest(workflow_id=str(wf.id), input_data={})

        patches = _patch_dispatch()
        _start_patches(patches)
        try:
            expected = await execute_sdk_workflow(
                db_session, user, request, caller_org_id=None
            )
            local = await _dispatch(
                _execute_frame(str(wf.id), frame_id="parity-1"),
                principal,
                db_session,
            )
        finally:
            _stop_patches(patches)

        assert local["ok"] is True, local
        assert local["result"] == expected.model_dump(mode="json")


@pytest.mark.asyncio
class TestCancelDispatch:
    async def _scheduled_via_local(self, db_session, principal, wf):
        patches = _patch_dispatch()
        _start_patches(patches)
        try:
            response = await _dispatch(
                _execute_frame(str(wf.id), delay_seconds=3600),
                principal,
                db_session,
            )
        finally:
            _stop_patches(patches)
        assert response["ok"] is True, response
        return response["result"]["execution_id"]

    async def test_cancel_scheduled_row(self, db_session):
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _engine_principal()
        execution_id = await self._scheduled_via_local(db_session, principal, wf)

        response = await _dispatch(
            _cancel_frame(execution_id), principal, db_session
        )

        assert response["ok"] is True, response
        assert response["result"]["execution_id"] == execution_id
        assert response["result"]["status"] == ExecutionStatus.CANCELLED.value
        row = await _scheduled_row(db_session, execution_id)
        assert row.status == ExecutionStatus.CANCELLED
        await _delete_execution(db_session, execution_id)

    async def test_duplicate_cancel_is_409(self, db_session):
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _engine_principal()
        execution_id = await self._scheduled_via_local(db_session, principal, wf)

        first = await _dispatch(_cancel_frame(execution_id), principal, db_session)
        assert first["ok"] is True, first
        second = await _dispatch(
            _cancel_frame(execution_id, frame_id="dup-2"), principal, db_session
        )
        assert second["ok"] is False
        assert second["status"] == 409
        assert ExecutionStatus.CANCELLED.value in second["detail"]
        await _delete_execution(db_session, execution_id)

    async def test_late_cancel_after_promotion_is_409(self, db_session):
        from src.models.orm.executions import Execution as ExecutionModel

        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _engine_principal()
        execution_id = await self._scheduled_via_local(db_session, principal, wf)

        row = await _scheduled_row(db_session, execution_id)
        row.status = ExecutionStatus.PENDING
        await db_session.commit()

        response = await _dispatch(
            _cancel_frame(execution_id), principal, db_session
        )
        assert response["ok"] is False
        assert response["status"] == 409
        assert ExecutionStatus.PENDING.value in response["detail"]

        await db_session.execute(
            delete(ExecutionModel).where(ExecutionModel.id == row.id)
        )
        await db_session.commit()

    async def test_cancel_missing_is_404(self, db_session):
        principal = _engine_principal()
        response = await _dispatch(
            _cancel_frame(str(uuid4())), principal, db_session
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_cancel_cross_org_is_403(self, db_session):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}", org_id=org_a.id)
        seeder = _engine_principal(org_a.id)
        execution_id = await self._scheduled_via_local(db_session, seeder, wf)

        other = await _seed_user(db_session, org_id=org_b.id)
        from src.models.orm.executions import Execution as ExecutionModel

        row = await _scheduled_row(db_session, execution_id)
        row.executed_by = other.id
        await db_session.commit()

        # A service principal is the non-admin engine caller: org-confined,
        # so another org's row (and another submitter's row) denies.
        caller = _service_principal(org_b.id)
        response = await _dispatch(
            _cancel_frame(execution_id), caller, db_session
        )
        assert response["ok"] is False
        assert response["status"] == 403

        await db_session.execute(
            delete(ExecutionModel).where(ExecutionModel.id == row.id)
        )
        await db_session.commit()

    async def test_cancel_malformed_id_is_422(self, db_session):
        principal = _engine_principal()

        missing = await _dispatch(
            {"v": 1, "id": "bad-c1", "op": OP_WORKFLOWS_CANCEL},
            principal,
            db_session,
        )
        assert missing["ok"] is False
        assert missing["status"] == 422

        malformed = await _dispatch(
            _cancel_frame("not-a-uuid", frame_id="bad-c2"),
            principal,
            db_session,
        )
        assert malformed["ok"] is False
        assert malformed["status"] == 422

    async def test_http_and_local_cancel_agree(self, db_session):
        from shared.sdk_workflow_execution import cancel_scheduled_sdk_execution

        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _engine_principal()
        user = _workflow_user(principal)
        first_id = await self._scheduled_via_local(db_session, principal, wf)
        second_id = await self._scheduled_via_local(db_session, principal, wf)

        expected = await cancel_scheduled_sdk_execution(
            db_session, user, UUID(first_id), caller_org_id=None
        )
        local = await _dispatch(
            _cancel_frame(second_id, frame_id="parity-c"), principal, db_session
        )

        assert local["ok"] is True, local
        assert local["result"]["status"] == expected["status"]
        assert local["result"]["execution_id"] == second_id
        await _delete_execution(db_session, first_id)
        await _delete_execution(db_session, second_id)


@pytest.mark.asyncio
class TestFacadeLocalMapping:
    def _install_fake_transport(self, monkeypatch, fake):
        import bifrost._local_transport as local_transport

        monkeypatch.setattr(local_transport, "_installed", fake)

        def _dead(*args, **kwargs):
            raise AssertionError("HTTP must not be used in the engine path")

        # NOTE: patch via sys.modules — the ``bifrost.workflows`` attribute
        # on the package is the ``workflows`` facade class, not this module.
        monkeypatch.setattr(sys.modules["bifrost.workflows"], "get_client", _dead)

    async def test_execute_uses_local_and_returns_execution_id(self, monkeypatch):
        from bifrost.workflows import workflows

        seen = {}

        class FakeTransport:
            async def call_workflows_execute(self, *args, **kwargs):
                seen["args"] = args
                seen["kwargs"] = kwargs
                return {
                    "execution_id": "exec-local-1",
                    "status": ExecutionStatus.PENDING.value,
                }

        self._install_fake_transport(monkeypatch, FakeTransport())
        eid = await workflows.execute("workflows/child.py::main", {"x": 1})

        assert eid == "exec-local-1"
        assert seen["args"][0] == "workflows/child.py::main"
        assert seen["args"][1] == {"x": 1}

    async def test_execute_forwards_scope_solution_and_schedule(self, monkeypatch):
        from bifrost._execution_context import ExecutionContext, Organization
        from bifrost._context import set_execution_context, clear_execution_context
        from bifrost.workflows import workflows

        seen = {}

        class FakeTransport:
            async def call_workflows_execute(self, *args, **kwargs):
                seen["args"] = args
                return {"execution_id": "exec-local-2"}

        org_id = str(uuid4())
        solution_id = str(uuid4())
        ctx = ExecutionContext(
            user_id="u",
            email="u@example.com",
            name="User",
            scope=org_id,
            organization=Organization(id=org_id, name="Org"),
            is_platform_admin=False,
            is_function_key=False,
            execution_id="exec",
            solution_id=solution_id,
        )
        set_execution_context(ctx)
        try:
            self._install_fake_transport(monkeypatch, FakeTransport())
            run_at = datetime.now(timezone.utc) + timedelta(hours=1)
            eid = await workflows.execute(
                "wf",
                {"x": 1},
                run_as="22222222-2222-2222-2222-222222222222",
                scheduled_at=run_at,
            )
        finally:
            clear_execution_context()

        assert eid == "exec-local-2"
        # The requested target rides the frame; caller identity stays
        # parent-owned. Remaining fields are the schedule and delay.
        assert seen["args"][2] == org_id
        assert seen["args"][3] == "22222222-2222-2222-2222-222222222222"
        assert seen["args"][4] == solution_id
        assert seen["args"][5] == run_at.isoformat()
        assert seen["args"][6] is None

    async def test_execute_client_validation_runs_before_local(self, monkeypatch):
        from bifrost.workflows import workflows

        called = {"n": 0}

        class FakeTransport:
            async def call_workflows_execute(self, *args, **kwargs):
                called["n"] += 1
                return {"execution_id": "exec-never"}

        self._install_fake_transport(monkeypatch, FakeTransport())
        run_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        with pytest.raises(ValueError, match="mutually exclusive"):
            await workflows.execute("wf", scheduled_at=run_at, delay_seconds=60)
        with pytest.raises(ValueError, match="timezone"):
            await workflows.execute(
                "wf", scheduled_at=datetime.now() + timedelta(minutes=5)
            )
        assert called["n"] == 0

    async def test_execute_missing_execution_id_raises(self, monkeypatch):
        from bifrost.workflows import workflows

        class FakeTransport:
            async def call_workflows_execute(self, *args, **kwargs):
                return {"status": ExecutionStatus.PENDING.value}

        self._install_fake_transport(monkeypatch, FakeTransport())
        with pytest.raises(RuntimeError, match="no execution_id"):
            await workflows.execute("wf")

    async def test_cancel_uses_local_and_returns_none(self, monkeypatch):
        from bifrost.workflows import workflows

        seen = {}

        class FakeTransport:
            async def call_workflows_cancel(self, execution_id):
                seen["execution_id"] = execution_id
                return {"execution_id": execution_id, "status": "Cancelled"}

        self._install_fake_transport(monkeypatch, FakeTransport())
        assert await workflows.cancel("exec-local-1") is None
        assert seen["execution_id"] == "exec-local-1"

    async def test_local_errors_preserve_http_statuses(self, monkeypatch):
        from bifrost.client import BifrostAPIError
        from bifrost._local_transport import raise_for_local_status
        from bifrost.workflows import workflows

        for op, status in (
            ("execute", 404),
            ("execute", 403),
            ("cancel", 404),
            ("cancel", 403),
            ("cancel", 409),
        ):

            class FakeTransport:
                async def call_workflows_execute(self, *args, **kwargs):
                    raise_for_local_status(status, "denied", OP_WORKFLOWS_EXECUTE)

                async def call_workflows_cancel(self, execution_id):
                    raise_for_local_status(status, "denied", OP_WORKFLOWS_CANCEL)

            self._install_fake_transport(monkeypatch, FakeTransport())
            with pytest.raises(BifrostAPIError) as exc_info:
                if op == "execute":
                    await workflows.execute("wf")
                else:
                    await workflows.cancel(str(uuid4()))
            assert exc_info.value.response.status_code == status

    async def test_external_caller_keeps_http(self, monkeypatch):
        import bifrost._local_transport as local_transport
        from bifrost.workflows import workflows

        monkeypatch.setattr(local_transport, "_installed", None)
        fake = MagicMock()
        fake.post = AsyncMock(
            return_value=_mock_post_response({"execution_id": "e-http"})
        )
        monkeypatch.setattr(
            sys.modules["bifrost.workflows"], "get_client", lambda: fake
        )

        eid = await workflows.execute("wf", {"x": 1})
        assert eid == "e-http"
        payload = fake.post.await_args.kwargs["json"]
        assert payload["workflow_id"] == "wf"
        assert payload["input_data"] == {"x": 1}
        assert payload["sync"] is False

        fake.post = AsyncMock(return_value=_mock_post_response({"status": "Cancelled"}))
        assert await workflows.cancel("exec-1") is None
        url = fake.post.await_args.args[0]
        assert url == "/api/workflows/executions/exec-1/cancel"


def _mock_post_response(json_body: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = lambda: json_body
    resp.headers = {}
    return resp
