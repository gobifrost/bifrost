"""
Unit tests for AnthropicClient._convert_messages.

Verifies correct conversion of LLMMessage sequences to Anthropic API format,
especially tool_use/tool_result pairing.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.llm import LLMMessage, ToolCallRequest


@pytest.fixture
def client():
    """Create an AnthropicClient with mocked dependencies."""
    with patch("src.services.llm.anthropic_client.AsyncAnthropic"):
        from src.services.llm.anthropic_client import AnthropicClient

        config = MagicMock()
        config.api_key = "test-key"
        config.model = "claude-sonnet-4-20250514"
        config.max_tokens = 1024
        return AnthropicClient(config)


class TestConvertMessages:
    """Test _convert_messages formatting for Anthropic API."""

    def test_basic_conversation(self, client):
        """Simple user/assistant messages convert correctly."""
        messages = [
            LLMMessage(role="system", content="You are helpful"),
            LLMMessage(role="user", content="Hello"),
            LLMMessage(role="assistant", content="Hi there"),
        ]
        system, result = client._convert_messages(messages)
        assert system == "You are helpful"
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_single_tool_call_and_result(self, client):
        """Single tool_use + tool_result pair converts correctly."""
        messages = [
            LLMMessage(role="system", content="System"),
            LLMMessage(role="user", content="Search for X"),
            LLMMessage(
                role="assistant",
                content="Searching...",
                tool_calls=[ToolCallRequest(id="c1", name="search", arguments={"q": "X"})],
            ),
            LLMMessage(role="tool", content="Found X", tool_call_id="c1", tool_name="search"),
        ]
        system, result = client._convert_messages(messages)
        assert len(result) == 3
        # assistant has tool_use
        assert result[1]["role"] == "assistant"
        # tool result is a user message
        assert result[2]["role"] == "user"
        content = result[2]["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "c1"

    def test_multiple_tool_results_merged(self, client):
        """Multiple consecutive tool results merge into a single user message."""
        messages = [
            LLMMessage(role="system", content="System"),
            LLMMessage(role="user", content="Do two things"),
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCallRequest(id="c1", name="tool_a", arguments={}),
                    ToolCallRequest(id="c2", name="tool_b", arguments={}),
                ],
            ),
            LLMMessage(role="tool", content="Result A", tool_call_id="c1", tool_name="tool_a"),
            LLMMessage(role="tool", content="Result B", tool_call_id="c2", tool_name="tool_b"),
        ]
        system, result = client._convert_messages(messages)

        # Should be: user, assistant, user (with both tool_results)
        assert len(result) == 3
        assert result[2]["role"] == "user"
        content = result[2]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "c1"
        assert content[1]["type"] == "tool_result"
        assert content[1]["tool_use_id"] == "c2"

    def test_tool_results_not_merged_across_user_message(self, client):
        """Tool results separated by a user message are NOT merged."""
        messages = [
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[ToolCallRequest(id="c1", name="t", arguments={})],
            ),
            LLMMessage(role="tool", content="Result 1", tool_call_id="c1", tool_name="t"),
            LLMMessage(role="user", content="Now do another thing"),
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[ToolCallRequest(id="c2", name="t", arguments={})],
            ),
            LLMMessage(role="tool", content="Result 2", tool_call_id="c2", tool_name="t"),
        ]
        system, result = client._convert_messages(messages)

        # assistant, user(tool_result c1), user(text), assistant, user(tool_result c2)
        assert len(result) == 5
        # First tool result
        assert isinstance(result[1]["content"], list)
        assert len(result[1]["content"]) == 1
        # User text message
        assert result[2]["content"] == "Now do another thing"
        # Second tool result
        assert isinstance(result[4]["content"], list)
        assert len(result[4]["content"]) == 1

    def test_three_tool_results_merged(self, client):
        """Three consecutive tool results all merge into one user message."""
        messages = [
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCallRequest(id="c1", name="a", arguments={}),
                    ToolCallRequest(id="c2", name="b", arguments={}),
                    ToolCallRequest(id="c3", name="c", arguments={}),
                ],
            ),
            LLMMessage(role="tool", content="R1", tool_call_id="c1", tool_name="a"),
            LLMMessage(role="tool", content="R2", tool_call_id="c2", tool_name="b"),
            LLMMessage(role="tool", content="R3", tool_call_id="c3", tool_name="c"),
        ]
        system, result = client._convert_messages(messages)
        assert len(result) == 2  # assistant + single user with 3 tool_results
        content = result[1]["content"]
        assert isinstance(content, list)
        assert len(content) == 3
        assert [b["tool_use_id"] for b in content] == ["c1", "c2", "c3"]


class TestComplete:
    """complete() must stream internally so a large max_tokens never trips the
    Anthropic SDK's non-streaming ">10 minute" guard (which otherwise breaks run
    summarization whenever the admin-configured max_tokens is set above ~21k)."""

    @staticmethod
    def _final_message():
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "hello world"
        usage = MagicMock()
        usage.input_tokens = 12
        usage.output_tokens = 3
        msg = MagicMock()
        msg.content = [text_block]
        msg.stop_reason = "end_turn"
        msg.usage = usage
        msg.model = "claude-sonnet-4-20250514"
        return msg

    def _mock_stream(self, client, final_message):
        """Wire client.messages.stream to an async CM yielding final_message."""
        from unittest.mock import AsyncMock

        stream_obj = MagicMock()
        stream_obj.get_final_message = AsyncMock(return_value=final_message)

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=stream_obj)
        cm.__aexit__ = AsyncMock(return_value=False)

        client.client.messages.stream = MagicMock(return_value=cm)
        client.client.messages.create = MagicMock(
            side_effect=AssertionError("complete() must not call messages.create")
        )
        return cm

    @pytest.mark.asyncio
    async def test_complete_uses_streaming(self, client):
        cm = self._mock_stream(client, self._final_message())

        result = await client.complete(
            [LLMMessage(role="user", content="hi")], max_tokens=64000
        )

        # Streamed, not created — this is what avoids the 10-minute guard.
        client.client.messages.stream.assert_called_once()
        client.client.messages.create.assert_not_called()
        # max_tokens is forwarded verbatim; the guard would have fired on create().
        assert client.client.messages.stream.call_args.kwargs["max_tokens"] == 64000
        cm.__aenter__.assert_awaited_once()

        # Non-streaming return contract is preserved.
        assert result.content == "hello world"
        assert result.finish_reason == "end_turn"
        assert result.input_tokens == 12
        assert result.output_tokens == 3

    @pytest.mark.asyncio
    async def test_complete_assembles_tool_calls(self, client):
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "toolu_1"
        tool_block.name = "search"
        tool_block.input = {"q": "cats"}
        final = self._final_message()
        final.content = [tool_block]
        final.stop_reason = "tool_use"
        self._mock_stream(client, final)

        result = await client.complete([LLMMessage(role="user", content="hi")])

        assert result.tool_calls is not None
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[0].arguments == {"q": "cats"}
