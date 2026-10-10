"""Focused unit tests for the SDK workflow-mutations shared service.

Covers ``shared.sdk_workflow_execution`` — the single implementation behind
``workflows.execute()`` (``POST /api/workflows/execute``) and
``workflows.cancel()`` (``POST /api/workflows/executions/{id}/cancel``):

- auth decisions: inline-code admin gate, org_id admin gate, run_as
  decided by ``authorize_run_as``, Solution inbound denial, cross-org and
  role denials,
- execution-org priority (explicit override > workflow org > caller org),
- scheduled insert (DB row, no queue dispatch),
- dispatch forms: inline code, data-provider cache hit, data-provider
  sync dispatch, normal queue dispatch, transient/sync variants,
- error mapping: 404/400/500 from the execution service,
- publish ordering (execution update before history update) and the
  transient skip,
- cancel 404/403/409 precedence and the status-guarded UPDATE,
- router thin-boundary delegation for both handlers.

DB-backed via the ``db_session`` fixture. Queue/Redis/WebSocket side
effects are patched at their source modules (the service imports them
lazily, so source-level patches apply). Tests that commit (scheduled
insert, cancel) clean up their rows explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.sdk_workflow_execution import (
    SdkWorkflowExecutionError,
    cancel_scheduled_sdk_execution,
    execute_sdk_workflow,
    is_uuid_workflow_ref,
    should_publish_request_execution_update,
)
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.services.authorization.explain import RunAsTarget
from src.services.authorization.impersonation import DENIED_MESSAGE, RunAsError


@pytest_asyncio.fixture
async def db_session(async_engine):
    """Keep service commits visible in each test without persisting fixtures."""
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


def _admin(org_id=None, **kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=kwargs.get("user_id", uuid4()),
        email=kwargs.get("email", "sdk-wfexec-admin@test.local"),
        organization_id=org_id,
        name="SDK WfExec Admin",
        is_superuser=True,
        is_verified=True,
    )


def _user(org_id, **kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=kwargs.get("user_id", uuid4()),
        email=kwargs.get("email", "sdk-wfexec-user@test.local"),
        organization_id=org_id,
        name="SDK WfExec User",
        is_superuser=False,
        is_verified=True,
    )


def _request(**kwargs):
    from src.models import WorkflowExecutionRequest

    return WorkflowExecutionRequest(**kwargs)


async def _seed_org(db_session):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-wfexec-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="sdk-wfexec-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_user(db_session, *, org_id=None, is_superuser=False):
    from src.models import User as UserORM

    row = UserORM(
        email=f"sdk-wfexec-{uuid4().hex[:8]}@example.com",
        name="SDK WfExec",
        is_superuser=is_superuser,
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


async def _seed_execution(db_session, name, *, user_id, org_id=None, **kwargs):
    from src.models.orm.executions import Execution as ExecutionModel

    row = ExecutionModel(
        workflow_name=name,
        workflow_id=kwargs.get("workflow_id"),
        status=kwargs.get("status", ExecutionStatus.SCHEDULED),
        parameters={},
        executed_by=user_id,
        executed_by_name="Seed User",
        organization_id=org_id,
        scheduled_at=kwargs.get("scheduled_at"),
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


def _patch_dispatch(run_result=None, **kwargs):
    """Patch queue/cache/publish seams at their source modules."""
    run_workflow = AsyncMock(return_value=run_result or _pending_response())
    run_code = AsyncMock(return_value=run_result or _pending_response())
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
        patch("src.services.execution.service.run_code", run_code),
        patch(
            "src.services.execution.service.get_workflow_for_execution", get_meta
        ),
        patch("src.core.pubsub.publish_execution_update", AsyncMock()),
        patch("src.core.pubsub.publish_history_update", AsyncMock()),
        patch("src.core.cache.get_cached_data_provider", AsyncMock(return_value=None)),
    )


def _start_patches(patches):
    mocks = [p.start() for p in patches]
    return mocks


def _stop_patches(patches):
    for p in patches:
        p.stop()


def _run_as_target(user) -> RunAsTarget:
    return RunAsTarget(
        user_id=user.id,
        organization_id=user.organization_id,
        is_active=True,
        is_system=False,
        identity_kind=None,
        is_superuser=user.is_superuser,
        is_external=False,
        is_provider_org=False,
        email=user.email,
        name=user.name,
        privileged=False,
    )


def _patch_authorize_run_as(*, returns: RunAsTarget | None = None, raises: RunAsError | None = None):
    """Stand in for ``authorize_run_as`` with its real signature, recording each call."""
    calls: list[tuple[UserPrincipal, UUID]] = []

    async def authorize_run_as(
        db: AsyncSession, principal: UserPrincipal, run_as_user_id: UUID
    ) -> RunAsTarget | None:
        calls.append((principal, run_as_user_id))
        if raises is not None:
            raise raises
        return returns

    return patch("src.services.authorization.impersonation.authorize_run_as", authorize_run_as), calls


@pytest.mark.asyncio
class TestExecuteAuthDecisions:
    async def test_inline_code_requires_admin(self, db_session):
        org = await _seed_org(db_session)
        principal = _user(org.id)
        req = _request(code="Y29kZQ==", input_data={})
        with pytest.raises(SdkWorkflowExecutionError) as exc_info:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        assert exc_info.value.status_code == 403

    async def test_missing_workflow_and_code_is_400(self, db_session):
        from src.models import WorkflowExecutionRequest

        org = await _seed_org(db_session)
        principal = _admin(org.id)
        req = WorkflowExecutionRequest.model_construct(
            workflow_id=None, code=None, input_data={}
        )
        with pytest.raises(SdkWorkflowExecutionError) as exc_info:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        assert exc_info.value.status_code == 400

    async def test_org_id_override_requires_admin(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _user(org.id)
        req = _request(workflow_id=str(wf.id), input_data={}, org_id=str(org.id))
        with pytest.raises(SdkWorkflowExecutionError) as exc_info:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        assert exc_info.value.status_code == 403
        assert "org_id and run_as" in exc_info.value.detail

    async def test_unknown_workflow_is_404_with_scope_detail(self, db_session):
        org = await _seed_org(db_session)
        principal = _admin(org.id)
        missing = str(uuid4())
        req = _request(workflow_id=missing, input_data={})
        with pytest.raises(SdkWorkflowExecutionError) as exc_info:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["workflow_ref"] == missing
        assert exc_info.value.detail["derived_solution_scope"] is None

    async def test_solution_inbound_denied_is_404(self, db_session):
        org = await _seed_org(db_session)
        from src.models.orm.solutions import Solution

        stem = f"sealed-{uuid4().hex[:8]}"
        path = f"workflows/{stem}.py"
        # A loose workflow would resolve if the sealed target were allowed to
        # fall back to the shared namespace.
        await _seed_workflow(
            db_session,
            stem,
            path=path,
            function_name="main",
        )
        sealed_solution = Solution(
            id=uuid4(),
            slug=f"sealed-{uuid4().hex[:8]}",
            name="Sealed SDK workflow test",
            organization_id=org.id,
            allow_inbound_access=False,
        )
        db_session.add(sealed_solution)
        await db_session.flush()

        principal = _admin(org.id)
        req = _request(
            workflow_id=f"{path}::main",
            input_data={},
            solution_id=str(sealed_solution.id),
        )

        with pytest.raises(SdkWorkflowExecutionError) as exc_info:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == {
            "message": f"Workflow '{path}::main' not found"
        }

    async def test_cross_org_uuid_ref_is_404(self, db_session):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}", org_id=org_a.id)
        principal = _user(org_b.id)
        req = _request(workflow_id=str(wf.id), input_data={})
        with pytest.raises(SdkWorkflowExecutionError) as exc_info:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org_b.id
            )
        assert exc_info.value.status_code == 404

    async def test_portable_ref_without_role_is_403(self, db_session):
        org = await _seed_org(db_session)
        stem = f"gated-{uuid4().hex[:8]}"
        await _seed_workflow(
            db_session,
            stem,
            org_id=org.id,
            access_level="role_based",
            path=f"workflows/{stem}.py",
            function_name="gated_fn",
        )
        principal = _user(org.id)
        # Path refs resolve without role checks; the explicit can_access
        # assertion then denies with 403.
        req = _request(workflow_id=f"workflows/{stem}.py::gated_fn", input_data={})
        with pytest.raises(SdkWorkflowExecutionError) as exc_info:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
class TestExecuteRunAs:
    async def _dispatched_context(self, db_session, principal, **request):
        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            await execute_sdk_workflow(
                db_session, principal, _request(input_data={}, **request), caller_org_id=principal.organization_id
            )
        finally:
            _stop_patches(patches)
        return mocks[0].call_args[1]

    @pytest.mark.parametrize(
        "error",
        [
            RunAsError(403, DENIED_MESSAGE),
            RunAsError(404, "Run As user 'someone' not found"),
            RunAsError(400, "Run As user 'someone' is inactive"),
        ],
        ids=["refused", "unknown", "inactive"],
    )
    async def test_a_refused_run_as_keeps_the_status_and_message(self, db_session, error):
        org = await _seed_org(db_session)
        other = await _seed_user(db_session, org_id=org.id)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _user(org.id)
        authorize, calls = _patch_authorize_run_as(raises=error)

        with authorize, pytest.raises(SdkWorkflowExecutionError) as exc_info:
            await execute_sdk_workflow(
                db_session,
                principal,
                _request(workflow_id=str(wf.id), input_data={}, run_as=str(other.id)),
                caller_org_id=org.id,
            )

        assert (exc_info.value.status_code, exc_info.value.detail) == (error.status_code, error.detail)
        assert calls == [(principal, other.id)]

    async def test_an_allowed_run_as_acts_as_the_user_and_keeps_the_caller_as_run_user(self, db_session):
        from shared.run_lineage import person_lineage

        org = await _seed_org(db_session)
        target = await _seed_user(db_session, org_id=org.id)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _user(org.id)
        authorize, calls = _patch_authorize_run_as(returns=_run_as_target(target))

        with authorize:
            dispatched = await self._dispatched_context(
                db_session, principal, workflow_id=str(wf.id), run_as=str(target.id)
            )

        acting = dispatched["context"]
        expected_lineage = person_lineage(principal.user_id)
        expected = (str(target.id), target.email, target.name, False)
        assert (acting.user_id, acting.email, acting.name, acting.is_platform_admin) == expected
        assert dispatched["lineage"] == expected_lineage
        assert calls == [(principal, target.id)]

    async def test_a_workflow_naming_its_run_user_acts_as_the_run_user(self, db_session):
        from src.core.constants import SYSTEM_USER_ID

        org = await _seed_org(db_session)
        person = await _seed_user(db_session, org_id=org.id)
        parent = await _seed_execution(db_session, "parent", user_id=person.id, org_id=org.id)
        parent.run_user_id = person.id
        parent.started_by_user_id = person.id
        await db_session.flush()
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        engine = UserPrincipal(
            user_id=UUID(SYSTEM_USER_ID),
            email="engine@internal",
            organization_id=org.id,
            is_superuser=True,
            engine_execution_id=str(parent.id),
            run_user_id=person.id,
        )

        dispatched = await self._dispatched_context(
            db_session, engine, workflow_id=str(wf.id), run_as=str(person.id)
        )

        acting = dispatched["context"]
        expected = (str(person.id), person.email, False)
        assert (acting.user_id, acting.email, acting.is_platform_admin) == expected

    async def test_without_run_as_the_caller_acts_and_nothing_is_authorized(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _user(org.id)
        authorize, calls = _patch_authorize_run_as()

        with authorize:
            dispatched = await self._dispatched_context(db_session, principal, workflow_id=str(wf.id))

        acting = dispatched["context"]
        expected = (str(principal.user_id), principal.email, False)
        assert (acting.user_id, acting.email, acting.is_platform_admin) == expected
        assert calls == []


@pytest.mark.asyncio
class TestExecuteScheduling:
    async def test_delay_seconds_inserts_scheduled_row_without_dispatch(
        self, db_session
    ):
        from src.models.orm.executions import Execution as ExecutionModel

        org = await _seed_org(db_session)
        admin_row = await _seed_user(db_session, org_id=org.id, is_superuser=True)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _admin(org.id, user_id=admin_row.id)
        req = _request(workflow_id=str(wf.id), input_data={"a": 1}, delay_seconds=300)

        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            before = datetime.now(timezone.utc)
            result = await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        finally:
            _stop_patches(patches)

        assert result.status == ExecutionStatus.SCHEDULED
        assert result.workflow_id == str(wf.id)
        assert result.scheduled_at is not None
        assert result.scheduled_at >= before
        mocks[0].assert_not_awaited()  # run_workflow
        mocks[2].assert_not_awaited()  # get_workflow_for_execution

        row = (
            await db_session.execute(
                select(ExecutionModel).where(
                    ExecutionModel.id == UUID(result.execution_id)
                )
            )
        ).scalar_one()
        assert row.status == ExecutionStatus.SCHEDULED
        assert row.parameters == {"a": 1}
        assert row.organization_id == org.id
        assert row.executed_by == principal.user_id
        assert (row.run_user_id, row.started_by_user_id, row.root_execution_id) == (
            admin_row.id,
            admin_row.id,
            row.id,
        )

        await db_session.execute(
            delete(ExecutionModel).where(ExecutionModel.id == row.id)
        )
        await db_session.commit()

    async def test_run_as_applies_to_scheduled_row(self, db_session):
        from src.models.orm.executions import Execution as ExecutionModel

        org = await _seed_org(db_session)
        target = await _seed_user(db_session, org_id=org.id)
        admin_row = await _seed_user(db_session, org_id=org.id, is_superuser=True)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _admin(org.id, user_id=admin_row.id)
        req = _request(
            workflow_id=str(wf.id),
            input_data={},
            delay_seconds=300,
            run_as=str(target.id),
        )
        authorize, _calls = _patch_authorize_run_as(returns=_run_as_target(target))
        with authorize:
            result = await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        assert result.status == ExecutionStatus.SCHEDULED
        row = (
            await db_session.execute(
                select(ExecutionModel).where(
                    ExecutionModel.id == UUID(result.execution_id)
                )
            )
        ).scalar_one()
        assert row.executed_by == target.id
        # The run is for the admin who called; run_as changes the acting user only.
        assert (row.run_user_id, row.started_by_user_id, row.root_execution_id) == (
            admin_row.id,
            admin_row.id,
            row.id,
        )

        await db_session.execute(
            delete(ExecutionModel).where(ExecutionModel.id == row.id)
        )
        await db_session.commit()


@pytest.mark.asyncio
class TestExecuteLineage:
    async def _dispatched_lineage(self, db_session, principal, **request):
        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            await execute_sdk_workflow(
                db_session, principal, _request(**request), caller_org_id=principal.organization_id
            )
        finally:
            _stop_patches(patches)
        run_workflow, run_code = mocks[0], mocks[1]
        call = run_workflow.await_args or run_code.await_args
        return call.kwargs["lineage"]

    async def test_person_run_is_for_the_person(self, db_session):
        from shared.run_lineage import person_lineage

        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _admin(org.id)

        lineage = await self._dispatched_lineage(db_session, principal, workflow_id=str(wf.id))

        assert lineage == person_lineage(principal.user_id)

    async def test_child_run_copies_the_parent_lineage(self, db_session):
        from shared.run_lineage import RunLineage
        from src.core.constants import SYSTEM_USER_ID

        org = await _seed_org(db_session)
        person = await _seed_user(db_session, org_id=org.id)
        root = uuid4()
        parent = await _seed_execution(db_session, "parent", user_id=person.id, org_id=org.id)
        parent.run_user_id = person.id
        parent.started_by_user_id = person.id
        parent.root_execution_id = root
        await db_session.flush()
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        engine = UserPrincipal(
            user_id=UUID(SYSTEM_USER_ID),
            email="engine@internal",
            organization_id=org.id,
            is_superuser=True,
            engine_execution_id=str(parent.id),
        )

        lineage = await self._dispatched_lineage(db_session, engine, workflow_id=str(wf.id))

        assert lineage == RunLineage(person.id, person.id, root)

    async def test_inline_code_run_is_for_the_person(self, db_session):
        from shared.run_lineage import person_lineage

        org = await _seed_org(db_session)
        principal = _admin(org.id)

        lineage = await self._dispatched_lineage(db_session, principal, code="print(1)")

        assert lineage == person_lineage(principal.user_id)


@pytest.mark.asyncio
class TestExecuteDispatch:
    async def test_normal_enqueue_calls_run_workflow(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _admin(org.id)
        req = _request(workflow_id=str(wf.id), input_data={"x": 1})

        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            result = await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        finally:
            _stop_patches(patches)

        run_workflow, _, get_meta, pub_exec, pub_hist, _ = mocks
        assert result.status == ExecutionStatus.PENDING
        get_meta.assert_awaited_once_with(str(wf.id), db=db_session)
        run_workflow.assert_awaited_once()
        assert run_workflow.call_args[1]["workflow_id"] == str(wf.id)
        assert run_workflow.call_args[1]["sync"] is False
        pub_exec.assert_awaited_once()
        pub_hist.assert_awaited_once()

    async def test_publish_order_is_execution_then_history(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _admin(org.id)
        req = _request(workflow_id=str(wf.id), input_data={})

        order: list[str] = []
        run_workflow = AsyncMock(return_value=_pending_response())
        pub_exec = AsyncMock(side_effect=lambda **kw: order.append("execution"))
        pub_hist = AsyncMock(side_effect=lambda **kw: order.append("history"))
        patches = [
            patch("src.services.execution.service.run_workflow", run_workflow),
            patch(
                "src.services.execution.service.get_workflow_for_execution",
                AsyncMock(return_value={"name": "wf", "timeout_seconds": 60}),
            ),
            patch("src.core.pubsub.publish_execution_update", pub_exec),
            patch("src.core.pubsub.publish_history_update", pub_hist),
            patch(
                "src.core.cache.get_cached_data_provider",
                AsyncMock(return_value=None),
            ),
        ]
        _start_patches(patches)
        try:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        finally:
            _stop_patches(patches)
        assert order == ["execution", "history"]

    async def test_transient_skips_publish(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _admin(org.id)
        req = _request(workflow_id=str(wf.id), input_data={}, transient=True)

        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        finally:
            _stop_patches(patches)

        mocks[3].assert_not_awaited()
        mocks[4].assert_not_awaited()

    async def test_terminal_sync_result_marks_transient(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _admin(org.id)
        req = _request(workflow_id=str(wf.id), input_data={}, sync=True)

        patches = _patch_dispatch(
            run_result=_pending_response(status=ExecutionStatus.SUCCESS)
        )
        _start_patches(patches)
        try:
            result = await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        finally:
            _stop_patches(patches)
        assert result.is_transient is True

    async def test_inline_code_dispatches_to_run_code(self, db_session):
        org = await _seed_org(db_session)
        principal = _admin(org.id)
        req = _request(code="Y29kZQ==", input_data={}, script_name="probe")

        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            result = await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        finally:
            _stop_patches(patches)

        run_workflow, run_code, _, _, _, _ = mocks
        run_code.assert_awaited_once()
        assert run_code.call_args[1]["script_name"] == "probe"
        run_workflow.assert_not_awaited()
        assert result.status == ExecutionStatus.PENDING

    async def test_data_provider_cache_hit_returns_inline(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(
            db_session,
            f"dp-{uuid4().hex[:8]}",
            type="data_provider",
            cache_ttl_seconds=300,
        )
        principal = _admin(org.id)
        req = _request(workflow_id=str(wf.id), input_data={}, transient=True)

        run_workflow = AsyncMock(return_value=_pending_response())
        cached = AsyncMock(return_value={"data": [{"id": 1}]})
        patches = [
            patch("src.services.execution.service.run_workflow", run_workflow),
            patch(
                "src.services.execution.service.get_workflow_for_execution",
                AsyncMock(),
            ),
            patch("src.core.cache.get_cached_data_provider", cached),
        ]
        _start_patches(patches)
        try:
            result = await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        finally:
            _stop_patches(patches)

        assert result.status == ExecutionStatus.SUCCESS
        assert result.result == [{"id": 1}]
        assert result.is_transient is True
        run_workflow.assert_not_awaited()

    async def test_data_provider_non_transient_dispatches(self, db_session):
        org = await _seed_org(db_session)
        wf = await _seed_workflow(
            db_session,
            f"dp-{uuid4().hex[:8]}",
            type="data_provider",
            cache_ttl_seconds=300,
        )
        principal = _admin(org.id)
        req = _request(workflow_id=str(wf.id), input_data={}, transient=False)

        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            result = await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=org.id
            )
        finally:
            _stop_patches(patches)

        mocks[0].assert_awaited_once()
        assert mocks[0].call_args[1]["sync"] is True
        assert result.is_transient is False

    async def test_execution_error_mapping(self, db_session):
        from src.services.execution.service import (
            WorkflowLoadError,
            WorkflowNotFoundError,
        )

        org = await _seed_org(db_session)
        wf = await _seed_workflow(db_session, f"wf-{uuid4().hex[:8]}")
        principal = _admin(org.id)
        req = _request(workflow_id=str(wf.id), input_data={})

        cases = [
            (WorkflowNotFoundError("gone"), 404),
            (WorkflowLoadError("broken"), 500),
            (ValueError("is a service"), 400),
            (RuntimeError("boom"), 500),
        ]
        for error, status_code in cases:
            patches = _patch_dispatch()
            _start_patches(patches)
            try:
                # Stop this nested patch before stopping the outer patches;
                # otherwise its exit restores the outer AsyncMock globally.
                with patch(
                    "src.services.execution.service.run_workflow",
                    new=AsyncMock(side_effect=error),
                ):
                    with pytest.raises(SdkWorkflowExecutionError) as exc_info:
                        await execute_sdk_workflow(
                            db_session, principal, req, caller_org_id=org.id
                        )
            finally:
                _stop_patches(patches)
            assert exc_info.value.status_code == status_code
            if isinstance(error, RuntimeError):
                assert "Failed to execute workflow" in exc_info.value.detail

    async def test_org_scoped_workflow_pins_execution_org(self, db_session):
        wf_org = await _seed_org(db_session)
        caller_org = await _seed_org(db_session)
        wf = await _seed_workflow(
            db_session, f"wf-{uuid4().hex[:8]}", org_id=wf_org.id
        )
        principal = _admin(caller_org.id)
        req = _request(workflow_id=str(wf.id), input_data={})

        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=caller_org.id
            )
        finally:
            _stop_patches(patches)

        shared_ctx = mocks[0].call_args[1]["context"]
        assert shared_ctx.org_id == str(wf_org.id)

    async def test_explicit_org_id_override_wins(self, db_session):
        wf_org = await _seed_org(db_session)
        target_org = await _seed_org(db_session)
        admin = await _seed_user(db_session, is_superuser=True)
        principal = _admin(org_id=None, user_id=admin.id)
        wf = await _seed_workflow(
            db_session, f"wf-{uuid4().hex[:8]}", org_id=wf_org.id
        )
        req = _request(
            workflow_id=str(wf.id), input_data={}, org_id=str(target_org.id)
        )

        patches = _patch_dispatch()
        mocks = _start_patches(patches)
        try:
            await execute_sdk_workflow(
                db_session, principal, req, caller_org_id=None
            )
        finally:
            _stop_patches(patches)

        shared_ctx = mocks[0].call_args[1]["context"]
        assert shared_ctx.org_id == str(target_org.id)


@pytest.mark.asyncio
class TestCancelScheduled:
    async def test_missing_row_is_404(self, db_session):
        principal = _admin()
        with pytest.raises(SdkWorkflowExecutionError) as exc_info:
            await cancel_scheduled_sdk_execution(
                db_session, principal, uuid4(), caller_org_id=None
            )
        assert exc_info.value.status_code == 404

    async def test_cross_org_non_admin_is_403(self, db_session):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        submitter = await _seed_user(db_session, org_id=org_a.id)
        row = await _seed_execution(
            db_session, "wf", user_id=submitter.id, org_id=org_a.id
        )
        await db_session.commit()
        try:
            principal = _user(org_b.id)
            with pytest.raises(SdkWorkflowExecutionError) as exc_info:
                await cancel_scheduled_sdk_execution(
                    db_session, principal, row.id, caller_org_id=org_b.id
                )
            assert exc_info.value.status_code == 403
        finally:
            await db_session.execute(
                delete(row.__class__).where(row.__class__.id == row.id)
            )
            await db_session.commit()

    async def test_non_submitter_non_admin_is_403(self, db_session):
        org = await _seed_org(db_session)
        submitter = await _seed_user(db_session, org_id=org.id)
        other = await _seed_user(db_session, org_id=org.id)
        row = await _seed_execution(
            db_session, "wf", user_id=submitter.id, org_id=org.id
        )
        await db_session.commit()
        try:
            principal = _user(org.id, user_id=other.id)
            with pytest.raises(SdkWorkflowExecutionError) as exc_info:
                await cancel_scheduled_sdk_execution(
                    db_session, principal, row.id, caller_org_id=org.id
                )
            assert exc_info.value.status_code == 403
        finally:
            await db_session.execute(
                delete(row.__class__).where(row.__class__.id == row.id)
            )
            await db_session.commit()

    async def test_happy_path_flips_to_cancelled(self, db_session):
        from src.models.orm.executions import Execution as ExecutionModel

        org = await _seed_org(db_session)
        submitter = await _seed_user(db_session, org_id=org.id)
        row = await _seed_execution(
            db_session, "wf", user_id=submitter.id, org_id=org.id
        )
        await db_session.commit()
        try:
            principal = _user(org.id, user_id=submitter.id)
            result = await cancel_scheduled_sdk_execution(
                db_session, principal, row.id, caller_org_id=org.id
            )
            assert result == {
                "execution_id": str(row.id),
                "status": ExecutionStatus.CANCELLED.value,
            }
            await db_session.rollback()
            fresh = (
                await db_session.execute(
                    select(ExecutionModel).where(ExecutionModel.id == row.id)
                )
            ).scalar_one()
            assert fresh.status == ExecutionStatus.CANCELLED
            assert fresh.completed_at is not None
        finally:
            await db_session.execute(
                delete(ExecutionModel).where(ExecutionModel.id == row.id)
            )
            await db_session.commit()

    async def test_already_terminal_is_409(self, db_session):
        org = await _seed_org(db_session)
        submitter = await _seed_user(db_session, org_id=org.id)
        row = await _seed_execution(
            db_session,
            "wf",
            user_id=submitter.id,
            org_id=org.id,
            status=ExecutionStatus.CANCELLED,
        )
        await db_session.commit()
        try:
            principal = _user(org.id, user_id=submitter.id)
            with pytest.raises(SdkWorkflowExecutionError) as exc_info:
                await cancel_scheduled_sdk_execution(
                    db_session, principal, row.id, caller_org_id=org.id
                )
            assert exc_info.value.status_code == 409
            assert "Cancelled" in exc_info.value.detail
        finally:
            from src.models.orm.executions import Execution as ExecutionModel

            await db_session.execute(
                delete(ExecutionModel).where(ExecutionModel.id == row.id)
            )
            await db_session.commit()

    async def test_admin_may_cancel_foreign_row(self, db_session):
        from src.models.orm.executions import Execution as ExecutionModel

        org = await _seed_org(db_session)
        submitter = await _seed_user(db_session, org_id=org.id)
        row = await _seed_execution(
            db_session, "wf", user_id=submitter.id, org_id=org.id
        )
        await db_session.commit()
        try:
            principal = _admin()
            result = await cancel_scheduled_sdk_execution(
                db_session, principal, row.id, caller_org_id=None
            )
            assert result["status"] == ExecutionStatus.CANCELLED.value
        finally:
            await db_session.execute(
                delete(ExecutionModel).where(ExecutionModel.id == row.id)
            )
            await db_session.commit()


class TestHelpers:
    def test_is_uuid_workflow_ref(self):
        assert is_uuid_workflow_ref(str(uuid4())) is True
        assert is_uuid_workflow_ref("workflows/x.py::fn") is False
        assert is_uuid_workflow_ref("my-workflow") is False

    def test_should_publish_request_execution_update(self):
        assert should_publish_request_execution_update(ExecutionStatus.PENDING)
        assert should_publish_request_execution_update(ExecutionStatus.RUNNING)
        assert not should_publish_request_execution_update(ExecutionStatus.SUCCESS)
        assert not should_publish_request_execution_update(ExecutionStatus.FAILED)


@pytest.mark.asyncio
class TestRouterBoundaries:
    """Handlers delegate to the shared service and map its errors."""

    async def test_execute_delegates_and_maps_errors(self):
        from src.routers.workflows import execute_workflow

        principal = _admin()
        request = _request(workflow_id=str(uuid4()), input_data={})
        ctx = SimpleNamespace(
            user=principal,
            org_id=None,
            solution_id=None,
            app_id=None,
            caller_solution_id=None,
        )
        sentinel = _pending_response()
        with patch(
            "shared.sdk_workflow_execution.execute_sdk_workflow",
            new=AsyncMock(return_value=sentinel),
        ) as mock_exec:
            result = await execute_workflow(request, ctx, AsyncMock(), principal)
        assert result == sentinel
        mock_exec.assert_awaited_once()
        assert mock_exec.call_args[1]["caller_org_id"] is None

        with patch(
            "shared.sdk_workflow_execution.execute_sdk_workflow",
            new=AsyncMock(
                side_effect=SdkWorkflowExecutionError(404, {"message": "nope"})
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await execute_workflow(request, ctx, AsyncMock(), principal)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == {"message": "nope"}

    async def test_cancel_delegates_and_maps_errors(self):
        from src.routers.workflows import cancel_scheduled_execution

        principal = _admin()
        execution_id = uuid4()
        ctx = SimpleNamespace(user=principal, org_id=None)
        sentinel = {"execution_id": str(execution_id), "status": "Cancelled"}
        with patch(
            "shared.sdk_workflow_execution.cancel_scheduled_sdk_execution",
            new=AsyncMock(return_value=sentinel),
        ) as mock_cancel:
            result = await cancel_scheduled_execution(
                execution_id, ctx, AsyncMock(), principal
            )
        assert result == sentinel
        mock_cancel.assert_awaited_once()
        assert mock_cancel.call_args[1]["caller_org_id"] is None

        for status_code in (404, 403, 409):
            with patch(
                "shared.sdk_workflow_execution.cancel_scheduled_sdk_execution",
                new=AsyncMock(
                    side_effect=SdkWorkflowExecutionError(status_code, "err")
                ),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await cancel_scheduled_execution(
                        execution_id, ctx, AsyncMock(), principal
                    )
            assert exc_info.value.status_code == status_code

    async def test_scheduled_insert_is_shared_with_forms(self):
        """forms.py consumes the same canonical insert (no router import)."""
        import ast
        from pathlib import Path

        import src.routers.forms as forms_module

        tree = ast.parse(Path(forms_module.__file__).read_text())
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        assert not any(
            "src.routers.workflows" in (node.module or "")
            for node in imports
            if isinstance(node, ast.ImportFrom)
        )


def test_shared_service_has_no_transport_imports():
    """The shared service must stay callable without FastAPI/router imports."""
    import ast
    from pathlib import Path

    import shared.sdk_workflow_execution as service

    tree = ast.parse(Path(service.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "fastapi" not in imported
    assert not any("routers" in (m or "") for m in imported)
