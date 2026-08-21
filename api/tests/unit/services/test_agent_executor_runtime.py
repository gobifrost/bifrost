"""Chat contract coverage for the Pydantic AI loop."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from shared.authorization_scopes import PLATFORM_SUPERUSER_SCOPE
from src.models.contracts.agents import ToolResult
from src.services.agent_executor import AgentExecutor
from src.services.llm import LLMMessage, ToolDefinition
from src.services.llm.base import ToolCallRequest
from src.services.llm.base import LLMConfig
from src.services.llm.pydantic_client import PydanticAIClient
from src.services.llm_config_service import LLMProviderConfig
from src.services.model_capabilities import manual_capabilities
from src.services.native_chat_profile import NativeChatExecutionProfile
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


class CountingTestModel(TestModel):
    """TestModel with the pre-request token-count API used by hard budgets."""

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return RequestUsage(input_tokens=10)


class CapturingTestModel(CountingTestModel):
    """Capture the exact request after Pydantic has applied instructions."""

    requests: list[list[ModelMessage]]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests = []

    @asynccontextmanager
    async def request_stream(self, messages, *args, **kwargs):
        self.requests.append(messages)
        async with super().request_stream(messages, *args, **kwargs) as response:
            yield response


@pytest.fixture
def executor() -> AgentExecutor:
    return AgentExecutor(MagicMock())


@pytest.fixture(autouse=True)
def configured_chat_model(monkeypatch: pytest.MonkeyPatch):
    """Keep these runtime-contract tests focused on the Pydantic event loop."""
    config = LLMProviderConfig(
        provider="openai",
        model="test-model",
        chat_balanced_capabilities=manual_capabilities(
            provider="openai",
            model="test-model",
            endpoint=None,
            image_input=False,
            pdf_input=False,
            tool_calling=True,
        ),
    )

    class _NoopUsageGovernance:
        async def constrain_budget(self, _session, budget):
            return budget

        async def record_runner_completion(self, _session, *, runner_duration_ms):
            del runner_duration_ms

        def observe_model_usage(self, _usage):
            return False

    with patch(
        "src.services.llm_config_service.LLMConfigService.get_config",
        new=AsyncMock(return_value=config),
    ):
        monkeypatch.setattr(
            "src.services.agent_executor.build_runtime_usage_governance",
            AsyncMock(return_value=_NoopUsageGovernance()),
        )
        yield


@pytest.fixture
def conversation():
    value = MagicMock()
    value.id = uuid4()
    value.user_id = uuid4()
    return value


def _saved_message(*args, **kwargs):
    return MagicMock(id=kwargs.get("message_id") or uuid4())


def _authorization(
    boundary: AuthorizationBoundary,
    *capabilities: str,
) -> AuthorizationContext:
    principal = MagicMock()
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.asyncio
async def test_chat_stream_contract_is_driven_by_pydantic_runtime(
    executor: AgentExecutor,
    conversation,
) -> None:
    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._record_ai_usage = AsyncMock()
    executor._build_message_history = AsyncMock(
        return_value=[
            LLMMessage(role="system", content="Be useful"),
            LLMMessage(role="user", content="Hello"),
        ]
    )
    client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-model", api_key="test-key")
    )

    with patch(
        "src.services.agent_executor.get_llm_client",
        new_callable=AsyncMock,
        return_value=client,
    ), patch(
        "src.services.agent_executor.create_agent_model",
        return_value=CountingTestModel(
            call_tools=[],
            custom_output_text="Hello from Pydantic",
        ),
    ):
        chunks = [
            chunk
            async for chunk in executor.chat(
                None,
                conversation,
                "Hello",
                stream=True,
                enable_routing=False,
            )
        ]

    assert "".join(chunk.content or "" for chunk in chunks if chunk.type == "delta") == (
        "Hello from Pydantic"
    )
    done = next(chunk for chunk in chunks if chunk.type == "done")
    assert done.content == "Hello from Pydantic"
    assert executor.active_usage is not None
    assert executor.active_usage.requests > 0
    executor._record_ai_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_agentless_chat_uses_maintained_bifrost_gateway_profile(
    executor: AgentExecutor,
    conversation,
) -> None:
    user_id = uuid4()
    org_id = uuid4()
    user = MagicMock(user_id=user_id, organization_id=org_id, is_superuser=False)
    captured_profiles = []

    async def capture_tools(profile, *, caller_user_id=None):
        captured_profiles.append((profile, caller_user_id))
        return []

    executor._get_agent_tools = AsyncMock(side_effect=capture_tools)
    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._record_ai_usage = AsyncMock()
    executor._build_message_history = AsyncMock(
        return_value=[
            LLMMessage(role="system", content="Use Bifrost"),
            LLMMessage(role="user", content="List my options"),
        ]
    )
    client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-model", api_key="test-key")
    )

    with patch(
        "src.services.agent_executor.get_llm_client",
        new_callable=AsyncMock,
        return_value=client,
    ), patch(
        "src.services.agent_executor.create_agent_model",
        return_value=CountingTestModel(
            call_tools=[],
            custom_output_text="Use the gateway",
        ),
    ):
        _ = [
            chunk
            async for chunk in executor.chat(
                None,
                conversation,
                "List my options",
                user=user,
                stream=False,
                enable_routing=False,
            )
        ]

    profile, caller_user_id = captured_profiles[0]
    assert isinstance(profile, NativeChatExecutionProfile)
    assert profile.organization_id == org_id
    assert profile.owner_user_id == user_id
    assert "bifrost_search_capabilities" in profile.system_tools
    assert caller_user_id == conversation.user_id


@pytest.mark.asyncio
async def test_agentless_chat_uses_selected_authorization_boundary(
    executor: AgentExecutor,
    conversation,
) -> None:
    user_id = uuid4()
    home_org_id = uuid4()
    selected_org_id = uuid4()
    user = MagicMock(
        user_id=user_id,
        organization_id=home_org_id,
        is_superuser=True,
        is_external=False,
    )
    captured_profiles = []

    async def capture_tools(profile, *, caller_user_id=None):
        del caller_user_id
        captured_profiles.append(profile)
        return []

    executor._get_agent_tools = AsyncMock(side_effect=capture_tools)
    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._record_ai_usage = AsyncMock()
    executor._build_message_history = AsyncMock(
        return_value=[
            LLMMessage(role="system", content="Use Bifrost"),
            LLMMessage(role="user", content="List customer tools"),
        ]
    )
    client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-model", api_key="test-key")
    )

    with patch(
        "src.services.agent_executor.get_llm_client",
        new_callable=AsyncMock,
        return_value=client,
    ), patch(
        "src.services.agent_executor.create_agent_model",
        return_value=CountingTestModel(
            call_tools=[],
            custom_output_text="Use selected org",
        ),
    ):
        _ = [
            chunk
            async for chunk in executor.chat(
                None,
                conversation,
                "List customer tools",
                user=user,
                authorization_context=_authorization(
                    AuthorizationBoundary.organization(selected_org_id),
                    PLATFORM_SUPERUSER_SCOPE,
                ),
                stream=False,
                enable_routing=False,
            )
        ]

    profile = captured_profiles[0]
    assert isinstance(profile, NativeChatExecutionProfile)
    assert profile.organization_id == selected_org_id
    assert profile.authorization_boundary == f"organization:{selected_org_id}"
    assert profile.gateway_is_platform_admin is False
    assert profile.resource_gate_bypass is True


@pytest.mark.asyncio
async def test_agentless_chat_rejects_managed_organizations_boundary(
    executor: AgentExecutor,
    conversation,
) -> None:
    user = MagicMock(
        user_id=uuid4(),
        organization_id=uuid4(),
        is_superuser=False,
        is_external=False,
    )

    with pytest.raises(ValueError, match="Managed Organizations"):
        _ = [
            chunk
            async for chunk in executor.chat(
                None,
                conversation,
                "List all customers",
                user=user,
                authorization_context=_authorization(
                    AuthorizationBoundary.managed_organizations()
                ),
                stream=False,
                enable_routing=False,
            )
        ]


@pytest.mark.asyncio
async def test_tagged_agent_is_context_not_transitive_tool_grant(
    executor: AgentExecutor,
    conversation,
) -> None:
    selected = MagicMock()
    selected.id = uuid4()
    selected.name = "Tagged Specialist"
    selected.tools = [MagicMock(name="dangerous_direct_tool")]
    user = MagicMock(user_id=uuid4(), organization_id=uuid4(), is_superuser=False)
    captured_profiles = []

    async def capture_tools(profile, *, caller_user_id=None):
        del caller_user_id
        captured_profiles.append(profile)
        return []

    executor._get_agent_tools = AsyncMock(side_effect=capture_tools)
    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._record_ai_usage = AsyncMock()
    executor._build_message_history = AsyncMock(
        return_value=[
            LLMMessage(role="system", content="Use Bifrost"),
            LLMMessage(role="user", content="Work as selected"),
        ]
    )
    client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-model", api_key="test-key")
    )

    with patch(
        "src.services.agent_executor.get_llm_client",
        new_callable=AsyncMock,
        return_value=client,
    ), patch(
        "src.services.agent_executor.create_agent_model",
        return_value=CountingTestModel(
            call_tools=[],
            custom_output_text="Use selected context",
        ),
    ):
        _ = [
            chunk
            async for chunk in executor.chat(
                selected,
                conversation,
                "Work as selected",
                user=user,
                stream=False,
                enable_routing=False,
            )
        ]

    profile = captured_profiles[0]
    assert isinstance(profile, NativeChatExecutionProfile)
    assert profile.tools == ()
    assert profile.delegated_agents == ()
    assert str(selected.id) in profile.system_prompt
    assert "bifrost_execute_tool" in profile.system_tools


@pytest.mark.asyncio
async def test_runtime_model_observer_receives_request_lifecycle(
    conversation,
) -> None:
    observer = AsyncMock()
    observed = AgentExecutor(
        MagicMock(),
        runtime_model_event_handler=observer,
    )
    observed._save_message = AsyncMock(side_effect=_saved_message)
    observed._record_ai_usage = AsyncMock()
    observed._build_message_history = AsyncMock(
        return_value=[
            LLMMessage(role="system", content="Be useful"),
            LLMMessage(role="user", content="Hello"),
        ]
    )
    client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-model", api_key="test-key")
    )

    with patch(
        "src.services.agent_executor.get_llm_client",
        new_callable=AsyncMock,
        return_value=client,
    ), patch(
        "src.services.agent_executor.create_agent_model",
        return_value=CountingTestModel(
            call_tools=[],
            custom_output_text="Observed",
        ),
    ):
        _ = [
            chunk
            async for chunk in observed.chat(
                None,
                conversation,
                "Hello",
                stream=True,
                enable_routing=False,
            )
        ]

    event_types = [call.args[0].type for call in observer.await_args_list]
    assert event_types == ["request", "response"]


@pytest.mark.asyncio
async def test_system_tool_context_ignores_legacy_superuser_without_authorization(
    executor: AgentExecutor,
) -> None:
    captured_context = None

    async def fake_tool(context, **_kwargs):
        nonlocal captured_context
        captured_context = context
        return {"ok": True}

    user = MagicMock(id=uuid4(), email="admin@example.com", name="Admin", is_superuser=True)
    conversation = MagicMock(user=user)
    agent = MagicMock()
    agent.organization_id = uuid4()
    agent.bundle_path = None
    agent.id = uuid4()
    agent.solution_id = None
    agent.resource_gate_bypass = False
    agent.authorization_boundary = None
    agent.gateway_is_platform_admin = None

    with patch(
        "src.services.mcp_server.server.get_system_tool_function",
        return_value=fake_tool,
    ):
        result = await executor._execute_system_tool(
            ToolCallRequest(id="call-1", name="bifrost_test_tool", arguments={}),
            agent,
            conversation,
        )

    assert result.error is None
    assert captured_context is not None
    assert captured_context.is_platform_admin is False


@pytest.mark.asyncio
async def test_system_tool_context_uses_canonical_platform_superuser_capability(
    executor: AgentExecutor,
) -> None:
    captured_context = None

    async def fake_tool(context, **_kwargs):
        nonlocal captured_context
        captured_context = context
        return {"ok": True}

    user = MagicMock(id=uuid4(), email="admin@example.com", name="Admin", is_superuser=False)
    conversation = MagicMock(user=user)
    agent = MagicMock()
    agent.organization_id = uuid4()
    agent.bundle_path = None
    agent.id = uuid4()
    agent.solution_id = None
    agent.resource_gate_bypass = False
    agent.authorization_boundary = None
    agent.gateway_is_platform_admin = None

    with patch(
        "src.services.mcp_server.server.get_system_tool_function",
        return_value=fake_tool,
    ):
        result = await executor._execute_system_tool(
            ToolCallRequest(id="call-1", name="bifrost_test_tool", arguments={}),
            agent,
            conversation,
            authorization_context=_authorization(
                AuthorizationBoundary.platform(),
                PLATFORM_SUPERUSER_SCOPE,
            ),
        )

    assert result.error is None
    assert captured_context is not None
    assert captured_context.is_platform_admin is True


@pytest.mark.asyncio
async def test_chat_reapplies_agent_instructions_when_stored_history_exists(
    executor: AgentExecutor,
    conversation,
) -> None:
    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._record_ai_usage = AsyncMock()
    executor._build_message_history = AsyncMock(
        return_value=[
            LLMMessage(role="system", content="Stable triage instructions"),
            LLMMessage(role="user", content="Prior ticket"),
            LLMMessage(role="assistant", content="Prior response"),
            LLMMessage(role="user", content="Current ticket"),
        ]
    )
    client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-model", api_key="test-key")
    )
    model = CapturingTestModel(
        call_tools=[],
        custom_output_text="Current response",
    )

    with patch(
        "src.services.agent_executor.get_llm_client",
        new_callable=AsyncMock,
        return_value=client,
    ), patch(
        "src.services.agent_executor.create_agent_model",
        return_value=model,
    ):
        _ = [
            chunk
            async for chunk in executor.chat(
                None,
                conversation,
                "Current ticket",
                stream=False,
                enable_routing=False,
            )
        ]

    instructions = [
        message.instructions
        for message in model.requests[0]
        if isinstance(message, ModelRequest) and message.instructions
    ]
    assert len(instructions) == 1
    assert instructions[0].startswith("Stable triage instructions\n\n")
    assert "shared artifact workspace" in instructions[0]


@pytest.mark.asyncio
async def test_chat_maps_pydantic_tool_events_to_existing_bifrost_contract(
    executor: AgentExecutor,
    conversation,
) -> None:
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Ticket Agent"
    agent.max_iterations = 5
    agent.max_token_budget = 10_000
    agent.llm_model = None
    agent.llm_max_tokens = None
    agent.organization_id = uuid4()

    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._update_tool_call_message = AsyncMock()
    executor._record_ai_usage = AsyncMock()
    executor._get_agent_tools = AsyncMock(
        return_value=[
            ToolDefinition(
                name="get_ticket",
                description="Get a ticket",
                parameters={
                    "type": "object",
                    "properties": {"ticket_id": {"type": "integer"}},
                    "required": ["ticket_id"],
                },
            )
        ]
    )
    executor._build_message_history = AsyncMock(
        return_value=[
            LLMMessage(role="system", content="Use tools"),
            LLMMessage(role="user", content="Check the ticket"),
        ]
    )
    executor._execute_tool = AsyncMock(
        return_value=ToolResult(
            tool_call_id="ignored-by-runtime-mapping",
            tool_name="get_ticket",
            result={"status": "open"},
            duration_ms=5,
        )
    )
    client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-model", api_key="test-key")
    )

    with patch(
        "src.services.agent_executor.get_llm_client",
        new_callable=AsyncMock,
        return_value=client,
    ), patch(
        "src.services.agent_executor.create_agent_model",
        return_value=CountingTestModel(
            call_tools=["get_ticket"],
            custom_output_text="Ticket checked",
        ),
    ):
        chunks = [
            chunk
            async for chunk in executor.chat(
                agent,
                conversation,
                "Check the ticket",
                stream=True,
                enable_routing=False,
            )
        ]

    assert any(chunk.type == "tool_call" for chunk in chunks)
    assert any(chunk.type == "tool_progress" for chunk in chunks)
    assert any(chunk.type == "tool_result" for chunk in chunks), [
        (chunk.type, chunk.error) for chunk in chunks
    ]
    assert next(chunk for chunk in chunks if chunk.type == "done").content == "Ticket checked"
    executor._execute_tool.assert_awaited_once()
