"""Resolve durable runtime authority from current database facts.

This module deliberately accepts only a typed runtime credential identifier.
Transport claims are attribution, not authority: every invocation re-loads the
run, its user, and any workflow powers from the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from shared.system_account_guard import is_system_account
from src.models.enums import LIVE_EXECUTION_STATUSES
from src.models.orm.agent_runs import LIVE_AGENT_RUN_STATUSES, AgentRun
from src.models.orm.agents import Agent
from src.models.orm.executions import Execution
from src.models.orm.services import _SERVICE_LIVE_STATES, ServiceAttempt, ServiceDefinition
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.authorization.explain import (
    Powers,
    RunUser,
    load_powers_for_workflow,
    load_run_user,
)

RuntimeKind = Literal["workflow_execution", "service_attempt", "agent_run"]


class RuntimeIdentityError(ValueError):
    """The durable record cannot safely establish runtime authority."""


_EXECUTION_NOT_FOUND = "Workflow execution was not found."
_EXECUTION_NOT_LIVE = "Workflow execution is not live."
_EXECUTION_RUN_USER_MISSING = "Workflow execution has no current run user."
_EXECUTION_LINEAGE_MISSING = "Workflow execution has incomplete lineage."
_EXECUTION_ACTOR_MISSING = "Workflow execution has no valid acting user."
_WORKFLOW_MISSING = "Workflow runtime references a missing workflow."
_SERVICE_ID_MISSING = "Service runtime requires a service ID."
_SERVICE_ATTEMPT_NOT_FOUND = "Service attempt was not found."
_SERVICE_ATTEMPT_SERVICE_MISMATCH = "Service attempt does not belong to this service."
_SERVICE_NOT_FOUND = "Service definition was not found."
_SERVICE_ATTEMPT_NOT_LIVE = "Service attempt is not live."
_SERVICE_ATTEMPT_LEASE_EXPIRED = "Service attempt lease has expired."
_SERVICE_RUN_USER_MISSING = "Service attempt has no current run user."
_AGENT_RUN_NOT_FOUND = "Agent run was not found."
_AGENT_RUN_NOT_LIVE = "Agent run is not live."
_AGENT_RUN_USER_MISSING = "Agent run has no current run user."
_AGENT_ACTOR_MISSING = "Agent run has no valid acting user."


@dataclass(frozen=True)
class RuntimeAuthority:
    kind: RuntimeKind
    credential_id: UUID
    run_user: RunUser
    powers: Powers | None
    acting_user_id: UUID
    organization_id: UUID | None
    workflow_id: UUID | None
    started_by_user_id: UUID | None
    root_execution_id: UUID | None
    service_id: UUID | None = None
    solution_id: UUID | None = None
    impersonated_by_user_id: UUID | None = None
    impersonation_session_id: UUID | None = None
    # Whether the run's own Solution is installed and active; None when the
    # run belongs to no Solution. Answers the run's own ``?solution=`` gate.
    solution_active: bool | None = None


async def load_runtime_authority(
    db: AsyncSession,
    kind: RuntimeKind,
    credential_id: UUID,
    *,
    service_id: UUID | None = None,
) -> RuntimeAuthority:
    """Load the current authority facts for one durable runtime credential."""
    if kind == "workflow_execution":
        return await _load_workflow_execution(db, credential_id)
    if kind == "service_attempt":
        return await _load_service_attempt(db, credential_id, service_id)
    if kind == "agent_run":
        return await _load_agent_run(db, credential_id)
    raise RuntimeIdentityError("Unknown runtime kind.")


async def _solution_active(db: AsyncSession, solution_id: UUID | None) -> bool | None:
    if solution_id is None:
        return None
    status = (await db.execute(select(Solution.status).where(Solution.id == solution_id))).scalar_one_or_none()
    return status == "active"


_WORKFLOW_AUTHORITY_COLUMNS = (Workflow.id, Workflow.solution_id, Workflow.permission_mode)


async def _load_workflow_execution(db: AsyncSession, execution_id: UUID) -> RuntimeAuthority:
    execution = await db.get(
        Execution,
        execution_id,
        options=[
            load_only(
                Execution.id,
                Execution.status,
                Execution.run_user_id,
                Execution.started_by_user_id,
                Execution.root_execution_id,
                Execution.executed_by,
                Execution.workflow_id,
                Execution.organization_id,
                Execution.impersonated_by_user_id,
                Execution.impersonation_session_id,
                raiseload=True,
            )
        ],
        populate_existing=True,
    )
    if execution is None:
        raise RuntimeIdentityError(_EXECUTION_NOT_FOUND)
    if execution.status not in LIVE_EXECUTION_STATUSES:
        raise RuntimeIdentityError(_EXECUTION_NOT_LIVE)
    if execution.run_user_id is None:
        raise RuntimeIdentityError(_EXECUTION_RUN_USER_MISSING)
    run_user = await load_run_user(db, execution.run_user_id)
    if run_user is None:
        raise RuntimeIdentityError(_EXECUTION_RUN_USER_MISSING)
    if execution.started_by_user_id is None or execution.root_execution_id is None:
        raise RuntimeIdentityError(_EXECUTION_LINEAGE_MISSING)
    if execution.executed_by is None or is_system_account(execution.executed_by):
        raise RuntimeIdentityError(_EXECUTION_ACTOR_MISSING)
    if execution.executed_by != run_user.user_id and await load_run_user(db, execution.executed_by) is None:
        raise RuntimeIdentityError(_EXECUTION_ACTOR_MISSING)

    powers = None
    solution_id = None
    if execution.workflow_id is not None:
        workflow = await db.get(
            Workflow,
            execution.workflow_id,
            options=[load_only(*_WORKFLOW_AUTHORITY_COLUMNS, raiseload=True)],
            populate_existing=True,
        )
        if workflow is None:
            raise RuntimeIdentityError(_WORKFLOW_MISSING)
        powers = await load_powers_for_workflow(db, workflow)
        solution_id = workflow.solution_id

    return RuntimeAuthority(
        kind="workflow_execution",
        credential_id=execution.id,
        run_user=run_user,
        powers=powers,
        acting_user_id=execution.executed_by,
        organization_id=execution.organization_id,
        workflow_id=execution.workflow_id,
        started_by_user_id=execution.started_by_user_id,
        root_execution_id=execution.root_execution_id,
        solution_id=solution_id,
        impersonated_by_user_id=execution.impersonated_by_user_id,
        impersonation_session_id=execution.impersonation_session_id,
        solution_active=await _solution_active(db, solution_id),
    )


async def _load_service_attempt(
    db: AsyncSession, attempt_id: UUID, service_id: UUID | None
) -> RuntimeAuthority:
    if service_id is None:
        raise RuntimeIdentityError(_SERVICE_ID_MISSING)
    # Column reads, not entity loads: they never touch objects the caller's
    # session already holds (the service claim path holds this attempt).
    attempt = (
        await db.execute(
            select(
                ServiceAttempt.id,
                ServiceAttempt.service_id,
                ServiceAttempt.run_user_id,
                ServiceAttempt.state,
                ServiceAttempt.lease_expires_at,
            ).where(ServiceAttempt.id == attempt_id)
        )
    ).one_or_none()
    if attempt is None:
        raise RuntimeIdentityError(_SERVICE_ATTEMPT_NOT_FOUND)
    if attempt.service_id != service_id:
        raise RuntimeIdentityError(_SERVICE_ATTEMPT_SERVICE_MISMATCH)
    definition = (
        await db.execute(
            select(
                ServiceDefinition.id,
                ServiceDefinition.workflow_id,
                ServiceDefinition.organization_id,
            ).where(ServiceDefinition.id == service_id)
        )
    ).one_or_none()
    if definition is None:
        raise RuntimeIdentityError(_SERVICE_NOT_FOUND)
    if attempt.state not in _SERVICE_LIVE_STATES:
        raise RuntimeIdentityError(_SERVICE_ATTEMPT_NOT_LIVE)
    if attempt.lease_expires_at <= datetime.now(timezone.utc):
        raise RuntimeIdentityError(_SERVICE_ATTEMPT_LEASE_EXPIRED)
    if attempt.run_user_id is None:
        raise RuntimeIdentityError(_SERVICE_RUN_USER_MISSING)
    run_user = await load_run_user(db, attempt.run_user_id)
    if run_user is None:
        raise RuntimeIdentityError(_SERVICE_RUN_USER_MISSING)
    workflow = (
        await db.execute(select(*_WORKFLOW_AUTHORITY_COLUMNS).where(Workflow.id == definition.workflow_id))
    ).one_or_none()
    if workflow is None:
        raise RuntimeIdentityError(_WORKFLOW_MISSING)

    return RuntimeAuthority(
        kind="service_attempt",
        credential_id=attempt.id,
        run_user=run_user,
        powers=await load_powers_for_workflow(db, workflow),
        acting_user_id=run_user.user_id,
        organization_id=definition.organization_id,
        workflow_id=workflow.id,
        started_by_user_id=None,
        root_execution_id=None,
        service_id=definition.id,
        solution_id=workflow.solution_id,
        solution_active=await _solution_active(db, workflow.solution_id),
    )


async def _load_agent_run(db: AsyncSession, run_id: UUID) -> RuntimeAuthority:
    # Column reads, not entity loads: the agent-run consumers call this with
    # the run object in their own session and keep using it.
    run = (
        await db.execute(
            select(
                AgentRun.id,
                AgentRun.status,
                AgentRun.run_user_id,
                AgentRun.acting_user_id,
                AgentRun.org_id,
                AgentRun.agent_id,
                AgentRun.impersonated_by_user_id,
                AgentRun.impersonation_session_id,
            ).where(AgentRun.id == run_id)
        )
    ).one_or_none()
    if run is None:
        raise RuntimeIdentityError(_AGENT_RUN_NOT_FOUND)
    if run.status not in LIVE_AGENT_RUN_STATUSES:
        raise RuntimeIdentityError(_AGENT_RUN_NOT_LIVE)
    if run.run_user_id is None:
        raise RuntimeIdentityError(_AGENT_RUN_USER_MISSING)
    run_user = await load_run_user(db, run.run_user_id)
    if run_user is None:
        raise RuntimeIdentityError(_AGENT_RUN_USER_MISSING)

    if run.acting_user_id is None:
        raise RuntimeIdentityError(_AGENT_ACTOR_MISSING)
    if run.acting_user_id != run_user.user_id and await load_run_user(db, run.acting_user_id) is None:
        raise RuntimeIdentityError(_AGENT_ACTOR_MISSING)
    solution_id = (
        None
        if run.agent_id is None
        else (await db.execute(select(Agent.solution_id).where(Agent.id == run.agent_id))).scalar_one_or_none()
    )
    return RuntimeAuthority(
        kind="agent_run",
        credential_id=run.id,
        run_user=run_user,
        powers=None,
        acting_user_id=run.acting_user_id,
        organization_id=run.org_id,
        solution_id=solution_id,
        workflow_id=None,
        started_by_user_id=None,
        root_execution_id=None,
        impersonated_by_user_id=run.impersonated_by_user_id,
        impersonation_session_id=run.impersonation_session_id,
        solution_active=await _solution_active(db, solution_id),
    )


