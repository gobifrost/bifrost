"""Chat contract coverage for the Pydantic AI loop."""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from src.models.contracts.agents import ToolResult
from src.models.contracts.artifacts import ModelCapabilities
from src.models.orm.ai_models import AIModelProfile
from src.services.agent_executor import AgentExecutor
from src.services.llm import LLMMessage, ToolDefinition
from src.services.llm.base import LLMConfig
from src.services.llm.pydantic_client import PydanticAIClient
from src.services.model_capabilities import manual_capabilities


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
def configured_chat_model():
    """Keep these runtime-contract tests focused on the Pydantic event loop."""
    profile = MagicMock(spec=AIModelProfile)
    profile.id = uuid4()
    profile.name = "Everyday"
    config = LLMConfig(provider="openai", model="test-model", api_key="test-key")
    capabilities = manual_capabilities(
        provider="openai",
        model="test-model",
        endpoint=None,
        image_input=False,
        pdf_input=False,
        tool_calling=True,
    )
    with (
        patch(
            "src.services.agent_executor.AIModelService.resolve_chat_profile",
            new=AsyncMock(return_value=(profile, config, capabilities)),
        ),
        patch(
            "src.services.agent_executor.AIModelService.has_assignment",
            new=AsyncMock(return_value=False),
        ),
    ):
        yield


@pytest.fixture
def conversation():
    value = MagicMock()
    value.id = uuid4()
    value.user_id = uuid4()
    return value


def _saved_message(*args, **kwargs):
    return MagicMock(id=kwargs.get("message_id") or uuid4())


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
        return_value=CountingTestModel(custom_output_text="Hello from Pydantic"),
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
    executor._record_ai_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_resolves_explicit_model_profile_id(
    executor: AgentExecutor,
    conversation,
) -> None:
    profile_id = uuid4()
    profile = MagicMock(spec=AIModelProfile)
    profile.id = profile_id
    profile.name = "Pro"
    resolver = AsyncMock(
        return_value=(
            profile,
            LLMConfig(provider="openai", model="pro-model", api_key="test-key"),
            manual_capabilities(
                provider="openai",
                model="pro-model",
                endpoint=None,
                image_input=False,
                pdf_input=False,
                tool_calling=True,
            ),
        )
    )
    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._record_ai_usage = AsyncMock()
    executor._build_message_history = AsyncMock(return_value=[LLMMessage(role="user", content="Hello")])
    client = PydanticAIClient(LLMConfig(provider="openai", model="pro-model", api_key="test-key"))

    with (
        patch("src.services.agent_executor.AIModelService.resolve_chat_profile", resolver),
        patch("src.services.agent_executor.get_llm_client", new=AsyncMock(return_value=client)) as client_factory,
        patch(
            "src.services.agent_executor.create_agent_model",
            return_value=CountingTestModel(custom_output_text="Hello"),
        ),
    ):
        _ = [
            chunk
            async for chunk in executor.chat(
                None,
                conversation,
                "Hello",
                stream=False,
                enable_routing=False,
                model_profile_id=profile_id,
            )
        ]

    resolver.assert_awaited_once_with(profile_id)
    client_factory.assert_awaited_once()
    assert client_factory.await_args.kwargs["profile_id"] == profile_id


@pytest.mark.asyncio
async def test_chat_omitted_model_profile_uses_default_resolution(
    executor: AgentExecutor,
    conversation,
) -> None:
    profile = MagicMock(spec=AIModelProfile)
    profile.id = uuid4()
    profile.name = "Default"
    resolver = AsyncMock(
        return_value=(
            profile,
            LLMConfig(provider="openai", model="test-model", api_key="test-key"),
            manual_capabilities(
                provider="openai",
                model="test-model",
                endpoint=None,
                image_input=False,
                pdf_input=False,
                tool_calling=True,
            ),
        )
    )
    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._record_ai_usage = AsyncMock()
    executor._build_message_history = AsyncMock(return_value=[LLMMessage(role="user", content="Hello")])
    client = PydanticAIClient(LLMConfig(provider="openai", model="test-model", api_key="test-key"))

    with patch(
        "src.services.agent_executor.AIModelService.resolve_chat_profile",
        resolver,
    ), patch(
        "src.services.agent_executor.get_llm_client",
        new=AsyncMock(return_value=client),
    ), patch(
        "src.services.agent_executor.create_agent_model",
        return_value=CountingTestModel(custom_output_text="Hello"),
    ):
        _ = [
            chunk
            async for chunk in executor.chat(
                None,
                conversation,
                "Hello",
                stream=False,
                enable_routing=False,
            )
        ]

    resolver.assert_awaited_once_with(None)


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
    model = CapturingTestModel(custom_output_text="Current response")

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
async def test_unknown_capabilities_still_offer_agent_tools(
    executor: AgentExecutor,
    conversation,
) -> None:
    captured: dict[str, Any] = {}

    class FakeRunStreamEvents:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakePydanticAgent:
        def __init__(self, model, **kwargs):
            captured["toolsets"] = kwargs["toolsets"]

        def run_stream_events(self, *args, **kwargs):
            return FakeRunStreamEvents()

    profile = MagicMock(spec=AIModelProfile)
    profile.id = uuid4()
    profile.name = "Unknown"
    config = LLMConfig(provider="openai", model="test-model", api_key="test-key")
    capabilities = ModelCapabilities(source="unknown", tool_calling=False)
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Test Agent"
    agent.organization_id = None
    agent.max_iterations = None
    agent.max_token_budget = None
    agent.llm_max_tokens = None

    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._record_ai_usage = AsyncMock()
    executor._build_message_history = AsyncMock(
        return_value=[
            LLMMessage(role="system", content="Be useful"),
            LLMMessage(role="user", content="Hello"),
        ]
    )
    executor._get_agent_tools = AsyncMock(
        return_value=[
            ToolDefinition(
                name="wf_test",
                description="Test",
                parameters={"type": "object"},
            )
        ]
    )

    llm_client = MagicMock()
    llm_client.config = LLMConfig(
        provider="openai",
        model="test-model",
        api_key="test-key",
    )
    llm_client.provider_name = "openrouter"

    with patch(
        "src.services.agent_executor.AIModelService.resolve_chat_profile",
        new=AsyncMock(return_value=(profile, config, capabilities)),
    ), patch(
        "src.services.agent_executor.get_llm_client",
        new=AsyncMock(return_value=llm_client),
    ), patch(
        "src.services.agent_executor.create_agent_model",
        return_value=CountingTestModel(custom_output_text="Hello from Pydantic"),
    ), patch(
        "src.services.agent_executor.PydanticAgent",
        new=FakePydanticAgent,
    ):
        chunks = [
            chunk
            async for chunk in executor.chat(
                agent,
                conversation,
                "Hello",
                stream=False,
                enable_routing=False,
            )
        ]

    assert any(chunk.type == "done" for chunk in chunks)
    toolsets = captured["toolsets"]
    assert len(toolsets) == 1
    assert toolsets[0]._definitions[0].name == "wf_test"


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
