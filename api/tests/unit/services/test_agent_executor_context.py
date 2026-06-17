"""
Unit tests for AgentExecutor message-shaping helpers.

Token estimation and the tool_use/tool_result fixups. Context compaction
itself lives in tests/unit/services/test_chat_compaction.py (M5 moved the
prune/summarize logic into src/services/chat_compaction.py).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.agent_executor import AgentExecutor
from src.services.llm import LLMMessage, ToolCallRequest



@pytest.fixture
def mock_session_factory():
    """Mock database session factory."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


@pytest.fixture
def executor(mock_session_factory):
    """Create an AgentExecutor instance with mocked session factory."""
    return AgentExecutor(mock_session_factory)


class TestTokenEstimation:
    """Test token estimation functionality."""

    def test_estimate_tokens_empty_messages(self, executor):
        """Test token estimation with empty message list."""
        result = executor._estimate_tokens([])
        assert result == 0

    def test_estimate_tokens_text_only(self, executor):
        """Test token estimation with text content only."""
        messages = [
            LLMMessage(role="system", content="Hello world"),  # 11 chars = ~2 tokens
            LLMMessage(role="user", content="How are you?"),  # 12 chars = ~3 tokens
        ]
        result = executor._estimate_tokens(messages)
        # (11 + 12) // 4 = 5 tokens
        assert result == 5

    def test_estimate_tokens_with_tool_calls(self, executor):
        """Test token estimation includes tool call JSON."""
        messages = [
            LLMMessage(
                role="assistant",
                content="Let me help",
                tool_calls=[
                    ToolCallRequest(
                        id="call_123",
                        name="search",
                        arguments={"query": "test"},
                    )
                ],
            ),
        ]
        result = executor._estimate_tokens(messages)
        # Should include both content and tool call JSON
        assert result > 0
        # Should be more than just the text content
        text_only = len("Let me help") // 4
        assert result > text_only

    def test_estimate_tokens_none_content(self, executor):
        """Test token estimation handles None content."""
        messages = [
            LLMMessage(role="assistant", content=None, tool_calls=None),
        ]
        result = executor._estimate_tokens(messages)
        assert result == 0

    def test_estimate_tokens_large_content(self, executor):
        """Test token estimation with large content."""
        # Create ~100K characters (should be ~25K tokens)
        large_content = "x" * 100_000
        messages = [LLMMessage(role="user", content=large_content)]
        result = executor._estimate_tokens(messages)
        assert result == 25_000  # 100000 // 4


