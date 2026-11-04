"""Regression coverage for framework tool-result persistence."""

from typing import Literal

import pytest
from pydantic_ai.messages import ToolReturnPart

from src.models.contracts.agents import ToolCall
from src.services.agent_executor import _tool_result_from_framework_part


def test_framework_tool_result_persists_success_outcome_as_result() -> None:
    """Successful framework results remain successful Bifrost receipts."""
    tool_call = ToolCall(id="persisted-call", name="run_code", arguments={})

    persisted = _tool_result_from_framework_part(
        tool_call,
        ToolReturnPart(
            tool_name="run_code",
            tool_call_id="framework-success",
            content={"stdout": "done"},
        ),
    )

    assert persisted.model_dump() == {
        "tool_call_id": "persisted-call",
        "tool_name": "run_code",
        "result": {"stdout": "done"},
        "error": None,
        "duration_ms": None,
        "error_type": None,
        "metadata": None,
    }


@pytest.mark.parametrize(
    "outcome", ["failed", "denied", "interrupted", "retried"]
)
def test_framework_tool_result_persists_non_success_outcome_as_error(
    outcome: Literal["failed", "denied", "interrupted", "retried"],
) -> None:
    """Framework outcomes that did not succeed cannot be persisted as results."""
    tool_call = ToolCall(id="persisted-call", name="run_code", arguments={})

    persisted = _tool_result_from_framework_part(
        tool_call,
        ToolReturnPart(
            tool_name="run_code",
            tool_call_id="framework-retry",
            content="The framework did not produce a successful result.",
            outcome=outcome,
        ),
    )

    assert persisted.model_dump() == {
        "tool_call_id": "persisted-call",
        "tool_name": "run_code",
        "result": None,
        "error": "The framework did not produce a successful result.",
        "duration_ms": None,
        "error_type": None,
        "metadata": None,
    }
