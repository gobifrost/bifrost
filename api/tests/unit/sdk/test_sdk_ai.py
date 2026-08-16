"""
Unit tests for Bifrost AI SDK module.

Tests message building and structured output parsing utilities.
Uses mocked dependencies for fast, isolated testing.
"""

import json
from unittest.mock import AsyncMock
import pytest
from pydantic import BaseModel


class SampleResponse(BaseModel):
    """Sample Pydantic model for structured output tests."""
    answer: str
    confidence: float


class TestAIBuildMessages:
    """Test message building utility functions."""

    def test_build_messages_with_prompt_only(self):
        """Test _build_messages with only a prompt."""
        from bifrost.ai import _build_messages

        result = _build_messages("Hello!", None, None)

        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "Hello!"}

    def test_build_messages_with_system_and_prompt(self):
        """Test _build_messages with system and prompt."""
        from bifrost.ai import _build_messages

        result = _build_messages("Hello!", None, "You are helpful.")

        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are helpful."}
        assert result[1] == {"role": "user", "content": "Hello!"}

    def test_build_messages_with_messages_list(self):
        """Test _build_messages with message list."""
        from bifrost.ai import _build_messages

        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hi!"},
        ]
        result = _build_messages(None, messages, None)

        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "Be helpful."}
        assert result[1] == {"role": "user", "content": "Hi!"}

    def test_build_messages_with_system_overrides_messages_system(self):
        """Test _build_messages - system param replaces system in messages."""
        from bifrost.ai import _build_messages

        messages = [
            {"role": "system", "content": "Original system."},
            {"role": "user", "content": "Hi!"},
        ]
        result = _build_messages(None, messages, "New system.")

        # System from parameter should be first, original system should be filtered
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "New system."}
        assert result[1] == {"role": "user", "content": "Hi!"}


class TestAIStructuredOutput:
    """Test structured output parsing."""

    def test_parse_structured_response_plain_json(self):
        """Test parsing plain JSON response."""
        from bifrost.ai import _parse_structured_response

        content = '{"answer": "test", "confidence": 0.9}'
        result = _parse_structured_response(content, SampleResponse)

        assert isinstance(result, SampleResponse)
        assert result.answer == "test"
        assert result.confidence == 0.9

    def test_parse_structured_response_markdown_code_block(self):
        """Test parsing JSON in markdown code block."""
        from bifrost.ai import _parse_structured_response

        content = '''```json
{"answer": "test", "confidence": 0.9}
```'''
        result = _parse_structured_response(content, SampleResponse)

        assert isinstance(result, SampleResponse)
        assert result.answer == "test"
        assert result.confidence == 0.9

    def test_parse_structured_response_invalid_json_raises(self):
        """Test parsing invalid JSON raises error."""
        from bifrost.ai import _parse_structured_response

        content = "not valid json"

        with pytest.raises(json.JSONDecodeError):
            _parse_structured_response(content, SampleResponse)


@pytest.mark.asyncio
async def test_encode_input_files_resolves_artifact_refs(monkeypatch):
    from bifrost.ai import _encode_input_files
    from bifrost.artifacts import artifacts
    from bifrost.models import ArtifactRef

    read = AsyncMock(return_value=b"%PDF-input")
    monkeypatch.setattr(artifacts, "read", read)
    ref = ArtifactRef(
        id="artifact-input",
        filename="input.pdf",
        content_type="application/pdf",
        size_bytes=10,
    )

    encoded = await _encode_input_files([ref])

    assert encoded == [
        {
            "filename": "input.pdf",
            "content_type": "application/pdf",
            "data_base64": "JVBERi1pbnB1dA==",
        }
    ]
    read.assert_awaited_once_with(ref)

