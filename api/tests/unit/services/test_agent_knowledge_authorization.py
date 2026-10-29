"""Cached grounding remains bound to the Agent's current sources and scope."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.agent_executor import AgentExecutor
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import ToolCallRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("executor_type", [AgentExecutor, AutonomousAgentExecutor])
@pytest.mark.parametrize("change", ["namespace", "organization", "global_document"])
async def test_cached_grounding_uses_current_agent_binding(executor_type, change):
    organization_id = uuid4()
    agent = SimpleNamespace(organization_id=organization_id, knowledge_sources=["current"])
    executor = executor_type(None)
    executor._knowledge_search_budget.cache_document("document", {
        "content": "Contoso knowledge",
        "namespace": "removed" if change == "namespace" else "current",
        "organization_id": str(uuid4()) if change == "organization" else None,
        "score": 0.9,
        "key": "example",
        "metadata": {},
    })

    result = await executor._execute_knowledge_search(ToolCallRequest(
        id="followup",
        name="search_knowledge",
        arguments={"doc_id": "document", "include_full_content": True},
    ), agent)
    payload = json.loads(result) if isinstance(result, str) else result.result

    assert payload["count"] == (1 if change == "global_document" else 0)
    if change != "global_document":
        assert "Contoso knowledge" not in json.dumps(payload)
