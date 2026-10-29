"""E2E boundaries for composing approved agent capabilities into one run."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    LoadCapabilityReturnPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolSearchReturnPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from src.models.contracts.executions import WorkflowExecutionResponse
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.models.orm.agents import Agent, AgentDelegation, AgentTool
from src.services.agent_runtime.composition import (
    AgentCompositionError,
    _delegate_name,
    _qualified_tool_name,
    build_agent_composition,
)
from src.services.authorization.enforce import load_caller
from src.services.authorization.runtime_auth import load_runtime_principal
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.execution.agent_helpers import resolve_agent_tools
from src.services.llm.base import LLMConfig


pytestmark = pytest.mark.asyncio


class _LoadsSpecialistCapability(TestModel):
    """Load one deferred capability, then use its child-owned workflow tool."""

    def __init__(
        self, capability_id: str, child_instruction: str, child_id: UUID, tool_name: str
    ) -> None:
        super().__init__(model_name="test-agent-capability-composition")
        self._capability_id = capability_id
        self._child_instruction = child_instruction
        self.loaded_instruction_text = ""
        self.child_tool_name = _qualified_tool_name(child_id, tool_name)
        self._source_tool_name = tool_name

    def _request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        del model_settings
        loaded_instructions = [
            part.instructions
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, LoadCapabilityReturnPart) and part.instructions
        ]
        self.loaded_instruction_text = "\n".join(loaded_instructions)
        completed = {
            part.tool_call_id
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        if "load-specialist" not in completed:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "load_capability",
                        {"id": self._capability_id},
                        tool_call_id="load-specialist",
                    )
                ],
                model_name=self.model_name,
            )

        assert loaded_instructions == [
            f"{self._child_instruction}\n\n"
            "Search for an approved individual tool only after loading this skill. "
            "Use it only for work this specialist owns and return evidence to the "
            "owning conversation."
        ]
        search_completed = any(
            isinstance(message, ModelRequest)
            and any(
                isinstance(part, ToolSearchReturnPart)
                and part.tool_call_id == "search-specialist-tool"
                for part in message.parts
            )
            for message in messages
        )
        if not search_completed:
            assert self.child_tool_name not in {
                tool.name
                for tool in model_request_parameters.function_tools
                if model_request_parameters.visibility_of(tool.name)
                in {"visible", "via_history"}
            }, "a loaded specialist tool must still be searched"
            assert any(
                tool.name == "search_tools"
                and model_request_parameters.visibility_of(tool.name)
                in {"visible", "via_history"}
                for tool in model_request_parameters.function_tools
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_tools",
                        {"queries": [self._source_tool_name]},
                        tool_call_id="search-specialist-tool",
                    )
                ],
                model_name=self.model_name,
            )

        if "call-specialist-tool" not in completed:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                f"receipt = {self.child_tool_name}(query='composition boundary')\n"
                                "{'answer': receipt['answer']}"
                            )
                        },
                        tool_call_id="call-specialist-tool",
                    )
                ],
                model_name=self.model_name,
            )
        return ModelResponse(
            parts=[TextPart("Specialist capability completed.")],
            model_name=self.model_name,
        )


class _NativeDelegationParentModel(TestModel):
    """Use the framework-native delegate tool once, then consume its receipt."""

    def __init__(self, child_name: str) -> None:
        super().__init__(model_name="test-native-delegation-parent")
        self._child_name = child_name
        self.parent_tool_return_ids: set[str] = set()

    def _request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        del model_settings, model_request_parameters
        self.parent_tool_return_ids = {
            part.tool_call_id
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        if "native-delegation-call" not in self.parent_tool_return_ids:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {
                            "agent_name": self._child_name,
                            "task": "Return durable child evidence.",
                        },
                        tool_call_id="native-delegation-call",
                    )
                ],
                model_name=self.model_name,
            )
        return ModelResponse(
            parts=[TextPart("Parent received the durable child result.")],
            model_name=self.model_name,
        )


def _create_agent(
    e2e_client,
    platform_admin,
    *,
    name: str,
    system_prompt: str,
    organization_id: str | None = None,
    tool_ids: list[str] | None = None,
    delegated_agent_ids: list[str] | None = None,
) -> dict:
    response = e2e_client.post(
        "/api/agents",
        headers=platform_admin.headers,
        json={
            "name": name,
            "description": "Agent capability composition coverage",
            "system_prompt": system_prompt,
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": organization_id,
            "tool_ids": tool_ids or [],
            "delegated_agent_ids": delegated_agent_ids or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _load_runtime_caller(session, run_id: UUID):
    principal = await load_runtime_principal(
        session,
        {"credential_kind": "agent_run", "sub": str(run_id)},
    )
    assert principal is not None
    return await load_caller(session, principal)


async def _load_agent(session, agent_id: str) -> Agent:
    result = await session.execute(
        select(Agent)
        .options(selectinload(Agent.tools), selectinload(Agent.delegated_agents))
        .where(Agent.id == UUID(agent_id))
    )
    return result.scalar_one()


async def test_loaded_child_capability_uses_its_bound_workflow_in_the_owning_run(
    e2e_client,
    platform_admin,
    async_session_factory,
    db_session,
    test_tool_workflow,
):
    """A skill stays in the root run while dispatching only its live child binding."""
    tag = uuid4().hex[:8]
    child_instruction = "SPECIALIST INSTRUCTION: inspect the requested service boundary."
    child = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Capability Specialist {tag}",
        system_prompt=child_instruction,
        tool_ids=[test_tool_workflow["id"]],
    )
    root = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Capability Coordinator {tag}",
        system_prompt="Coordinate the request.",
        delegated_agent_ids=[child["id"]],
    )
    run_id = uuid4()
    model = _LoadsSpecialistCapability(
        f"bifrost_agent_{UUID(child['id']).hex}", child_instruction,
        UUID(child["id"]), f"wf_{test_tool_workflow['name']}",
    )
    try:
        root_agent = await _load_agent(db_session, root["id"])
        db_session.add(
            AgentRun(
                id=run_id,
                agent_id=root_agent.id,
                trigger_type="manual",
                status="running",
                caller_user_id=str(platform_admin.user_id),
                run_user_id=platform_admin.user_id,
                acting_user_id=platform_admin.user_id,
            )
        )
        await db_session.commit()
        caller = await _load_runtime_caller(db_session, run_id)
        dispatched = AsyncMock(
            return_value=WorkflowExecutionResponse(
                execution_id=str(uuid4()),
                status=ExecutionStatus.SUCCESS,
                result={"answer": "from the child-bound workflow"},
            )
        )
        executor = AutonomousAgentExecutor(async_session_factory)

        with (
            patch(
                "src.services.execution.autonomous_agent_executor.get_llm_configs",
                new_callable=AsyncMock,
                return_value=[
                    LLMConfig(
                        provider="openai",
                        model="test-agent-capability-composition",
                        api_key="test-key",
                    )
                ],
            ),
            patch(
                "src.services.agent_runtime.model_factory.create_agent_model",
                return_value=model,
            ),
            patch(
                "src.services.execution.service.execute_tool",
                new=dispatched,
            ),
        ):
            result = await executor.run(
                agent=root_agent,
                input_data={"task": "Use the specialist capability."},
                run_id=str(run_id),
                caller=caller,
                run_user_id=platform_admin.user_id,
                run_as=None,
                agent_execution_mode="automatic",
            )

        assert result["status"] == "completed", result
        assert result["output"] == "Specialist capability completed."
        assert model.child_tool_name is not None
        assert model.loaded_instruction_text.count(child_instruction) == 1
        dispatch_kwargs = dispatched.await_args.kwargs
        assert dispatch_kwargs["workflow_id"] == test_tool_workflow["id"]
        assert dispatch_kwargs["parameters"] == {"query": "composition boundary"}
        assert dispatch_kwargs["is_agent"] is True
        assert dispatch_kwargs["user_id"] == str(platform_admin.user_id)

        await executor.flush_to_db(db_session)
        await db_session.commit()
        steps = list(
            (
                await db_session.execute(
                    select(AgentRunStep)
                    .where(AgentRunStep.run_id == run_id)
                    .order_by(AgentRunStep.step_number)
                )
            ).scalars()
        )
        skill_steps = [
            step
            for step in steps
            if step.content and step.content.get("tool_name") == model.child_tool_name
        ]
        assert [step.type for step in skill_steps] == ["tool_call", "tool_result"]
        for step in skill_steps:
            assert step.content is not None
            assert step.content["source_agent_id"] == child["id"]
            assert step.content["source_tool_name"] == f"wf_{test_tool_workflow['name']}"
            assert step.content["source_path_agent_ids"] == [root["id"], child["id"]]
            assert step.content["capability_id"] == (
                f"bifrost_agent_{UUID(child['id']).hex}"
            )

        child_run_count = await db_session.scalar(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.agent_id == UUID(child["id"])
            )
        )
        assert child_run_count == 0
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await db_session.commit()
        for agent in (root, child):
            e2e_client.delete(
                f"/api/agents/{agent['id']}", headers=platform_admin.headers
            )


async def test_native_delegate_task_runs_a_transitive_child_without_a_middle_run(
    e2e_client,
    platform_admin,
    async_session_factory,
    db_session,
):
    """A native grandchild delegate reuses the middle agent's durable lifecycle."""
    tag = uuid4().hex[:8]
    child = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Native Child {tag}",
        system_prompt="Return the durable child evidence.",
    )
    middle = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Native Middle {tag}",
        system_prompt="Delegate approved work to the child.",
        delegated_agent_ids=[child["id"]],
    )
    root = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Native Root {tag}",
        system_prompt="Coordinate the approved descendant tree.",
        delegated_agent_ids=[middle["id"]],
    )
    run_id = uuid4()
    try:
        root_agent = await _load_agent(db_session, root["id"])
        child_agent = await _load_agent(db_session, child["id"])
        db_session.add(
            AgentRun(
                id=run_id,
                agent_id=root_agent.id,
                trigger_type="manual",
                status="running",
                caller_user_id=str(platform_admin.user_id),
                run_user_id=platform_admin.user_id,
                acting_user_id=platform_admin.user_id,
            )
        )
        await db_session.commit()
        caller = await _load_runtime_caller(db_session, run_id)
        parent_model = _NativeDelegationParentModel(_delegate_name(child_agent))
        child_model = TestModel(
            custom_output_text="Durable native child result.",
            model_name="test-native-delegation-child",
        )
        executor = AutonomousAgentExecutor(async_session_factory)

        with (
            patch(
                "src.services.execution.autonomous_agent_executor.get_llm_configs",
                new_callable=AsyncMock,
                return_value=[
                    LLMConfig(
                        provider="openai",
                        model="test-native-delegation",
                        api_key="test-key",
                    )
                ],
            ),
            patch(
                "src.services.agent_runtime.model_factory.create_agent_model",
                side_effect=[parent_model, child_model],
            ) as create_model,
            patch(
                "src.services.execution.summary_dispatch.stage_summary",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await executor.run(
                agent=root_agent,
                input_data={"task": "Use the approved native child capability."},
                run_id=str(run_id),
                caller=caller,
                run_user_id=platform_admin.user_id,
                run_as=None,
                agent_execution_mode="automatic",
            )

        assert result["status"] == "completed"
        assert result["output"] == "Parent received the durable child result."
        assert create_model.call_count == 2
        assert parent_model.parent_tool_return_ids == {"native-delegation-call"}

        await executor.flush_to_db(db_session)
        await db_session.commit()
        child_runs = list(
            (
                await db_session.execute(
                    select(AgentRun).where(AgentRun.parent_run_id == run_id)
                )
            ).scalars()
        )
        assert len(child_runs) == 1
        child_run = child_runs[0]
        assert child_run.agent_id == UUID(child["id"])
        assert child_run.status == "completed"
        assert child_run.output == {"text": "Durable native child result."}
        assert child_run.caller_user_id == str(platform_admin.user_id)
        assert child_run.run_user_id == platform_admin.user_id
        assert child_run.acting_user_id == platform_admin.user_id
        assert await db_session.scalar(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.agent_id == UUID(middle["id"])
            )
        ) == 0

        steps = list(
            (
                await db_session.execute(
                    select(AgentRunStep)
                    .where(AgentRunStep.run_id == run_id)
                    .order_by(AgentRunStep.step_number)
                )
            ).scalars()
        )
        delegation_result = next(
            step
            for step in steps
            if step.type == "tool_result"
            and step.content
            and step.content["tool_name"] == "delegate_task"
        )
        assert delegation_result.content is not None
        assert delegation_result.content["child_run_id"] == str(child_run.id)
        assert delegation_result.content["result"] == "Durable native child result."
        assert delegation_result.content["source_agent_id"] == middle["id"]
        assert delegation_result.content["source_path_agent_ids"] == [
            root["id"],
            middle["id"],
        ]
        assert delegation_result.content["delegated_agent_id"] == child["id"]
        assert delegation_result.content["delegated_target_path_agent_ids"] == [
            root["id"],
            middle["id"],
            child["id"],
        ]
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await db_session.commit()
        for agent in (root, middle, child):
            e2e_client.delete(
                f"/api/agents/{agent['id']}", headers=platform_admin.headers
            )


async def test_revoked_delegation_or_tool_binding_is_denied_before_dispatch(
    e2e_client,
    platform_admin,
    async_session_factory,
    db_session,
    test_tool_workflow,
):
    """A composition snapshot never keeps a removed edge or tool binding alive."""
    tag = uuid4().hex[:8]
    child = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Revocable Specialist {tag}",
        system_prompt="Use your one tool.",
        tool_ids=[test_tool_workflow["id"]],
    )
    root = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Revocation Coordinator {tag}",
        system_prompt="Delegate only when approved.",
        delegated_agent_ids=[child["id"]],
    )
    try:
        caller = await _load_runtime_caller_for_human(db_session, platform_admin.user_id)
        root_agent = await _load_agent(db_session, root["id"])
        definitions, workflow_ids = await resolve_agent_tools(
            root_agent, db_session, caller_user_id=platform_admin.user_id
        )
        composition = await build_agent_composition(
            root_agent,
            caller=caller,
            session_factory=async_session_factory,
            caller_user_id=platform_admin.user_id,
            entry_definitions=definitions,
            entry_workflow_ids=workflow_ids,
        )
        child_tool = next(
            name
            for name, binding in composition._bindings.items()
            if binding.owner_id == UUID(child["id"])
            and binding.original_name == f"wf_{test_tool_workflow['name']}"
        )

        async with async_session_factory() as revoked:
            await revoked.execute(
                delete(AgentDelegation).where(
                    AgentDelegation.parent_agent_id == UUID(root["id"]),
                    AgentDelegation.child_agent_id == UUID(child["id"]),
                )
            )
            await revoked.commit()
        with pytest.raises(AgentCompositionError, match="delegation is no longer available"):
            await composition.resolve_call(child_tool, {"query": "must not dispatch"})

        async with async_session_factory() as restored:
            restored.add(
                AgentDelegation(
                    parent_agent_id=UUID(root["id"]), child_agent_id=UUID(child["id"])
                )
            )
            await restored.commit()
        composition = await build_agent_composition(
            root_agent,
            caller=caller,
            session_factory=async_session_factory,
            caller_user_id=platform_admin.user_id,
            entry_definitions=definitions,
            entry_workflow_ids=workflow_ids,
        )
        async with async_session_factory() as revoked:
            await revoked.execute(
                delete(AgentTool).where(
                    AgentTool.agent_id == UUID(child["id"]),
                    AgentTool.workflow_id == UUID(test_tool_workflow["id"]),
                )
            )
            await revoked.commit()
        with pytest.raises(AgentCompositionError, match="Tool is no longer bound"):
            await composition.resolve_call(child_tool, {"query": "must not dispatch"})
    finally:
        for agent in (root, child):
            e2e_client.delete(
                f"/api/agents/{agent['id']}", headers=platform_admin.headers
            )


