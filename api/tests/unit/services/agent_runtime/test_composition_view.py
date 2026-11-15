"""Pure model-visible composition snapshot contracts."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from src.services.agent_runtime.composition_view import (
    AgentCompositionSnapshot,
    AgentCompositionView,
    DeferredAgentSnapshot,
    ToolBinding,
    _capability_id,
    _qualified_tool_name,
)
from src.services.llm import ToolDefinition


class ViewError(Exception):
    pass


def test_snapshot_json_round_trip_has_only_model_visible_data():
    root_id, child_id = uuid4(), uuid4()
    definition = ToolDefinition(
        name="search_case",
        description="Find a case.",
        parameters={"type": "object", "properties": {"case": {"type": "string"}}},
    )
    capability_id = _capability_id(child_id)
    qualified_name = _qualified_tool_name(child_id, definition.name)
    snapshot = AgentCompositionSnapshot.from_parts(
        root_id=root_id,
        instructions="Keep ownership.",
        entry_definitions=(),
        bindings={
            qualified_name: ToolBinding(
                name=qualified_name,
                owner_id=child_id,
                original_name=definition.name,
                path=(root_id, child_id),
                capability_id=capability_id,
                parameters=definition.parameters,
            )
        },
        deferred=(
            DeferredAgentSnapshot(
                agent_id=child_id,
                name="Case Specialist",
                description="Case work.",
                system_prompt="Verify evidence.",
                path=(root_id, child_id),
                definitions=(definition,),
                capability_id=capability_id,
            ),
        ),
        isolated_delegates={},
    )

    encoded = snapshot.to_json()
    restored = AgentCompositionSnapshot.from_json(encoded)

    assert restored == snapshot
    assert "caller" not in encoded
    assert "session_factory" not in encoded
    assert encoded["deferred"][0]["agent_id"] == str(child_id)


@pytest.mark.asyncio
async def test_view_reauthorizes_a_loaded_capability_before_next_model_request():
    root_id, child_id = uuid4(), uuid4()
    capability_id = _capability_id(child_id)
    snapshot = AgentCompositionSnapshot.from_parts(
        root_id=root_id,
        instructions="Keep ownership.",
        entry_definitions=(),
        bindings={},
        deferred=(
            DeferredAgentSnapshot(
                agent_id=child_id,
                name="Specialist",
                description=None,
                system_prompt="Private instructions.",
                path=(root_id, child_id),
                definitions=(),
                capability_id=capability_id,
            ),
        ),
        isolated_delegates={},
    )
    authorize = AsyncMock(side_effect=[None, ViewError("Specialist revoked")])
    view = AgentCompositionView(
        snapshot,
        authorize_capability_load=authorize,
        error_type=ViewError,
    )
    requests = 0

    def model(_, __):
        nonlocal requests
        requests += 1
        return ModelResponse(parts=[ToolCallPart("load_capability", {"id": capability_id}, "load")])

    with pytest.raises(ViewError, match="Specialist revoked"):
        await Agent(FunctionModel(model), capabilities=view.capabilities(AsyncMock())).run("inspect")

    assert requests == 1
    assert authorize.await_count == 2
