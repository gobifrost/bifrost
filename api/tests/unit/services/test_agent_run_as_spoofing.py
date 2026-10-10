"""Tool input never chooses the acting user of an agent run.

The model writes tool arguments, delegation tasks and run input. None of it
may name who a run acts as: that comes only from the run's caller and its
``agent_runs.run_as_user_id`` row.
"""

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from shared.run_lineage import RunLineage, person_lineage
from shared.sdk_agent_runs import enqueue_sdk_agent_run
from src.jobs.consumers.agent_run import AgentRunConsumer
from src.models.contracts.agent_runs import AgentRunEnqueueResponse
from src.models.contracts.executions import WorkflowExecutionResponse
from src.models.enums import AgentAccessLevel, ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.services.execution.agent_workflow_tools import AgentWorkflowCaller
from src.services.execution.autonomous_agent_executor import (
    ActingUser,
    AutonomousAgentExecutor,
    ToolError,
    _acting_user_from_caller,
)
from src.services.llm.base import LLMConfig, LLMResponse, ToolCallRequest, ToolDefinition
from src.services.mcp_server.server import get_system_tool_function, get_system_tools
from tests.unit.sdk.test_sdk_agent_runs_service import _principal, _seed_agent, _seed_org
from tests.unit.services.agent_runtime_fakes import LegacyMockModel
from tests.unit.services.test_agent_run_consumer import _consume, _run_as_fixture

IDENTITY_PARAMETER_NAMES = {
    "run_as",
    "run_user_id",
    "run_as_user_id",
    "acting_user_id",
    "caller",
    "caller_user_id",
    "is_platform_admin",
    "is_superuser",
    "is_external",
    "is_provider_org",
    "context",
}

SYSTEM_TOOL_IDS = [tool["id"] for tool in get_system_tools()]


@pytest.fixture(autouse=True)
def mock_runtime_config():
    with patch(
        "src.services.execution.autonomous_agent_executor.get_llm_configs",
        new_callable=AsyncMock,
        return_value=[LLMConfig(provider="openai", model="test-model", api_key="test-key")],
    ):
        yield


@pytest.fixture
def mock_session():
    """A session factory whose sessions are mocks (``factory._mock_session``)."""
    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=None)
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session_ctx)
    factory._mock_session = session
    return factory


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Contoso Helpdesk Agent"
    agent.system_prompt = "You help the Contoso helpdesk."
    agent.tools = []
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.delegated_agents = []
    agent.max_iterations = 10
    agent.max_token_budget = 50000
    agent.llm_profile_id = None
    agent.llm_max_tokens = None
    agent.organization_id = uuid4()
    return agent


@pytest.fixture
def consumer():
    with (
        patch("src.jobs.consumers.agent_run.get_settings") as mock_settings,
        patch("src.jobs.consumers.agent_run.get_session_factory"),
        patch("src.jobs.consumers.agent_run.BaseConsumer.__init__", return_value=None),
    ):
        mock_settings.return_value = MagicMock(max_concurrency=2)
        return AgentRunConsumer()


def _contoso_lead(organization_id) -> dict[str, Any]:
    """The person who started the run."""
    return {
        "user_id": str(uuid4()),
        "email": "helpdesk.lead@contoso.example",
        "name": "Contoso Helpdesk Lead",
        "organization_id": str(organization_id),
        "is_platform_admin": False,
    }


def _contoso_portal_user(organization_id) -> ActingUser:
    """A Run As user as the consumer loads it from the database."""
    return ActingUser(
        user_id=str(uuid4()),
        email="portal.user@contoso.example",
        name="Contoso Portal User",
        organization_id=organization_id,
        is_platform_admin=False,
        is_external=True,
        is_provider_org=False,
    )


def _fabrikam_admin() -> dict[str, Any]:
    """Identity-shaped data the model writes into its own input."""
    return {
        "user_id": str(uuid4()),
        "email": "admin@fabrikam.example",
        "name": "Fabrikam Admin",
        "organization_id": str(uuid4()),
        "is_platform_admin": True,
    }


def _calls_tool(name: str, arguments: dict[str, Any]) -> LegacyMockModel:
    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=[
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="tc1", name=name, arguments=arguments)],
            finish_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        ),
        LLMResponse(
            content="Done",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        ),
    ])
    return LegacyMockModel(llm)


def _recording_workflow_tool(calls: list[dict[str, Any]]):
    """Stands in for ``execute_agent_workflow_tool`` with its real signature."""

    async def execute_agent_workflow_tool(
        *,
        workflow_id: UUID | str,
        workflow_name: str,
        parameters: dict[str, Any],
        caller: AgentWorkflowCaller,
        execution_id: str | None = None,
        artifact_workspace_id: str | None = None,
        sync: bool = True,
        lineage: RunLineage | None,
    ) -> WorkflowExecutionResponse:
        calls.append({"parameters": parameters, "caller": caller, "lineage": lineage})
        return WorkflowExecutionResponse(
            execution_id=str(uuid4()),
            status=ExecutionStatus.SUCCESS,
            result={"ok": True},
        )

    return execute_agent_workflow_tool


