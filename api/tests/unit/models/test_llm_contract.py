"""Tests for LLM admin request contracts."""

from src.models.contracts.llm import LLMTestRequest


def test_llm_test_request_does_not_require_model():
    """Connection Test lists provider models, so the caller need not guess one."""
    request = LLMTestRequest(
        provider="openai",
        api_key="sk-test-key",
        endpoint="https://example.openai.azure.com/openai/v1",
    )

    assert request.model is None
