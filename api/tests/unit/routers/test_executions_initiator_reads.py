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
from src.routers.executions import ExecutionRepository


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
        repo = ExecutionRepository(db_session)

        body, error = await repo.get_execution_result(run_as_tree["root"].id, _caller(run_as_tree["initiator"]))

        assert error is None
        assert body == {"result": {"answer": "contoso-root"}, "result_type": "json"}

    async def test_a_child_of_the_initiators_run_stays_refused(self, db_session, run_as_tree):
        repo = ExecutionRepository(db_session)

        body, error = await repo.get_execution_result(run_as_tree["child"].id, _caller(run_as_tree["initiator"]))

        assert (body, error) == (None, "Forbidden")

    async def test_an_unrelated_caller_stays_refused(self, db_session, run_as_tree):
        repo = ExecutionRepository(db_session)

        body, error = await repo.get_execution_result(run_as_tree["root"].id, _caller(run_as_tree["stranger"]))

        assert (body, error) == (None, "Forbidden")

    async def test_the_acting_user_reads_their_runs_as_before(self, db_session, run_as_tree):
        repo = ExecutionRepository(db_session)
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
        repo = ExecutionRepository(db_session)

        logs, error = await repo.get_execution_logs(run_as_tree["root"].id, _caller(run_as_tree["initiator"]))

        assert (logs, error) == ([], None)

    async def test_a_child_of_the_initiators_run_stays_refused(self, db_session, run_as_tree):
        repo = ExecutionRepository(db_session)

        logs, error = await repo.get_execution_logs(run_as_tree["child"].id, _caller(run_as_tree["initiator"]))

        assert (logs, error) == (None, "Forbidden")

    async def test_an_unrelated_caller_stays_refused(self, db_session, run_as_tree):
        repo = ExecutionRepository(db_session)

        logs, error = await repo.get_execution_logs(run_as_tree["root"].id, _caller(run_as_tree["stranger"]))

        assert (logs, error) == (None, "Forbidden")

    async def test_the_acting_user_reads_their_runs_as_before(self, db_session, run_as_tree):
        repo = ExecutionRepository(db_session)
        colleague = _caller(run_as_tree["colleague"])

        plain = await repo.get_execution_logs(run_as_tree["plain"].id, colleague)
        child = await repo.get_execution_logs(run_as_tree["child"].id, colleague)

        assert plain == ([], None)
        assert child == ([], None)
