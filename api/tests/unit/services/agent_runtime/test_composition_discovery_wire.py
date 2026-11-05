"""Provider-wire proof of instruction loading followed by individual discovery."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx2
import pytest
from pydantic_ai import Agent

from src.services.agent_runtime.composition import (
    AgentComposition,
    ToolBinding,
    _DeferredAgent,
    _capability_id,
    _qualified_tool_name,
)
from src.services.agent_runtime.model_factory import create_agent_model
from src.services.llm import ToolDefinition
from src.services.llm.base import LLMConfig


@pytest.mark.asyncio
async def test_openrouter_receives_only_the_individually_discovered_tool_schema():
    root_id, source_id = uuid4(), uuid4()
    capability_id = _capability_id(source_id)
    reset = ToolDefinition(
        name="reset_password",
        description="Reset a password for an approved user.",
        parameters={
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
        },
    )
    unused = ToolDefinition(
        name="inventory_report",
        description="Read unrelated inventory.",
        parameters={
            "type": "object",
            "description": "UNUSED_SCHEMA_SENTINEL" + "x" * 100_000,
            "properties": {},
        },
    )
    qualified = _qualified_tool_name(source_id, reset.name)
    unused_qualified = _qualified_tool_name(source_id, unused.name)
    source = SimpleNamespace(
        id=source_id,
        name="Accounts Specialist",
        description="Account administration instructions.",
        system_prompt="Verify the user identity before changing credentials.",
    )
    composition = AgentComposition(
        root_id=root_id,
        caller=SimpleNamespace(),
        session_factory=lambda: None,
        caller_user_id=None,
        instructions="Keep ownership of the task.",
        entry_definitions=(),
        bindings={
            _qualified_tool_name(source_id, definition.name): ToolBinding(
                name=_qualified_tool_name(source_id, definition.name),
                owner_id=source_id,
                original_name=definition.name,
                path=(root_id, source_id),
                capability_id=capability_id,
                parameters=definition.parameters,
            )
            for definition in (reset, unused)
        },
        deferred=(
            _DeferredAgent(source, (root_id, source_id), (reset, unused), capability_id),
        ),
        isolated_delegates={},
    )
    composition.authorize_capability_load = AsyncMock()
    execute = AsyncMock(return_value="reset receipt")
    bodies: list[dict] = []
    actions = [
        ("load_capability", {"id": capability_id}),
        ("search_tools", {"queries": ["reset password"]}),
        (qualified, {"user_id": "approved-user"}),
    ]

    async def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        index = len(bodies) - 1
        message: dict = {"role": "assistant", "content": "done"}
        if index < len(actions):
            name, arguments = actions[index]
            message.update(
                content=None,
                tool_calls=[{
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }],
            )
        return httpx2.Response(200, json={
            "id": f"completion-{index}",
            "object": "chat.completion",
            "created": 1,
            "model": "deepseek/deepseek-v4.1-flash",
            "provider": "Synthetic",
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if index < len(actions) else "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with patch(
            "src.services.agent_runtime.model_factory.get_ai_retry_http_client",
            return_value=client,
        ):
            model = create_agent_model(LLMConfig(
                provider="openai",
                model="deepseek/deepseek-v4.1-flash",
                endpoint="https://openrouter.ai/api/v1",
                api_key="synthetic-never-sent-to-network",
            ))
            result = await Agent(
                model,
                instructions=composition.instructions,
                capabilities=composition.capabilities(execute),
            ).run("Use the account instructions to reset the approved user's password.")

    assert result.output == "done"
    assert len(bodies) == 4
    visible = [
        {tool["function"]["name"] for tool in body.get("tools", [])}
        for body in bodies
    ]
    assert qualified not in visible[0]
    assert qualified not in visible[1], "Loading instructions must not load the toolset"
    assert "search_tools" in visible[1]
    assert qualified in visible[2]
    assert all(unused_qualified not in names for names in visible)
    assert all("UNUSED_SCHEMA_SENTINEL" not in json.dumps(body) for body in bodies)
    execute.assert_awaited_once_with(qualified, {"user_id": "approved-user"}, "call-2")
