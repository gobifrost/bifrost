"""History-repair tests retained at the Bifrost/Pydantic boundary.

Token estimation, summarization, and tool-output pruning are intentionally not
tested here anymore: Pydantic Harness owns those policies and its configured
capability contract is covered by ``test_agent_runtime.py``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.agent_executor import AgentExecutor
from src.services.llm import LLMMessage, ToolCallRequest


@pytest.fixture
def executor() -> AgentExecutor:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return AgentExecutor(factory)


def test_interleaved_user_message_moves_after_tool_result(executor: AgentExecutor) -> None:
    messages = [
        LLMMessage(
            role="assistant",
            content="Searching",
            tool_calls=[ToolCallRequest(id="c1", name="search", arguments={})],
        ),
        LLMMessage(role="user", content="One more detail"),
        LLMMessage(role="tool", content="Result", tool_call_id="c1", tool_name="search"),
    ]

    repaired = executor._fix_interleaved_messages(messages)

    assert [message.role for message in repaired] == ["assistant", "tool", "user"]
    assert repaired[1].tool_call_id == "c1"


def test_missing_tool_result_gets_interrupted_placeholder(executor: AgentExecutor) -> None:
    messages = [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[ToolCallRequest(id="c1", name="search", arguments={})],
        ),
        LLMMessage(role="user", content="What happened?"),
    ]

    repaired = executor._fix_dangling_tool_calls(messages)

    assert repaired[1].role == "tool"
    assert repaired[1].tool_call_id == "c1"
    assert repaired[1].content == "[Tool execution was interrupted]"
