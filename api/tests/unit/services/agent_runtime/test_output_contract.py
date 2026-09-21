"""Invocation-owned output contracts: validate, correct once, or fail."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from src.models.contracts.agent_runs import AgentRunResponse
from src.services.agent_runtime.output_contract import (
    ContractError,
    contract_correction_prompt,
    correction_allowed,
    parse_final_output,
    validate_output,
    validate_output_schema,
)
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig, ToolCallRequest

SCHEMA = {
    "type": "object",
    "properties": {"ticket_id": {"type": "integer"}},
    "required": ["ticket_id"],
}


class TestContractPrimitives:
    def test_valid_schema_passes(self):
        validate_output_schema(SCHEMA)
        validate_output_schema(None)

    def test_invalid_schema_rejected(self):
        with pytest.raises(ContractError):
            validate_output_schema({"type": "not-a-type"})
        with pytest.raises(ContractError):
            validate_output_schema("nope")  # type: ignore[arg-type]

    def test_parse_and_validate(self):
        parsed, was_json = parse_final_output('{"ticket_id": 7}')
        assert was_json
        assert validate_output(SCHEMA, parsed) == []
        parsed, was_json = parse_final_output("hello")
        assert not was_json
        assert parsed is None

    def test_validation_errors_name_paths(self):
        errors = validate_output(SCHEMA, {"ticket_id": "seven"})
        assert len(errors) == 1
        assert "ticket_id" in errors[0]

    def test_correction_allowed_only_with_remaining_budget(self):
        assert correction_allowed(
            iterations_used=1, max_iterations=5, tokens_used=10, max_tokens=100
        )
        assert not correction_allowed(
            iterations_used=5, max_iterations=5, tokens_used=10, max_tokens=100
        )
        assert not correction_allowed(
            iterations_used=1, max_iterations=5, tokens_used=100, max_tokens=100
        )
        assert correction_allowed(
            iterations_used=0,
            max_iterations=None,
            tokens_used=0,
            max_tokens=None,
        )

    def test_correction_prompt_names_violations(self):
        prompt = contract_correction_prompt(["ticket_id: not an integer"])
        assert "ticket_id" in prompt
        assert "ONLY" in prompt


def _mock_agent():
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Contract Agent"
    agent.system_prompt = "Emit JSON."
    agent.tools = []
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.delegated_agents = []
    agent.roles = []
    agent.max_iterations = 10
    agent.max_token_budget = 50000
    agent.llm_profile_id = None
    agent.llm_max_tokens = None
    agent.organization_id = None
    agent.is_active = True
    return agent


def _mock_session_factory():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock(return_value=None)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_ctx)


def _scripted_model(responses: list[str]):
    calls = {"count": 0}

    def _fn(messages, info):
        text = responses[min(calls["count"], len(responses) - 1)]
        calls["count"] += 1
        return ModelResponse(parts=[TextPart(content=text)], model_name="fake")

    model = FunctionModel(_fn)
    return model, calls


async def _run_with_model(agent, script, output_schema, **overrides):
    model, calls = _scripted_model(script)
    executor = AutonomousAgentExecutor(_mock_session_factory())
    with (
        patch(
            "src.services.execution.autonomous_agent_executor.get_llm_configs",
            new_callable=AsyncMock,
            return_value=[LLMConfig(
                provider="openai", model="test-model", api_key="k"
            )],
        ),
        patch(
            "src.services.agent_runtime.model_factory.create_agent_model",
            return_value=model,
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.resolve_agent_tools",
            new_callable=AsyncMock,
            return_value=([], {}),
        ),
    ):
        result = await executor.run(
            agent,
            input_data={"task": "go"},
            output_schema=output_schema,
            run_id=str(uuid4()),
            **overrides,
        )
    return result, calls


class TestExecutorContract:
    @pytest.mark.asyncio
    async def test_valid_output_completes(self):
        result, calls = await _run_with_model(
            _mock_agent(), ['{"ticket_id": 7}'], SCHEMA
        )
        assert result["status"] == "completed"
        assert result["output"] == {"ticket_id": 7}
        assert result["contract_valid"] is True
        assert result["contract_errors"] == []
        assert calls["count"] == 1

    @pytest.mark.asyncio
    async def test_invalid_output_corrected_once(self):
        result, calls = await _run_with_model(
            _mock_agent(), ['{"ticket_id": "seven"}', '{"ticket_id": 7}'], SCHEMA
        )
        assert result["status"] == "completed"
        assert result["output"] == {"ticket_id": 7}
        assert result["contract_valid"] is True
        assert calls["count"] == 2

    @pytest.mark.asyncio
    async def test_invalid_output_without_budget_fails_closed(self):
        agent = _mock_agent()
        agent.max_iterations = 1
        result, calls = await _run_with_model(
            agent, ['{"ticket_id": "seven"}', '{"ticket_id": 7}'], SCHEMA
        )
        assert result["status"] == "contract_failed"
        assert result["output"] == {"text": '{"ticket_id": "seven"}'}
        assert result["contract_valid"] is False
        assert result["contract_errors"]
        assert calls["count"] == 1

    @pytest.mark.asyncio
    async def test_non_json_output_fails_with_reason(self):
        result, _ = await _run_with_model(
            _mock_agent(), ["first prose", "second prose"], SCHEMA
        )
        assert result["status"] == "contract_failed"
        assert result["output"] == {"text": "second prose"}
        assert result["contract_valid"] is False
        assert any("JSON" in error for error in result["contract_errors"])

    @pytest.mark.asyncio
    async def test_no_schema_leaves_output_untouched(self):
        result, _ = await _run_with_model(
            _mock_agent(), ["just prose"], None
        )
        assert result["status"] == "completed"
        assert result["output"] == "just prose"
        assert result["contract_valid"] is None


class TestDelegatedContract:
    @pytest.mark.asyncio
    async def test_delegated_run_enforces_same_contract(self):
        parent = _mock_agent()
        child = _mock_agent()
        child.name = "Child Agent"
        child.organization_id = parent.organization_id
        parent.delegated_agents = [child]

        session = AsyncMock()
        stored_rows: list = []
        session.add = MagicMock(side_effect=lambda row: stored_rows.append(row))
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        async def _get_row(model, row_id):
            return stored_rows[0] if stored_rows else None

        session.get = AsyncMock(side_effect=_get_row)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = child
        session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        factory = MagicMock(return_value=mock_ctx)

        model, _ = _scripted_model(['{"ticket_id": "seven"}'])
        executor = AutonomousAgentExecutor(factory)
        with (
            patch(
                "src.services.execution.autonomous_agent_executor.get_llm_configs",
                new_callable=AsyncMock,
                return_value=[LLMConfig(
                    provider="openai", model="test-model", api_key="k"
                )],
            ),
            patch(
                "src.services.agent_runtime.model_factory.create_agent_model",
                return_value=model,
            ),
            patch(
                "src.services.execution.autonomous_agent_executor.resolve_agent_tools",
                new_callable=AsyncMock,
                return_value=([], {}),
            ),
        ):
            outcome = await executor.run_delegation(
                parent_agent=parent,
                tool_call=ToolCallRequest(
                    id="tc1", name="delegate_to_child_agent", arguments={"task": "go"}
                ),
                parent_run_id=str(uuid4()),
                output_schema=SCHEMA,
            )
        assert outcome.status == "contract_failed"
        assert outcome.error is not None
        assert "contract" in outcome.error
        added = [call.args[0] for call in session.add.call_args_list]
        child_rows = [row for row in added if hasattr(row, "output_schema")]
        assert child_rows
        assert child_rows[0].output_schema == SCHEMA
        assert child_rows[0].contract_valid is False


class TestContractResponseFields:
    def test_response_carries_contract_outcome_without_breaking_clients(self):
        assert "output_schema" in AgentRunResponse.model_fields
        assert "contract_valid" in AgentRunResponse.model_fields
        assert "contract_errors" in AgentRunResponse.model_fields
        assert AgentRunResponse.model_fields["contract_valid"].default is None
        assert AgentRunResponse.model_fields["contract_errors"].default is None


MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "alembic"
    / "versions"
    / "20260918_agent_output_contract.py"
)


def test_contract_migration_chain():
    assert MIGRATION_PATH.exists()
    spec = importlib.util.spec_from_file_location(
        "agent_output_contract_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "20260918_agent_output_contract"
    assert module.down_revision == "20260918_durable_agent_runtime"
