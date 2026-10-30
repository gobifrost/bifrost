"""``legacy_credential_status``: what still depends on legacy runtime credentials."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.services.authorization.legacy_status import legacy_credential_status


async def _execution(session: AsyncSession, user: User, organization: Organization, **fields) -> Execution:
    execution = Execution(
        workflow_name=f"legacy_{uuid4().hex[:6]}", executed_by_name="Legacy", run_user_id=user.id,
        executed_by=user.id, organization_id=organization.id, started_by_user_id=user.id,
        root_execution_id=uuid4(), **fields,
    )
    session.add(execution)
    await session.flush()
    return execution


async def _attempt(session: AsyncSession, user: User, organization: Organization, **fields) -> ServiceAttempt:
    name = f"svc_{uuid4().hex[:8]}"
    workflow = Workflow(name=name, function_name=name, path=f"workflows/{name}.py", organization_id=organization.id)
    session.add(workflow)
    await session.flush()
    definition = ServiceDefinition(workflow_id=workflow.id, organization_id=organization.id, created_by="legacy")
    session.add(definition)
    await session.flush()
    attempt = ServiceAttempt(
        service_id=definition.id, run_user_id=user.id, lease_token="lease",
        **{"lease_expires_at": datetime.now(timezone.utc) + timedelta(minutes=5), "state": "running", **fields},
    )
    session.add(attempt)
    await session.flush()
    return attempt


async def _launcher(session: AsyncSession, **fields) -> ExecutionWorker:
    worker = ExecutionWorker(
        form="static", state="ready", resource_class="standard", slots=1, hostname=f"runner-{uuid4().hex[:6]}",
        **{"remote_owner_lease_expires_at": datetime.now(timezone.utc) + timedelta(seconds=20), **fields},
    )
    session.add(worker)
    await session.flush()
    return worker


async def test_only_live_legacy_runs_and_connected_legacy_launchers_are_listed(db_session: AsyncSession) -> None:
    organization = Organization(name=f"Legacy {uuid4().hex[:8]}", created_by="legacy")
    db_session.add(organization)
    await db_session.flush()
    user = User(email=f"legacy-{uuid4().hex[:8]}@example.test", name="Legacy", organization_id=organization.id)
    db_session.add(user)
    await db_session.flush()

    legacy_run = await _execution(db_session, user, organization, status=ExecutionStatus.RUNNING)
    claims_run = await _execution(db_session, user, organization, status=ExecutionStatus.RUNNING, credential_version=2)
    ended_run = await _execution(db_session, user, organization, status=ExecutionStatus.SUCCESS)
    legacy_attempt = await _attempt(db_session, user, organization)
    claims_attempt = await _attempt(db_session, user, organization, credential_version=2)
    lapsed_attempt = await _attempt(
        db_session, user, organization, lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    unreported = await _launcher(db_session)
    protocol_one = await _launcher(db_session, remote_credential_protocol=1)
    protocol_two = await _launcher(db_session, remote_credential_protocol=2)
    disconnected = await _launcher(
        db_session, remote_owner_lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )

    status = await legacy_credential_status(db_session)

    runs = {(run.kind, run.id) for run in status.live_runs}
    assert {("workflow_execution", legacy_run.id), ("service_attempt", legacy_attempt.id)} <= runs
    listed = {run.id for run in status.live_runs}
    assert not {claims_run.id, ended_run.id, claims_attempt.id, lapsed_attempt.id} & listed
    launchers = {launcher.worker_id: launcher.credential_protocol for launcher in status.launchers}
    assert (launchers.get(unreported.id), launchers.get(protocol_one.id)) == (1, 1)
    assert not {protocol_two.id, disconnected.id} & launchers.keys()
