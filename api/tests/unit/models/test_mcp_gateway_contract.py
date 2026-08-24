"""Contracts for selecting an Agent or maintained Builder session over MCP."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts.mcp import (
    MCPGatewayCapabilitySearchRequest,
    MCPGatewayBuilderExecuteResponse,
    MCPGatewayExecuteRequest,
    MCPGatewayExecuteResponse,
)


def test_capability_search_accepts_query_or_exactly_one_subject() -> None:
    assert MCPGatewayCapabilitySearchRequest(query="create an app").query
    assert MCPGatewayCapabilitySearchRequest(agent_id=str(uuid4())).agent_id
    assert MCPGatewayCapabilitySearchRequest(
        builder_session_id=str(uuid4())
    ).builder_session_id


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"agent_id": str(uuid4()), "builder_session_id": str(uuid4())},
        {"tool_ref": str(uuid4())},
    ],
)
def test_capability_search_rejects_ambiguous_or_unscoped_hydration(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        MCPGatewayCapabilitySearchRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "agent_id": str(uuid4()),
            "arguments": {},
        },
        {
            "builder_session_id": str(uuid4()),
            "arguments": {},
        },
    ],
)
def test_gateway_execution_body_rejects_selector_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        MCPGatewayExecuteRequest.model_validate(payload)


def test_gateway_execution_body_accepts_existing_path_bound_agent_shape() -> None:
    request = MCPGatewayExecuteRequest.model_validate(
        {
            "arguments": {"ticket_id": 42},
            "async": False,
        }
    )

    assert request.arguments == {"ticket_id": 42}
    assert request.async_ is False


def test_gateway_execution_body_accepts_builder_path_shape() -> None:
    request = MCPGatewayExecuteRequest(
        arguments={"ticket_id": 42},
    )

    assert request.arguments == {"ticket_id": 42}
    assert request.async_ is None


def test_agent_gateway_execution_response_schema_preserves_required_agent_id() -> None:
    response = MCPGatewayExecuteResponse(
        agent_id=str(uuid4()),
        agent_name="Operations",
        tool_ref=str(uuid4()),
        tool_name="lookup_ticket",
        source="workflow",
        duration_ms=12,
        result={"ok": True},
    )

    assert response.async_ is False
    assert response.execution_id is None
    assert response.result == {"ok": True}

    with pytest.raises(ValidationError):
        MCPGatewayExecuteResponse(
            agent_name="Operations",
            tool_ref=str(uuid4()),
            tool_name="lookup_ticket",
            source="workflow",
            duration_ms=12,
            result={"ok": True},
        )


def test_builder_gateway_execution_response_is_distinct_and_additive() -> None:
    builder_session_id = str(uuid4())
    response = MCPGatewayBuilderExecuteResponse(
        builder_session_id=builder_session_id,
        agent_name="Bifrost Build",
        tool_ref=str(uuid4()),
        tool_name="write_file",
        source="builder_workspace",
        duration_ms=12,
        result={"ok": True},
    )

    assert response.builder_session_id == builder_session_id
    assert response.async_ is False
    assert response.execution_id is None
    assert response.result == {"ok": True}
