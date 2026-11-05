from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.llm import ToolDefinition


def _agent(**overrides):
    values = {
        "id": uuid4(),
        "system_tools": ["list_organizations"],
        "knowledge_sources": ["operations"],
        "delegated_agents": [],
        "mcp_connections": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _definition(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} capability",
        parameters={"type": "object", "properties": {}},
    )


def test_resolve_capability_tools_classifies_current_agent_grants():
    from src.services.capabilities.catalog import resolve_capability_tools

    workflow_id = uuid4()
    delegate = SimpleNamespace(
        id=uuid4(), name="Billing Specialist", is_active=True
    )
    connection_id = uuid4()
    agent = _agent(
        delegated_agents=[delegate],
        mcp_connections=[SimpleNamespace(id=connection_id)],
    )
    mcp_name = f"mcp__{connection_id}__lookup_ticket"

    tools = resolve_capability_tools(
        agent,
        [
            _definition("list_organizations"),
            _definition("search_knowledge"),
            _definition("delegate_to_billing_specialist"),
            _definition(mcp_name),
            _definition("run_report"),
        ],
        {"run_report": workflow_id, mcp_name: connection_id},
    )

    assert [(tool.definition.name, tool.source) for tool in tools] == [
        ("list_organizations", "system"),
        ("search_knowledge", "knowledge"),
        ("delegate_to_billing_specialist", "delegation"),
        (mcp_name, "external_mcp"),
        ("run_report", "workflow"),
    ]
    assert tools[3].remote_tool_name == "lookup_ticket"


def test_workflow_capability_ref_is_stable_when_display_name_changes():
    from src.services.capabilities.catalog import resolve_capability_tools

    agent = _agent()
    workflow_id = uuid4()

    old = resolve_capability_tools(
        agent, [_definition("old_name")], {"old_name": workflow_id}
    )
    new = resolve_capability_tools(
        agent, [_definition("new_name")], {"new_name": workflow_id}
    )

    assert old[0].tool_ref == new[0].tool_ref
    assert old[0].source_identity == f"workflow:{workflow_id}"


def test_capability_ref_lookup_is_exact():
    from src.services.capabilities.catalog import (
        find_capability_by_ref,
        resolve_capability_tools,
    )

    tools = resolve_capability_tools(
        _agent(), [_definition("list_organizations")], {}
    )

    assert find_capability_by_ref(tools, tools[0].tool_ref) is tools[0]
    assert find_capability_by_ref(tools, "list_organizations") is None


def test_route_does_not_guess_an_ungrafted_delegation():
    from src.services.capabilities.catalog import resolve_capability_route

    agent = _agent(
        delegated_agents=[SimpleNamespace(id=uuid4(), name="Other", is_active=True)]
    )

    assert resolve_capability_route(agent, "delegate_to_billing_specialist", {}) is None


def test_route_does_not_guess_an_unhydrated_mcp_tool():
    from src.services.capabilities.catalog import resolve_capability_route

    connection_id = uuid4()
    agent = _agent(mcp_connections=[SimpleNamespace(id=connection_id)])
    name = f"mcp__{connection_id}__not_in_the_live_catalog"

    assert resolve_capability_route(agent, name, {}) is None


@pytest.mark.parametrize(
    ("knowledge_sources", "system_tools"),
    [
        (["operations"], []),
        ([], ["search_knowledge"]),
    ],
)
def test_knowledge_route_requires_a_real_grant_and_keeps_its_identity(
    knowledge_sources, system_tools
):
    from src.services.capabilities.catalog import (
        capability_ref,
        resolve_capability_route,
    )

    agent = _agent(
        knowledge_sources=knowledge_sources,
        system_tools=system_tools,
    )

    route = resolve_capability_route(agent, "search_knowledge", {})

    assert route is not None
    assert route.source == "knowledge"
    assert route.source_identity == "knowledge:search_knowledge"
    alternate = resolve_capability_route(
        _agent(
            id=agent.id,
            knowledge_sources=["operations"],
            system_tools=[],
        ),
        "search_knowledge",
        {},
    )
    assert alternate is not None
    assert capability_ref(agent.id, route) == capability_ref(agent.id, alternate)


def test_knowledge_route_does_not_accept_an_ungranted_name():
    from src.services.capabilities.catalog import resolve_capability_route

    assert resolve_capability_route(
        _agent(knowledge_sources=[], system_tools=[]), "search_knowledge", {}
    ) is None


def test_invalid_schema_raises_before_a_dispatcher_can_run():
    from src.services.capabilities.catalog import (
        CapabilityArgumentError,
        require_valid_capability_arguments,
    )

    with pytest.raises(CapabilityArgumentError) as exc_info:
        require_valid_capability_arguments({"type": "not-a-type"}, {})

    assert exc_info.value.schema_error
    assert exc_info.value.issues == []


def test_capability_ref_preserves_existing_mcp_reference_contract():
    from uuid import UUID
    from src.services.capabilities.catalog import CapabilityRoute, capability_ref

    route = CapabilityRoute(
        source="workflow",
        source_identity="workflow:22222222-2222-2222-2222-222222222222",
        source_id=UUID("22222222-2222-2222-2222-222222222222"),
    )
    assert capability_ref(UUID("11111111-1111-1111-1111-111111111111"), route) == (
        "b4829af7-78c5-5df4-b869-ee26db4992a3"
    )