async def test_foreign_organization_delegate_is_not_composed_from_a_readable_root(
    e2e_client,
    platform_admin,
    org1,
    org2,
    async_session_factory,
    db_session,
):
    """Readable root access cannot use a manually-inserted foreign-org edge."""
    tag = uuid4().hex[:8]
    root = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Scoped Coordinator {tag}",
        system_prompt="Stay inside your organization.",
        organization_id=org1["id"],
    )
    child = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Foreign Specialist {tag}",
        system_prompt="This capability belongs to another organization.",
        organization_id=org2["id"],
    )
    try:
        db_session.add(
            AgentDelegation(
                parent_agent_id=UUID(root["id"]), child_agent_id=UUID(child["id"])
            )
        )
        await db_session.commit()
        caller = await _load_runtime_caller_for_human(db_session, platform_admin.user_id)
        root_agent = await _load_agent(db_session, root["id"])
        definitions, workflow_ids = await resolve_agent_tools(
            root_agent, db_session, caller_user_id=platform_admin.user_id
        )
        composition = await build_agent_composition(
            root_agent,
            caller=caller,
            session_factory=async_session_factory,
            caller_user_id=platform_admin.user_id,
            entry_definitions=definitions,
            entry_workflow_ids=workflow_ids,
        )

        assert all(
            binding.owner_id != UUID(child["id"])
            for binding in composition._bindings.values()
        )
        with pytest.raises(AgentCompositionError, match="is not available"):
            await composition.resolve_call("foreign-capability", {})
    finally:
        for agent in (root, child):
            e2e_client.delete(
                f"/api/agents/{agent['id']}", headers=platform_admin.headers
            )


async def _load_runtime_caller_for_human(session, user_id: UUID):
    """Use the normal canonical caller loader for resolver-only denials."""
    from src.services.authorization.callers import load_human_caller

    return await load_human_caller(session, user_id)
