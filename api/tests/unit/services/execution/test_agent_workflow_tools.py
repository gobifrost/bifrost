"""Tests for the canonical agent workflow-tool launch boundary."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.authorization.runtime import RuntimeAuthority
from src.services.authorization.explain import RunUser
from src.services.execution.agent_workflow_tools import execute_agent_workflow_tool


class _Reach:
    def covers(self, _organization_id):
        return True


@pytest.mark.asyncio
async def test_bound_tool_uses_original_user_for_authority_and_actor_for_effects(monkeypatch):
    actor_id = uuid4()
    original_user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    agent_id = uuid4()

    original_ctx = MagicMock(spec=AuthorizationContext)
    original_ctx.user_id = original_user_id
    original_ctx.home_organization_id = organization_id
    runtime = RuntimeAuthority(
        kind="agent_run",
        credential_id=uuid4(),
        run_user=RunUser(original_user_id, original_ctx, None, is_active=True),
        powers=None,
        acting_user_id=actor_id,
        organization_id=organization_id,
        workflow_id=None,
        started_by_user_id=None,
        root_execution_id=None,
    )
    caller = Caller(
        UserPrincipal(
            user_id=actor_id,
            email="actor@example.test",
            name="Actor",
            organization_id=organization_id,
            run_user_id=original_user_id,
            runtime_authority=runtime,
        ),
        original_ctx,
    )

    agent = MagicMock()
    agent.id = agent_id
    agent.organization_id = organization_id
    agent.solution_id = None
    workflow = MagicMock()
    workflow.id = workflow_id
    workflow.name = "bound_tool"
    workflow.organization_id = organization_id
    workflow.solution_id = None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(one_or_none=MagicMock(return_value=(agent, workflow))))
    session.commit = AsyncMock()

    @asynccontextmanager
    async def session_context():
        yield session

    session_factory = MagicMock(return_value=session_context())
    require_calls = []

    async def allow_agent(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "src.core.database.get_session_factory",
        MagicMock(return_value=session_factory),
    )
    monkeypatch.setattr(
        "src.services.execution.agent_workflow_tools.refresh_caller",
        AsyncMock(return_value=caller),
    )
    monkeypatch.setattr(
        "src.services.execution.agent_workflow_tools.user_reach",
        MagicMock(return_value=_Reach()),
    )
    monkeypatch.setattr(
        "src.services.execution.agent_workflow_tools.require_permission",
        lambda refreshed, permission, target: require_calls.append(
            (refreshed.ctx.user_id, permission, target)
        ),
    )
    monkeypatch.setattr(
        "src.services.agent_write_policy.can_read_agent",
        allow_agent,
    )
    execute_tool = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("src.services.execution.service.execute_tool", execute_tool)

    result = await execute_agent_workflow_tool(
        agent_id=agent_id,
        workflow_id=workflow_id,
        parameters={"ticket_id": 7},
        caller=caller,
        execution_id="execution-1",
        artifact_workspace_id="workspace-1",
        sync=False,
    )

    assert result is execute_tool.return_value
    assert require_calls == [
        (original_user_id, "agents.execute", organization_id),
        (original_user_id, "workflows.execute", organization_id),
    ]
    assert execute_tool.await_args.kwargs["user_id"] == str(actor_id)
    assert execute_tool.await_args.kwargs["org_id"] == str(organization_id)
    lineage = execute_tool.await_args.kwargs["lineage"]
    assert lineage.run_user_id == original_user_id
    assert lineage.started_by_user_id == original_user_id


@pytest.mark.asyncio
async def test_refresh_precedes_current_binding_and_permission_checks(monkeypatch):
    actor_id = uuid4()
    old_user_id = uuid4()
    current_user_id = uuid4()
    organization_id = uuid4()
    caller = Caller(
        UserPrincipal(
            user_id=actor_id,
            email="actor@example.test",
            organization_id=organization_id,
        ),
        MagicMock(user_id=old_user_id, home_organization_id=organization_id),
    )
    refreshed = Caller(
        caller.principal,
        MagicMock(user_id=current_user_id, home_organization_id=organization_id),
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(one_or_none=MagicMock(return_value=None)))

    @asynccontextmanager
    async def session_context():
        yield session

    session_factory = MagicMock(return_value=session_context())
    monkeypatch.setattr(
        "src.core.database.get_session_factory",
        MagicMock(return_value=session_factory),
    )
    refresher = AsyncMock(return_value=refreshed)
    monkeypatch.setattr("src.services.execution.agent_workflow_tools.refresh_caller", refresher)

    with pytest.raises(ValueError, match="Workflow tool is no longer available"):
        await execute_agent_workflow_tool(
            agent_id=uuid4(),
            workflow_id=uuid4(),
            parameters={},
            caller=caller,
        )

    refresher.assert_awaited_once_with(session, caller)
    session.execute.assert_awaited_once()
