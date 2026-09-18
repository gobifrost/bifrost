"""Executor-level tests for the empty-output circuit breaker.

Drives the real PydanticAgent loop (via LegacyMockModel) with blank and
repetitive no-tool completions to prove single-fallback-then-handoff end to
end, including UsageLimits ledger charging and the durable budget_warning.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.unit.services.agent_runtime_fakes import LegacyMockModel
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig, LLMResponse


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock(return_value=None)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=mock_ctx)
    factory._mock_session = session
    return factory


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Test Agent"
    agent.system_prompt = "You are a test agent."
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


def _blank(output_tokens: int = 50) -> LLMResponse:
    """Blank no-tool response staying inside the test budget's headroom."""
    return LLMResponse(
        content=None,
        tool_calls=None,
        finish_reason="length",
        input_tokens=100,
        output_tokens=output_tokens,
    )


def _incident_blank() -> LLMResponse:
    """Incident shape: one blank response billing 131072 output tokens."""
    return LLMResponse(
        content=None,
        tool_calls=None,
        finish_reason="length",
        input_tokens=100,
        output_tokens=131072,
    )


@pytest.mark.asyncio
@patch("src.services.execution.autonomous_agent_executor.create_agent_model")
@patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
async def test_blank_responses_fallback_once_then_hand_off_with_usage(
    mock_resolve_tools, mock_get_llm, mock_session, mock_agent
):
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(side_effect=[_blank(), _blank()])
    mock_get_llm.return_value = LegacyMockModel(mock_llm)

    with patch(
        "src.services.execution.autonomous_agent_executor.get_llm_config",
        new_callable=AsyncMock,
        return_value=LLMConfig(
            provider="openai", model="test-model", api_key="test-key"
        ),
    ):
        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(agent=mock_agent, run_id=str(uuid4()))

    # One bounded fallback happened (two billed attempts), then a handoff —
    # the run completes durably instead of stalling with empty output.
    assert mock_llm.complete.call_count == 2
    assert result["status"] == "completed"
    assert result["output"] is not None
    assert "human" in str(result["output"])
    assert result["iterations_used"] == 2
    # Both attempts — including the rejected blank — are ledger-charged.
    assert result["tokens_used"] == 2 * (100 + 50)

    warnings = [
        step
        for step in executor._pending_steps
        if step["type"] == "budget_warning"
    ]
    assert len(warnings) == 1
    assert warnings[0]["content"]["reason"] == "empty_model_output"
    assert warnings[0]["content"]["fallbacks_used"] == 1


@pytest.mark.asyncio
@patch("src.services.execution.autonomous_agent_executor.create_agent_model")
@patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
async def test_incident_shaped_blank_ends_durably_over_budget(
    mock_resolve_tools, mock_get_llm, mock_session, mock_agent
):
    """A single 131072-token blank exceeds the budget: no silent stall.

    The pre-request UsageLimits guard rejects the guard's retry before a
    second 131K call can burn, and the run ends budget_exceeded with a
    human handoff naming the blank response — usage still charged.
    """
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(side_effect=[_incident_blank()])
    mock_get_llm.return_value = LegacyMockModel(mock_llm)

    with patch(
        "src.services.execution.autonomous_agent_executor.get_llm_config",
        new_callable=AsyncMock,
        return_value=LLMConfig(
            provider="openai", model="test-model", api_key="test-key"
        ),
    ):
        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(agent=mock_agent, run_id=str(uuid4()))

    assert mock_llm.complete.call_count == 1  # no second 131K call
    assert result["status"] == "budget_exceeded"
    assert result["output"] is not None
    assert "empty response" in str(result["output"])
    assert "human" in str(result["output"])
    assert result["tokens_used"] == 100 + 131072
    warnings = [
        step
        for step in executor._pending_steps
        if step["type"] == "budget_warning"
    ]
    assert len(warnings) == 1
    assert warnings[0]["content"]["reason"] == "runtime_budget_exceeded"
    assert warnings[0]["content"]["empty_response_seen"] is True


@pytest.mark.asyncio
@patch("src.services.execution.autonomous_agent_executor.create_agent_model")
@patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
async def test_repetitive_no_tool_output_hands_off_after_one_retry(
    mock_resolve_tools, mock_get_llm, mock_session, mock_agent
):
    """One echoing no-tool completion retries once (bounded), then hands off."""
    mock_resolve_tools.return_value = ([], {})

    def _echo() -> LLMResponse:
        return LLMResponse(
            content="Contact the ticket owner for next steps. " * 200,
            tool_calls=None,
            finish_reason="length",
            input_tokens=100,
            output_tokens=500,
        )

    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(side_effect=[_echo(), _echo()])
    mock_get_llm.return_value = LegacyMockModel(mock_llm)

    with patch(
        "src.services.execution.autonomous_agent_executor.get_llm_config",
        new_callable=AsyncMock,
        return_value=LLMConfig(
            provider="openai", model="test-model", api_key="test-key"
        ),
    ):
        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(agent=mock_agent, run_id=str(uuid4()))

    assert mock_llm.complete.call_count == 2
    assert result["status"] == "completed"
    assert "repeated" in str(result["output"] or "")
    assert result["iterations_used"] == 2
    warnings = [
        step
        for step in executor._pending_steps
        if step["type"] == "budget_warning"
    ]
    assert len(warnings) == 1
    assert warnings[0]["content"]["reason"] == "empty_model_output"
