"""``GET /api/executions/{id}/result`` and ``/logs`` read access.

The caller reads their own executions: the ones they acted in, and the root
runs they started as someone else (Run As). A child of such a run is not
theirs; anyone else's execution stays refused. DB-backed via ``db_session``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.routers.executions import ExecutionReadAdapter


def _caller(user) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        name="Contoso User",
        is_superuser=False,
        is_verified=True,
    )


async def _seed_user(db_session, org_id):
    from src.models import User as UserORM

    row = UserORM(
        email=f"contoso-{uuid4().hex[:8]}@example.com",
        name="Contoso User",
        organization_id=org_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_execution(db_session, name, *, executed_by, started_by_user_id, org_id):
    from src.models.orm.executions import Execution as ExecutionModel

    row = ExecutionModel(
        workflow_name=name,
        status=ExecutionStatus.SUCCESS,
        parameters={},
        result={"answer": name},
        result_type="json",
        executed_by=executed_by,
        executed_by_name="Contoso User",
        started_by_user_id=started_by_user_id,
        organization_id=org_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def run_as_tree(db_session):
    """An initiator's root run acting as a colleague, its child, and the colleague's own run."""
    from src.models.orm.organizations import Organization as OrganizationModel

    org = OrganizationModel(name=f"Contoso {uuid4().hex[:8]}", is_active=True, created_by="contoso-test")
    db_session.add(org)
    await db_session.flush()
    initiator = await _seed_user(db_session, org.id)
    colleague = await _seed_user(db_session, org.id)
    stranger = await _seed_user(db_session, org.id)
    root = await _seed_execution(
        db_session, "contoso-root", executed_by=colleague.id, started_by_user_id=initiator.id, org_id=org.id
    )
    root.root_execution_id = root.id
    child = await _seed_execution(
        db_session, "contoso-child", executed_by=colleague.id, started_by_user_id=initiator.id, org_id=org.id
    )
    child.root_execution_id = root.id
    plain = await _seed_execution(
        db_session, "contoso-plain", executed_by=colleague.id, started_by_user_id=colleague.id, org_id=org.id
    )
    plain.root_execution_id = plain.id
    await db_session.flush()
    return {
        "initiator": initiator,
        "colleague": colleague,
        "stranger": stranger,
        "root": root,
        "child": child,
        "plain": plain,
    }


@pytest.mark.asyncio
class TestExecutionResult:
    async def test_initiator_reads_the_root_run_they_started_as_someone_else(self, db_session, run_as_tree):
        repo = ExecutionReadAdapter(db_session)

        body, error = await repo.get_execution_result(run_as_tree["root"].id, _caller(run_as_tree["initiator"]))

        assert error is None
        assert body == {"result": {"answer": "contoso-root"}, "result_type": "json"}

    async def test_a_child_of_the_initiators_run_stays_refused(self, db_session, run_as_tree):
        repo = ExecutionReadAdapter(db_session)

        body, error = await repo.get_execution_result(run_as_tree["child"].id, _caller(run_as_tree["initiator"]))

        assert (body, error) == (None, "Forbidden")

    async def test_an_unrelated_caller_stays_refused(self, db_session, run_as_tree):
        repo = ExecutionReadAdapter(db_session)

        body, error = await repo.get_execution_result(run_as_tree["root"].id, _caller(run_as_tree["stranger"]))

        assert (body, error) == (None, "Forbidden")

    async def test_the_acting_user_reads_their_runs_as_before(self, db_session, run_as_tree):
        repo = ExecutionReadAdapter(db_session)
        colleague = _caller(run_as_tree["colleague"])

        plain_body, plain_error = await repo.get_execution_result(run_as_tree["plain"].id, colleague)
        child_body, child_error = await repo.get_execution_result(run_as_tree["child"].id, colleague)

        assert plain_error is None
        assert plain_body == {"result": {"answer": "contoso-plain"}, "result_type": "json"}
        assert child_error is None
        assert child_body == {"result": {"answer": "contoso-child"}, "result_type": "json"}


@pytest.mark.asyncio
class TestExecutionLogs:
    async def test_initiator_reads_the_root_run_they_started_as_someone_else(self, db_session, run_as_tree):
        repo = ExecutionReadAdapter(db_session)

        logs, error = await repo.get_execution_logs(run_as_tree["root"].id, _caller(run_as_tree["initiator"]))

        assert (logs, error) == ([], None)

    async def test_a_child_of_the_initiators_run_stays_refused(self, db_session, run_as_tree):
        repo = ExecutionReadAdapter(db_session)

        logs, error = await repo.get_execution_logs(run_as_tree["child"].id, _caller(run_as_tree["initiator"]))

        assert (logs, error) == (None, "Forbidden")

    async def test_an_unrelated_caller_stays_refused(self, db_session, run_as_tree):
        repo = ExecutionReadAdapter(db_session)

        logs, error = await repo.get_execution_logs(run_as_tree["root"].id, _caller(run_as_tree["stranger"]))

        assert (logs, error) == (None, "Forbidden")

    async def test_the_acting_user_reads_their_runs_as_before(self, db_session, run_as_tree):
        repo = ExecutionReadAdapter(db_session)
        colleague = _caller(run_as_tree["colleague"])

        plain = await repo.get_execution_logs(run_as_tree["plain"].id, colleague)
        child = await repo.get_execution_logs(run_as_tree["child"].id, colleague)

        assert plain == ([], None)
        assert child == ([], None)


@pytest.mark.asyncio
async def test_pending_cancellation_commits_before_publishing_queue_snapshot(
    db_session, async_session_factory, monkeypatch
) -> None:
    """A pending cancellation is durable before its canonical queue snapshot."""
    from unittest.mock import AsyncMock

    from src.models.orm.executions import Execution as ExecutionModel
    from src.models.orm.work_dispatches import WorkDispatch
    from src.routers import executions as executions_router
    from src.services.execution.queue_tracker import get_queued_executions
    from src.services.authorization.enforce import Caller
    from src.services.authorization.execution_access import ExecutionReadAccess

    principal = UserPrincipal(
        user_id=uuid4(),
        email="queue-canceller@example.com",
        organization_id=None,
        is_superuser=True,
    )
    execution_id = uuid4()
    execution = ExecutionModel(
        id=execution_id,
        workflow_name="queue-cancellation",
        status=ExecutionStatus.PENDING,
        executed_by_name="Queue Canceller",
    )
    db_session.add(execution)
    await db_session.flush()
    db_session.add(
        WorkDispatch(
            family="workflow_execution",
            subject_id=execution_id,
            handler="workflow.execute",
            handler_version=1,
            message={},
            payload={},
            memory_request_bytes=1,
        )
    )
    await db_session.commit()

    try:
        async def snapshot() -> None:
            async with async_session_factory() as observer:
                persisted = await observer.get(ExecutionModel, execution_id)
                assert persisted is not None
                assert persisted.status is ExecutionStatus.CANCELLED
                assert await get_queued_executions(observer) == []

        monkeypatch.setattr(
            executions_router,
            "load_caller",
            AsyncMock(return_value=Caller(principal, None)),
        )
        monkeypatch.setattr(executions_router, "execution_cancel_allowed", lambda *_: True)
        monkeypatch.setattr(
            executions_router,
            "execution_read_access",
            lambda *_: ExecutionReadAccess(visible=True, diagnostics=False),
        )
        monkeypatch.setattr(
            "src.services.execution.queue_tracker.publish_queue_positions",
            snapshot,
        )
        monkeypatch.setattr(executions_router, "publish_execution_update", AsyncMock())
        monkeypatch.setattr(executions_router, "publish_history_update", AsyncMock())

        cancelled, error = await ExecutionReadAdapter(db_session).cancel_execution(execution_id, principal)

        assert error is None
        assert cancelled is not None
        assert cancelled.status == ExecutionStatus.CANCELLED.value
    finally:
        # Cancellation commits by design; a session rollback cannot remove its
        # fixtures. Release locks before cleaning the committed rows explicitly.
        await db_session.rollback()
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                WorkDispatch.__table__.delete().where(WorkDispatch.subject_id == execution_id)
            )
            await cleanup.execute(
                ExecutionModel.__table__.delete().where(ExecutionModel.id == execution_id)
            )
            await cleanup.commit()