class TestFixInterleavedMessages:
    """Test reordering of user messages wedged between tool_use and tool_result."""

    def test_no_interleaving_unchanged(self, executor):
        """Normal sequence passes through unchanged."""
        messages = [
            LLMMessage(role="user", content="Search for X"),
            LLMMessage(
                role="assistant",
                content="Searching...",
                tool_calls=[ToolCallRequest(id="c1", name="search", arguments={})],
            ),
            LLMMessage(role="tool", content="Result", tool_call_id="c1", tool_name="search"),
            LLMMessage(role="assistant", content="Found it"),
        ]
        result = executor._fix_interleaved_messages(messages)
        assert result == messages

    def test_user_between_tool_use_and_result(self, executor):
        """User message wedged between tool_use and tool_result gets moved after."""
        messages = [
            LLMMessage(role="user", content="Search for X"),
            LLMMessage(
                role="assistant",
                content="Searching...",
                tool_calls=[ToolCallRequest(id="c1", name="search", arguments={})],
            ),
            LLMMessage(role="user", content="Actually nevermind"),  # interleaved
            LLMMessage(role="tool", content="Result", tool_call_id="c1", tool_name="search"),
            LLMMessage(role="assistant", content="Found it"),
        ]
        result = executor._fix_interleaved_messages(messages)
        assert len(result) == 5
        # tool_result should immediately follow tool_use
        assert result[1].role == "assistant"
        assert result[1].tool_calls is not None
        assert result[2].role == "tool"
        assert result[2].tool_call_id == "c1"
        # user message moved after tool result
        assert result[3].role == "user"
        assert result[3].content == "Actually nevermind"
        assert result[4].role == "assistant"

    def test_multiple_tool_results_with_interleaved_user(self, executor):
        """User message between multi-tool assistant and results."""
        messages = [
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCallRequest(id="c1", name="tool_a", arguments={}),
                    ToolCallRequest(id="c2", name="tool_b", arguments={}),
                ],
            ),
            LLMMessage(role="user", content="Oops"),  # interleaved
            LLMMessage(role="tool", content="Result A", tool_call_id="c1", tool_name="tool_a"),
            LLMMessage(role="tool", content="Result B", tool_call_id="c2", tool_name="tool_b"),
            LLMMessage(role="assistant", content="Done"),
        ]
        result = executor._fix_interleaved_messages(messages)
        # assistant, tool_a, tool_b, user, assistant
        assert result[0].role == "assistant"
        assert result[1].role == "tool" and result[1].tool_call_id == "c1"
        assert result[2].role == "tool" and result[2].tool_call_id == "c2"
        assert result[3].role == "user" and result[3].content == "Oops"
        assert result[4].role == "assistant"

    def test_no_tool_calls_unchanged(self, executor):
        """Plain conversation without tools passes through unchanged."""
        messages = [
            LLMMessage(role="user", content="Hello"),
            LLMMessage(role="assistant", content="Hi"),
            LLMMessage(role="user", content="Bye"),
        ]
        result = executor._fix_interleaved_messages(messages)
        assert result == messages


class TestFixDanglingToolCalls:
    """Test dangling tool_call prevention."""

    def test_no_dangling(self, executor):
        """Messages with matching pairs are unchanged."""
        messages = [
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[ToolCallRequest(id="c1", name="search", arguments={})],
            ),
            LLMMessage(role="tool", content="Result", tool_call_id="c1", tool_name="search"),
        ]
        result = executor._fix_dangling_tool_calls(messages)
        assert len(result) == 2
        assert result[1].content == "Result"

    def test_injects_missing_tool_result(self, executor):
        """Missing tool result gets a placeholder injected."""
        messages = [
            LLMMessage(
                role="assistant",
                content="Let me search",
                tool_calls=[ToolCallRequest(id="c1", name="search", arguments={"q": "test"})],
            ),
            # No tool result follows!
            LLMMessage(role="user", content="What happened?"),
        ]
        result = executor._fix_dangling_tool_calls(messages)
        assert len(result) == 3
        assert result[1].role == "tool"
        assert result[1].tool_call_id == "c1"
        assert result[1].tool_name == "search"
        assert "[Tool execution was interrupted]" in result[1].content
        assert result[2].role == "user"

    def test_partial_results(self, executor):
        """Only missing tool results get placeholders, existing ones are kept."""
        messages = [
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCallRequest(id="c1", name="tool_a", arguments={}),
                    ToolCallRequest(id="c2", name="tool_b", arguments={}),
                ],
            ),
            LLMMessage(role="tool", content="Result A", tool_call_id="c1", tool_name="tool_a"),
            # c2 is missing
        ]
        result = executor._fix_dangling_tool_calls(messages)
        assert len(result) == 3
        # c1 result intact
        assert result[1].tool_call_id == "c1"
        assert result[1].content == "Result A"
        # c2 gets placeholder
        assert result[2].tool_call_id == "c2"
        assert result[2].tool_name == "tool_b"
        assert "[Tool execution was interrupted]" in result[2].content

    def test_no_tool_calls(self, executor):
        """Messages without tool_calls are unchanged."""
        messages = [
            LLMMessage(role="user", content="Hello"),
            LLMMessage(role="assistant", content="Hi"),
        ]
        result = executor._fix_dangling_tool_calls(messages)
        assert result == messages


