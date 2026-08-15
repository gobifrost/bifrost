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

from src.models.contracts.agents import ToolResult
from src.services.agent_executor import AgentExecutor
from src.services.llm import LLMMessage, ToolDefinition
from src.services.llm.base import LLMConfig
from src.services.llm.pydantic_client import PydanticAIClient
from src.services.llm_config_service import LLMProviderConfig


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
    config = LLMProviderConfig(provider="openai", model="test-model")
    with patch(
        "src.services.llm_config_service.LLMConfigService.get_config",
        new=AsyncMock(return_value=config),
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
    assert instructions == ["Stable triage instructions"]


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