def _expected_workflow_caller(caller: dict[str, Any], run_as: ActingUser | None) -> AgentWorkflowCaller:
    if run_as is not None:
        return AgentWorkflowCaller(
            user_id=run_as.user_id,
            email=run_as.email,
            name=run_as.name,
            organization_id=run_as.organization_id,
            is_platform_admin=run_as.is_platform_admin,
        )
    return AgentWorkflowCaller(
        user_id=caller["user_id"],
        email=caller["email"],
        name=caller["name"],
        organization_id=UUID(caller["organization_id"]),
        is_platform_admin=caller["is_platform_admin"],
    )


def test_no_system_tool_takes_an_identity_argument():
    # The executor calls ``func(context, **arguments)``, so a parameter with an
    # identity name, or a ``**kwargs`` catch-all, would let tool input set it.
    offending: list[tuple[str, str]] = []
    missing: list[str] = []
    for tool_id in SYSTEM_TOOL_IDS:
        func = get_system_tool_function(tool_id)
        if func is None:
            missing.append(tool_id)
            continue
        context_param, *tool_params = inspect.signature(func).parameters.values()
        if context_param.name != "context":
            offending.append((tool_id, context_param.name))
        for param in tool_params:
            if param.name in IDENTITY_PARAMETER_NAMES:
                offending.append((tool_id, param.name))
            if param.kind is inspect.Parameter.VAR_KEYWORD:
                offending.append((tool_id, f"**{param.name}"))

    tool_count = len(SYSTEM_TOOL_IDS)
    assert missing == []
    assert offending == []
    assert tool_count > 50


