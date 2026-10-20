"""MCP discovery shares original-user resource policy and configuration gates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.contracts.agents import ToolInfo
from src.models.enums import AgentAccessLevel
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller
from src.services.authorization.explain import RunUser
from src.services.authorization.runtime import RuntimeAuthority
from src.services.mcp_server.config_service import MCPConfig
from src.services.mcp_server.tool_access import MCPToolAccessService


def _caller(*, configuration=False, actor_admin=False):
    original_id, actor_id, home = uuid4(), uuid4(), uuid4()
    roles = () if not configuration else (
        RoleGrant(uuid4(), frozenset({"mcp.read"}), (Boundary(BoundaryKind.PLATFORM),)),
    )
    ctx = AuthorizationContext(
        user_id=original_id, home_organization_id=home, base_role_id=USER_ROLE_ID,
        is_external=False, base_permissions=USER_BASE_PERMISSIONS, role_grants=roles,
    )
    principal = UserPrincipal(
        user_id=actor_id, email="runner@contoso.example", organization_id=home,
        is_superuser=actor_admin,
    )
    principal.runtime_authority = RuntimeAuthority(
        kind="agent_run", credential_id=uuid4(), run_user=RunUser(original_id, ctx, None, True),
        powers=None, acting_user_id=actor_id, organization_id=home, workflow_id=None,
        started_by_user_id=original_id, root_execution_id=None,
    )
    return Caller(principal, ctx)


def _agent(caller, *, tools=(), namespaces=(), workflows=(), organization_id=...):
    return SimpleNamespace(
        id=uuid4(), name="Contoso Agent", access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=caller.ctx.home_organization_id if organization_id is ... else organization_id,
        owner_user_id=None, is_active=True, system_tools=list(tools),
        knowledge_sources=list(namespaces), tools=list(workflows),
    )


def _session(agents):
    result = MagicMock()
    result.scalars.return_value.unique.return_value.all.return_value = agents
    result.scalars.return_value.unique.return_value.first.return_value = agents[0] if agents else None
    return AsyncMock(execute=AsyncMock(return_value=result))


@pytest.fixture(autouse=True)
def _config():
    with patch("src.services.mcp_server.tool_access.MCPConfigService") as service:
        service.return_value.get_config = AsyncMock(return_value=MCPConfig())
        yield service.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize("namespaces,explicit,expected", [((), False, 0), (("docs",), False, 1), (("docs",), True, 1)])
async def test_knowledge_tool_matches_native_agent_injection(namespaces, explicit, expected):
    caller = _caller()
    agent = _agent(caller, tools=["search_knowledge"] if explicit else [], namespaces=namespaces)
    service = MCPToolAccessService(_session([agent]))
    result = await service.get_tools_for_agent(agent.id, caller)
    assert result is not None
    assert sum(tool.id == "search_knowledge" for tool in result.tools) == expected


@pytest.mark.asyncio
async def test_bound_workflow_discovery_checks_reach_and_deduplicates_without_direct_role_acl():
    caller = _caller()
    visible = SimpleNamespace(
        id=uuid4(), name="Contoso Lookup", tool_description="Look up an item", description="",
        category="operations", organization_id=caller.ctx.home_organization_id,
        is_active=True, type="tool", access_level="role_based", roles=[],
    )
    foreign = SimpleNamespace(**{**vars(visible), "id": uuid4(), "organization_id": uuid4()})
    inactive = SimpleNamespace(**{**vars(visible), "id": uuid4(), "is_active": False})
    agents = [_agent(caller, workflows=[visible, foreign, inactive]), _agent(caller, workflows=[visible])]
    result = await MCPToolAccessService(_session(agents)).get_accessible_tools(caller)
    assert [(tool.name, tool.description) for tool in result.tools] == [(visible.name, visible.tool_description)]


@pytest.mark.asyncio
async def test_actor_admin_fact_does_not_expose_a_foreign_agent():
    caller = _caller(actor_admin=True)
    agent = _agent(caller, tools=["bifrost_workflow_list"], organization_id=uuid4())
    session = _session([agent])
    service = MCPToolAccessService(session)
    assert await service.get_tools_for_agent(agent.id, caller) is None
    query = session.execute.await_args.args[0].compile()
    assert caller.ctx.home_organization_id in [item for value in query.params.values() if isinstance(value, list) for item in value]


@pytest.mark.asyncio
async def test_shared_global_agent_remains_visible_at_home():
    caller = _caller()
    agent = _agent(caller, tools=["bifrost_workflow_list"], organization_id=None)
    service = MCPToolAccessService(_session([agent]))
    result = await service.get_tools_for_agent(agent.id, caller)
    assert result is not None
    assert [tool.id for tool in result.tools] == ["bifrost_workflow_list"]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_role", [True, False])
async def test_role_acl_uses_original_user_id_instead_of_actor_roles(has_role):
    caller = _caller(actor_admin=True)
    agent = _agent(caller, tools=["bifrost_workflow_list"])
    agent.access_level = AgentAccessLevel.ROLE_BASED
    role_id = uuid4()
    session = _session([agent])
    role_result = MagicMock()
    role_result.scalars.return_value.all.return_value = [role_id]
    acl_result = MagicMock()
    acl_result.scalar_one_or_none.return_value = agent.id if has_role else None
    session.execute.side_effect = [session.execute.return_value, role_result, acl_result]

    result = await MCPToolAccessService(session).get_tools_for_agent(agent.id, caller)

    assert (result is not None) is has_role
    query = session.execute.await_args_list[1].args[0].compile()
    assert caller.ctx.user_id in query.params.values()
    assert caller.principal.user_id not in query.params.values()


@pytest.mark.asyncio
async def test_configuration_inventory_needs_named_global_permission():
    caller = _caller(actor_admin=True)
    session = _session([])
    service = MCPToolAccessService(session)
    with pytest.raises(HTTPException) as error:
        await service.get_accessible_tools(caller, for_configuration=True)
    assert error.value.status_code == 403
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("for_configuration,visible", [(True, True), (False, False)])
async def test_configuration_inventory_does_not_change_runtime_filtering(_config, for_configuration, visible):
    caller = _caller(configuration=True)
    agent = _agent(caller, tools=["bifrost_workflow_execute"])
    _config.get_config.return_value = MCPConfig(blocked_tool_ids=["bifrost_workflow_execute"])
    service = MCPToolAccessService(_session([agent]))
    result = await service.get_accessible_tools(caller, for_configuration=for_configuration)
    assert bool(result.tools) is visible


@pytest.mark.asyncio
async def test_system_tools_are_deduplicated_and_unknown_metadata_is_preserved():
    caller = _caller()
    agents = [_agent(caller, tools=["contoso_lookup"]), _agent(caller, tools=["contoso_lookup"])]
    service = MCPToolAccessService(_session(agents))
    result = await service.get_accessible_tools(caller)
    assert [(tool.id, tool.name) for tool in result.tools] == [("contoso_lookup", "Contoso Lookup")]


@pytest.mark.parametrize("allowed,blocked,expected", [
    (None, [], ["a", "b"]), (["a"], [], ["a"]), (["a", "b"], ["a"], ["b"]),
])
def test_config_allow_and_block_lists(allowed, blocked, expected):
    tools = [ToolInfo(id=value, name=value, description="", type="system") for value in ["a", "b"]]
    result = MCPToolAccessService(None)._apply_config_filters(tools, MCPConfig(allowed_tool_ids=allowed, blocked_tool_ids=blocked))
    assert [tool.id for tool in result] == expected


@pytest.mark.asyncio
async def test_readable_agent_requires_named_execute_permission_to_call_tools():
    caller = _caller()
    caller = Caller(caller.principal, AuthorizationContext(
        user_id=caller.ctx.user_id, home_organization_id=caller.ctx.home_organization_id,
        base_role_id=USER_ROLE_ID, is_external=False,
        base_permissions=frozenset({"agents.readbasic"}),
    ))
    caller.principal.runtime_authority = None
    agent = _agent(caller, tools=["bifrost_workflow_list"])
    service = MCPToolAccessService(_session([agent]))
    assert await service.get_tools_for_agent(agent.id, caller) is not None
    with pytest.raises(HTTPException) as error:
        await service.get_tools_for_agent(agent.id, caller, for_execution=True)
    assert error.value.status_code == 403