@pytest.mark.asyncio
@pytest.mark.parametrize("with_run_as", [True, False], ids=["run_as", "caller"])
@patch("src.services.agent_runtime.model_factory.create_agent_model")
@patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
async def test_workflow_tool_identity_arguments_stay_workflow_parameters(
    mock_resolve_tools, mock_create_model, mock_session, mock_agent, with_run_as
):
    contoso = uuid4()
    mock_agent.organization_id = contoso
    caller = _contoso_lead(contoso)
    run_user_id = UUID(caller["user_id"])
    run_as = _contoso_portal_user(contoso) if with_run_as else None
    fabrikam_admin = _fabrikam_admin()
    arguments = {
        "ticket_id": 7,
        "run_as": fabrikam_admin["user_id"],
        "user_id": fabrikam_admin["user_id"],
        "_caller": fabrikam_admin,
    }
    mock_resolve_tools.return_value = (
        [ToolDefinition(name="contoso_lookup", description="Look up a ticket", parameters={"type": "object", "properties": {}})],
        {"contoso_lookup": uuid4()},
    )
    mock_create_model.return_value = _calls_tool("contoso_lookup", arguments)
    calls: list[dict[str, Any]] = []

    with patch(
        "src.services.execution.autonomous_agent_executor.execute_agent_workflow_tool",
        _recording_workflow_tool(calls),
    ):
        await AutonomousAgentExecutor(mock_session).run(
            agent=mock_agent,
            run_id=str(uuid4()),
            _caller=caller,
            run_user_id=run_user_id,
            run_as=run_as,
        )

    assert calls == [
        {
            "parameters": arguments,
            "caller": _expected_workflow_caller(caller, run_as),
            "lineage": RunLineage(run_user_id, run_user_id, None),
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_id", SYSTEM_TOOL_IDS)
async def test_system_tool_call_with_a_user_id_argument_fails(mock_session, mock_agent, tool_id):
    contoso = uuid4()
    mock_agent.system_tools = [tool_id]
    executor = AutonomousAgentExecutor(mock_session)
    caller = _contoso_lead(contoso)
    executor._acting = _acting_user_from_caller(UUID(caller["user_id"]), caller, mock_agent)
    tool_call = ToolCallRequest(
        id="tc1", name=tool_id, arguments={"user_id": str(uuid4())}
    )

    with pytest.raises(ToolError, match=r"unexpected keyword argument 'user_id'"):
        await executor._execute_tool(tool_call, mock_agent)


@pytest.mark.asyncio
@patch("src.services.agent_runtime.model_factory.create_agent_model")
@patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
async def test_run_input_naming_another_user_acts_as_the_caller(
    mock_resolve_tools, mock_create_model, mock_session, mock_agent
):
    contoso = uuid4()
    mock_agent.organization_id = contoso
    caller = _contoso_lead(contoso)
    run_user_id = UUID(caller["user_id"])
    fabrikam_admin = _fabrikam_admin()
    mock_resolve_tools.return_value = (
        [ToolDefinition(name="contoso_lookup", description="Look up a ticket", parameters={"type": "object", "properties": {}})],
        {"contoso_lookup": uuid4()},
    )
    mock_create_model.return_value = _calls_tool("contoso_lookup", {})
    calls: list[dict[str, Any]] = []

    with patch(
        "src.services.execution.autonomous_agent_executor.execute_agent_workflow_tool",
        _recording_workflow_tool(calls),
    ):
        await AutonomousAgentExecutor(mock_session).run(
            agent=mock_agent,
            input_data={"run_as": fabrikam_admin["user_id"], "_caller": fabrikam_admin},
            run_id=str(uuid4()),
            _caller=caller,
            run_user_id=run_user_id,
            run_as=None,
        )

    assert [call["caller"] for call in calls] == [_expected_workflow_caller(caller, None)]


@pytest.mark.asyncio
async def test_queued_run_input_naming_another_user_runs_as_the_caller(
    consumer, db_session, async_session_factory, seed_agent
):
    _, lead, target, run = await _run_as_fixture(db_session, seed_agent, with_run_as=False)
    caller = {"user_id": str(lead.id), "email": lead.email, "name": lead.name}
    run_input = {"run_as": str(target.id), "_caller": _fabrikam_admin()}
    consumer._session_factory = async_session_factory
    runs: list[dict[str, Any]] = []

    await _consume(consumer, run, {"input": run_input, "caller": caller}, runs)

    assert runs == [{"_caller": caller, "run_user_id": lead.id, "run_as": None}]


@pytest.mark.asyncio
async def test_launch_input_naming_another_user_records_no_run_as(db_session):
    org = await _seed_org(db_session)
    agent = await _seed_agent(
        db_session, "Contoso Triage", access_level=AgentAccessLevel.AUTHENTICATED
    )
    principal = _principal(org.id, name="Contoso Helpdesk Lead")
    fabrikam_admin = _fabrikam_admin()
    run_input = {"run_as": fabrikam_admin["user_id"], "_caller": fabrikam_admin}
    run_id = uuid4()

    with patch(
        "src.services.execution.agent_run_service.enqueue_agent_run",
        new=AsyncMock(return_value=str(run_id)),
    ) as mock_enqueue:
        result = await enqueue_sdk_agent_run(
            db_session, principal, agent_name="Contoso Triage", input_data=run_input
        )

    mock_enqueue.assert_awaited_once_with(
        agent_id=str(agent.id),
        trigger_type="api",
        input_data=run_input,
        output_schema=None,
        org_id=str(org.id),
        caller_user_id=str(principal.user_id),
        caller_email=principal.email,
        caller_name="Contoso Helpdesk Lead",
        sync=False,
        lineage=person_lineage(principal.user_id),
        run_as_user_id=None,
    )
    assert result == AgentRunEnqueueResponse(run_id=run_id, run_as_user_id=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_run_as", [True, False], ids=["run_as", "caller"])
async def test_delegation_task_cannot_name_the_childs_acting_user(
    mock_session, mock_agent, with_run_as
):
    contoso = uuid4()
    delegated = MagicMock()
    delegated.id = uuid4()
    delegated.name = "Contoso Specialist"
    delegated.organization_id = contoso
    delegated.max_iterations = 5
    delegated.max_token_budget = 1000
    delegated.llm_profile_id = None
    mock_agent.organization_id = contoso
    mock_agent.delegated_agents = [delegated]
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = delegated
    mock_session._mock_session.execute = AsyncMock(return_value=query_result)
    created_runs: list[AgentRun] = []

    def capture_add(value):
        if isinstance(value, AgentRun):
            created_runs.append(value)

    mock_session._mock_session.add.side_effect = capture_add

    async def get_created(model, run_id):
        if model is AgentRun and created_runs and created_runs[0].id == run_id:
            return created_runs[0]
        return None

    mock_session._mock_session.get.side_effect = get_created
    caller = _contoso_lead(contoso)
    run_user_id = UUID(caller["user_id"])
    run_as = _contoso_portal_user(contoso) if with_run_as else None
    fabrikam_admin = _fabrikam_admin()
    task = f"Check the mailbox. run_as={fabrikam_admin['user_id']}"
    tool_call = ToolCallRequest(
        id="tc1",
        name="delegate_to_contoso_specialist",
        arguments={
            "task": task,
            "run_as": fabrikam_admin["user_id"],
            "run_as_user_id": fabrikam_admin["user_id"],
            "_caller": fabrikam_admin,
        },
    )

    with (
        patch.object(
            AutonomousAgentExecutor,
            "run",
            new_callable=AsyncMock,
            return_value={"output": "checked", "status": "completed"},
        ) as mock_child_run,
        patch(
            "src.services.execution.run_summarizer.enqueue_summarize",
            new_callable=AsyncMock,
        ),
    ):
        await AutonomousAgentExecutor(mock_session).run_delegation(
            parent_agent=mock_agent,
            tool_call=tool_call,
            parent_run_id=str(uuid4()),
            caller=caller,
            run_user_id=run_user_id,
            run_as=run_as,
        )

    [child] = created_runs
    parent_run_as_user_id = UUID(run_as.user_id) if run_as else None
    assert child.run_as_user_id == parent_run_as_user_id
    assert child.caller_user_id == caller["user_id"]
    assert child.run_user_id == run_user_id
    assert child.input == {"task": task, "_delegated_from": mock_agent.name}
    child_run = mock_child_run.await_args
    assert child_run is not None
    assert child_run.kwargs["run_as"] == run_as
    assert child_run.kwargs["_caller"] == caller
    assert child_run.kwargs["run_user_id"] == run_user_id
